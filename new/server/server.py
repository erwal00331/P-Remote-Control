"""
远程控制服务端 + 图片拼接服务
整合版：合并远程控制与图片拼接功能，增加图片获取能力
"""
import struct
import json
import socket
import threading
import time
from datetime import datetime
import asyncio
import logging
import io
import os
import glob

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from PIL import Image, ImageDraw, ImageFont

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ================= 配置区域 =================
TCP_PORT = int(os.getenv("SERVER_PORT", 8100))
WEB_PORT = int(os.getenv("WEB_PORT", 8000))
DEVICE_TIMEOUT = 40

# Load config dynamically
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "server_config.json")

# 图片拼接相关路径
MAP_PATH = os.path.join(DATA_DIR, "map.jpg")
IMAGES_DIR = os.path.join(DATA_DIR, "images")

# 确保目录存在
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

SECRET_KEY = {}
AUTHORITY_DICT = {}

def get_key_groups(key: str) -> list:
    """获取 key 对应的组列表"""
    info = SECRET_KEY.get(key)
    if not info:
        return []
    return info.get("groups", [])

def get_key_authority(key: str) -> str:
    """获取 key 对应的权限"""
    info = SECRET_KEY.get(key)
    if not info:
        return "unknown"
    return info.get("authority", "unknown")

# Config Watcher
def load_config():
    """Load config from file and update globals"""
    global SECRET_KEY, AUTHORITY_DICT
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            _cfg = json.load(f)
            SECRET_KEY.clear()
            SECRET_KEY.update(_cfg.get("secret_keys", {}))
            AUTHORITY_DICT.clear()
            AUTHORITY_DICT.update(_cfg.get("authority_dict", {}))
        
        # Inject ADMIN_KEY from environment
        if os.getenv("ADMIN_KEY"):
            SECRET_KEY[os.getenv("ADMIN_KEY")] = {"authority": "admin"}
            
        logger.info(f"Loaded configuration from {CONFIG_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to load configuration from {CONFIG_PATH}: {e}")
        return False

# Initial load
load_config()

def config_watcher_loop():
    """Watch config file for changes"""
    last_mtime = 0
    try:
        if os.path.exists(CONFIG_PATH):
            last_mtime = os.path.getmtime(CONFIG_PATH)
    except Exception:
        pass
        
    while running:
        time.sleep(5)
        try:
            if os.path.exists(CONFIG_PATH):
                mtime = os.path.getmtime(CONFIG_PATH)
                if mtime > last_mtime:
                    logger.info("Config file changed, reloading...")
                    if load_config():
                        last_mtime = mtime
        except Exception as e:
            logger.error(f"Config watcher error: {e}")


# Inject ADMIN_KEY from environment
if os.getenv("ADMIN_KEY"):
    SECRET_KEY[os.getenv("ADMIN_KEY")] = {"authority": "admin"}

# ================= 全局状态 =================
running = True
clients = {}  # {device_name: connection}
ips = {}      # {device_name: ip}
main_loop = None
devices_lock = threading.RLock()
registered_devices = {}  # {device_name: device_info}

# 内存保护限制
MAX_CLIENTS = 100
MAX_DEVICES = 200
MAX_HEADER_SIZE = 1024 * 1024  # 1MB
MAX_DATA_SIZE = 50 * 1024 * 1024  # 50MB

# ================= 图片拼接全局状态 =================
IMG_W = 1920  # 每张图片固定宽度
IMG_H = 1080  # 每张图片固定高度

# 画布锁，保证并发安全
canvas_lock = threading.Lock()

# 追踪当前画布的边界
canvas_max_x = 0  # 画布宽度 = max_x + IMG_W
canvas_max_y = 0  # 画布高度 = max_y + IMG_H

# 图片保存计数器
image_counter = 0
image_counter_lock = threading.Lock()


# ================= 核心工具函数 =================

def pack_data(header: dict, binary_data: bytes = b"") -> bytes:
    """打包数据：4字节头长度 + JSON头 + 二进制体"""
    header["date_len"] = len(binary_data)
    json_bytes = json.dumps(header, ensure_ascii=False).encode("utf-8")
    return struct.pack('!I', len(json_bytes)) + json_bytes + binary_data


def recvall(sock: socket.socket, count: int) -> bytes:
    """TCP 专用：确保读取指定长度的数据"""
    buf = bytearray(count)
    view = memoryview(buf)
    pos = 0
    while pos < count:
        try:
            nbytes = sock.recv_into(view[pos:])
            if not nbytes:
                return None
            pos += nbytes
        except socket.timeout:
            continue
        except Exception:
            return None
    return bytes(buf)


def send_to_connection(conn, packet: bytes) -> bool:
    """智能发送：自动识别 TCP Socket 或 WebSocket"""
    try:
        if isinstance(conn, socket.socket):
            conn.sendall(packet)
        elif isinstance(conn, WebSocket):
            if main_loop and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(conn.send_bytes(packet), main_loop)
            else:
                logger.error("Main loop not ready for WS send")
                return False
        return True
    except Exception as e:
        logger.error(f"Send Error: {e}")
        return False


# ================= 图片拼接核心函数 =================

def stitch_image(image_data: bytes, x: int, y: int):
    """
    将收到的图片拼接到 map.jpg 上
    - image_data: JPG 二进制数据
    - x, y: 图片左上角坐标
    """
    global canvas_max_x, canvas_max_y

    # 自动创建目录
    os.makedirs(os.path.dirname(MAP_PATH), exist_ok=True)

    with canvas_lock:
        # 解码收到的图片
        try:
            new_img = Image.open(io.BytesIO(image_data)).convert("RGB")
        except Exception as e:
            logger.error(f"无法解码图片: {e}")
            return False

        # 在图片右下角标注上传时间（绿色高亮）
        try:
            now = datetime.now()
            time_text = f"{now.month:02d}/{now.day:02d}/{now.hour:02d}/{now.minute:02d}"
            draw = ImageDraw.Draw(new_img)

            # 尝试加载字体，失败则使用默认字体
            font_size = 28
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except Exception:
                try:
                    font = ImageFont.truetype("msyh.ttc", font_size)  # 微软雅黑
                except Exception:
                    font = ImageFont.load_default()

            # 计算文字尺寸和位置（右下角）
            bbox = draw.textbbox((0, 0), time_text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            img_w, img_h = new_img.size
            padding = 10
            text_x = img_w - text_w - padding
            text_y = img_h - text_h - padding

            # 绘制半透明黑色背景提高可读性
            bg_margin = 4
            draw.rectangle(
                [text_x - bg_margin, text_y - bg_margin,
                 text_x + text_w + bg_margin, text_y + text_h + bg_margin],
                fill=(0, 0, 0)
            )

            # 绘制绿色高亮时间文字
            draw.text((text_x, text_y), time_text, fill=(0, 255, 0), font=font)
            logger.info(f"已在图片右下角标注上传时间: {time_text}")
        except Exception as e:
            logger.warning(f"标注上传时间失败，继续拼接: {e}")

        # 更新最大坐标
        new_max_x = max(canvas_max_x, x)
        new_max_y = max(canvas_max_y, y)

        # 计算画布尺寸
        canvas_w = new_max_x + IMG_W
        canvas_h = new_max_y + IMG_H

        # 加载已有的 map.jpg 或创建黑色画布
        if os.path.exists(MAP_PATH):
            try:
                existing = Image.open(MAP_PATH).convert("RGB")
            except Exception as e:
                logger.warning(f"无法读取已有 map.jpg，将创建新画布: {e}")
                existing = None
        else:
            existing = None

        # 创建新画布（纯黑色）
        canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))

        # 粘贴已有图像（如果存在）
        if existing is not None:
            canvas.paste(existing, (0, 0))

        # 粘贴新图片（覆盖重合区域）
        canvas.paste(new_img, (x, y))

        # 保存
        canvas.save(MAP_PATH, "JPEG", quality=95)

        # 更新全局状态
        canvas_max_x = new_max_x
        canvas_max_y = new_max_y

        logger.info(
            f"拼接成功: 坐标=({x}, {y}), "
            f"画布尺寸={canvas_w}x{canvas_h}"
        )
        return True


def save_individual_image(image_data: bytes, x: int, y: int, device_name: str = "unknown"):
    """
    单独保存接收到的每张图片到 images 目录
    文件名格式: {timestamp}_{device}_{x}_{y}.jpg
    """
    global image_counter
    with image_counter_lock:
        image_counter += 1
        count = image_counter
    
    # 自动创建目录
    os.makedirs(IMAGES_DIR, exist_ok=True)

    try:
        timestamp = int(time.time() * 1000)
        safe_device = device_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        filename = f"{timestamp}_{safe_device}_{x}_{y}_{count}.jpg"
        filepath = os.path.join(IMAGES_DIR, filename)
        
        with open(filepath, 'wb') as f:
            f.write(image_data)
        
        logger.info(f"图片已保存: {filename}")
        return filename
    except Exception as e:
        logger.error(f"保存图片失败: {e}")
        return None


def get_saved_image_list(limit: int = 100, offset: int = 0):
    """获取已保存图片的列表"""
    try:
        files = []
        for ext in ['*.jpg', '*.jpeg', '*.png']:
            files.extend(glob.glob(os.path.join(IMAGES_DIR, ext)))
        
        # 按修改时间倒序排列
        files.sort(key=os.path.getmtime, reverse=True)
        
        total = len(files)
        paged_files = files[offset:offset + limit]
        
        result = []
        for fp in paged_files:
            fname = os.path.basename(fp)
            fsize = os.path.getsize(fp)
            mtime = os.path.getmtime(fp)
            result.append({
                "filename": fname,
                "size": fsize,
                "modified": mtime,
                "path": fp
            })
        
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "images": result
        }
    except Exception as e:
        logger.error(f"获取图片列表失败: {e}")
        return {"total": 0, "offset": offset, "limit": limit, "images": []}


# ================= 业务逻辑处理 =================

def handle_request(header: dict, binary_data: bytes = b""):
    """
    统一处理所有请求
    返回格式: (Response_Dict, Status_Code, Binary_Data)
    """
    action = header.get("action")
    params = header.get("params", {})
    requester = header.get("requester", "unknown")
    key = header.get("key")
    
    if not action:
        return {"status": "error", "msg": "Missing action"}, 400, b""
    
    # 权限验证
    auth_info = SECRET_KEY.get(key)
    authority = auth_info["authority"] if auth_info else "unknown"
    
    if authority == "unknown" and action not in ["register", "forward", "list", "read", "stitch", "get_map", "get_saved_images", "get_image"]:
        return {"status": "error", "msg": "Invalid Key or Permission denied"}, 403, b""
    
    required_auth = AUTHORITY_DICT.get(action, [])
    if action in AUTHORITY_DICT and authority not in required_auth:
        return {"status": "error", "msg": "Permission denied"}, 403, b""
    
    # 用于存储需要在锁外执行的 I/O 操作
    target_conn = None
    packet = None
    targets_to_broadcast = []
    inner_params_for_broadcast = {}
    
    with devices_lock:
        if action == "register":
            if len(registered_devices) >= MAX_DEVICES and params.get('device_name') not in registered_devices:
                return {"status": "error", "msg": "注册设备数已达上限", "result": None}, 503, b""
            
            device_name = params.get('device_name')
            status_str = "在线" if clients.get(device_name) else "离线"
            # 组别由服务端通过 key 识别，不再信任客户端传入的 group
            key_groups = get_key_groups(key)
            # 取第一个组作为设备的主组（user 只有1个组，admin 取第1个作为设备注册组）
            device_group = key_groups[0] if key_groups else '未分组'
            registered_devices[device_name] = {
                "ip": ips.get(device_name, "unknown"),
                "last_registered": time.time(),
                "group": device_group,
                "client": status_str
            }
            return {"status": "success", "msg": "设备注册成功", "result": None}, 200, b""
        
        elif action == "list":
            # 根据请求者的权限和组别过滤设备列表
            if authority == "developer":
                # developer 可以看到所有成员
                result_copy = dict(registered_devices)
            else:
                # admin 和 user 只能看到所属组内的成员
                requester_groups = get_key_groups(key)
                result_copy = {}
                for dev_name, dev_info in registered_devices.items():
                    dev_group = dev_info.get('group', '未分组')
                    if dev_group in requester_groups:
                        result_copy[dev_name] = dict(dev_info)
            return {"status": "success", "msg": "获取列表成功", "result": result_copy}, 200, b""
        
        elif action == "read":
            dev_name = params.get('device_name')
            if dev_name in registered_devices:
                result_copy = dict(registered_devices[dev_name])
                return {"status": "success", "msg": "获取详情成功", "result": result_copy}, 200, b""
            else:
                return {"status": "error", "msg": "设备不存在", "result": None}, 404, b""
        
        elif action == "forward":
            target_name = params.get('device_name')
            inner_params = params.get('params', {})
            target_conn = clients.get(target_name)
            if target_conn:
                packet = pack_data(inner_params, binary_data)
        
        elif action == "divide":
            # 将某个 key 加入某个组（仅 developer 可用，权限已由 authority_dict 控制）
            target_key = params.get('key')
            target_group = params.get('group')
            if not target_key or not target_group:
                return {"status": "error", "msg": "缺少 key 或 group 参数", "result": None}, 400, b""
            
            if target_key not in SECRET_KEY:
                return {"status": "error", "msg": "目标 Key 不存在", "result": None}, 404, b""
            
            target_info = SECRET_KEY[target_key]
            target_authority = target_info.get("authority", "unknown")
            current_groups = target_info.get("groups", [])
            
            # user 只能属于 1 个组，替换
            if target_authority == "user":
                target_info["groups"] = [target_group]
            else:
                # admin/developer 可以属于多个组，追加（去重）
                if target_group not in current_groups:
                    current_groups.append(target_group)
                target_info["groups"] = current_groups
            
            # 持久化到配置文件
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                if target_key in cfg.get("secret_keys", {}):
                    cfg["secret_keys"][target_key]["groups"] = target_info["groups"]
                    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=4)
            except Exception as e:
                logger.error(f"持久化分组配置失败: {e}")
            
            # 同时更新该 key 下已注册设备的 group
            new_primary_group = target_info["groups"][0] if target_info["groups"] else "未分组"
            for dev_name, dev_info in registered_devices.items():
                # 找到使用该 key 注册的设备并更新组
                pass  # 设备会在下次心跳注册时自动更新组
            
            return {"status": "success", "msg": f"已将 Key 分配到组 [{target_group}]", "result": {"groups": target_info["groups"]}}, 200, b""
        
        elif action == "delete":
            dev_name = params.get('device_name')
            if dev_name in registered_devices:
                del registered_devices[dev_name]
                return {"status": "success", "msg": "删除成功", "result": None}, 200, b""
            return {"status": "error", "msg": "设备不存在", "result": None}, 404, b""
        
        elif action == "query_authority":
            query_key = params.get('key')
            if not query_key:
                return {"status": "error", "msg": "缺少 key 参数", "result": None}, 400, b""
            
            key_info = SECRET_KEY.get(query_key)
            if key_info:
                return {"status": "success", "msg": "查询成功", "result": {
                    "authority": key_info["authority"],
                    "groups": key_info.get("groups", [])
                }}, 200, b""
            else:
                return {"status": "success", "msg": "Key 不存在", "result": {"authority": "unknown", "groups": []}}, 200, b""
        
        elif action == "broadcast":
            target_group = params.get('group')
            inner_params_for_broadcast = params.get('params', {})
            
            if not target_group:
                return {"status": "error", "msg": "缺少 group 参数", "result": None}, 400, b""
            
            for dev_name, dev_info in registered_devices.items():
                if dev_info.get('group') == target_group and dev_name in clients:
                    if dev_name != requester:
                        targets_to_broadcast.append((dev_name, clients[dev_name]))
    
    # 锁外执行 I/O 操作
    if action == "forward":
        if target_conn and packet:
            if send_to_connection(target_conn, packet):
                return {"status": "success", "msg": "已转发", "result": None}, 200, b""
            else:
                return {"status": "error", "msg": "转发失败", "result": None}, 500, b""
        else:
            return {"status": "error", "msg": f"目标 {params.get('device_name')} 不在线", "result": None}, 404, b""
    
    if action == "broadcast":
        broadcast_count = 0
        packet = pack_data(inner_params_for_broadcast, binary_data)
        for dev_name, conn in targets_to_broadcast:
            if send_to_connection(conn, packet):
                broadcast_count += 1
        return {"status": "success", "msg": f"已广播到 {broadcast_count} 个设备", "result": {"count": broadcast_count}}, 200, b""
    
    # ================= 图片拼接相关 action =================
    
    if action == "stitch":
        # 图片拼接请求
        x = params.get("x", 0)
        y = params.get("y", 0)
        
        if not isinstance(x, int) or not isinstance(y, int):
            return {"status": "error", "msg": "坐标必须为整数", "result": None}, 400, b""
        
        if x < 0 or y < 0:
            return {"status": "error", "msg": "坐标不能为负数", "result": None}, 400, b""
        
        if len(binary_data) == 0:
            return {"status": "error", "msg": "未接收到图片数据", "result": None}, 400, b""
        
        if len(binary_data) > MAX_DATA_SIZE:
            return {"status": "error", "msg": "图片数据过大", "result": None}, 400, b""
        
        # 同时保存单独图片
        save_individual = params.get("save_individual", True)
        if save_individual:
            saved_name = save_individual_image(binary_data, x, y, requester)
        
        # 执行拼接
        success = stitch_image(binary_data, x, y)
        
        if success:
            canvas_w = canvas_max_x + IMG_W
            canvas_h = canvas_max_y + IMG_H
            result = {
                "canvas_size": f"{canvas_w}x{canvas_h}",
            }
            if save_individual and saved_name:
                result["saved_filename"] = saved_name
            return {"status": "success", "msg": "拼接成功", "result": result}, 200, b""
        else:
            return {"status": "error", "msg": "图片处理失败", "result": None}, 500, b""
    
    elif action == "get_map":
        # 通过协议获取拼接地图
        if not os.path.exists(MAP_PATH):
            return {"status": "error", "msg": "地图不存在", "result": None}, 404, b""
        
        try:
            with open(MAP_PATH, 'rb') as f:
                map_data = f.read()
            
            canvas_w = canvas_max_x + IMG_W if (canvas_max_x > 0 or canvas_max_y > 0) else 0
            canvas_h = canvas_max_y + IMG_H if (canvas_max_x > 0 or canvas_max_y > 0) else 0
            
            result = {
                "canvas_size": f"{canvas_w}x{canvas_h}",
                "file_size": len(map_data)
            }
            return {"status": "success", "msg": "获取地图成功", "result": result}, 200, map_data
        except Exception as e:
            logger.error(f"读取地图失败: {e}")
            return {"status": "error", "msg": f"读取地图失败: {e}", "result": None}, 500, b""
    
    elif action == "get_saved_images":
        # 获取已保存的图片列表
        limit = params.get("limit", 100)
        offset = params.get("offset", 0)
        image_list = get_saved_image_list(limit, offset)
        return {"status": "success", "msg": "获取图片列表成功", "result": image_list}, 200, b""
    
    elif action == "get_image":
        # 通过协议获取指定图片
        filename = params.get("filename")
        if not filename:
            return {"status": "error", "msg": "缺少 filename 参数", "result": None}, 400, b""
        
        # 安全性检查：防止路径遍历
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(IMAGES_DIR, safe_filename)
        
        if not os.path.exists(filepath):
            return {"status": "error", "msg": "图片不存在", "result": None}, 404, b""
        
        try:
            with open(filepath, 'rb') as f:
                img_data = f.read()
            result = {
                "filename": safe_filename,
                "file_size": len(img_data)
            }
            return {"status": "success", "msg": "获取图片成功", "result": result}, 200, img_data
        except Exception as e:
            logger.error(f"读取图片失败: {e}")
            return {"status": "error", "msg": f"读取图片失败: {e}", "result": None}, 500, b""

    return {"status": "error", "msg": f"Unknown action: {action}", "result": None}, 400, b""


# ================= TCP 服务 =================

def tcp_worker():
    """TCP 服务工作线程"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', TCP_PORT))
    server.listen(10)
    logger.info(f"TCP Server listening on port {TCP_PORT}")
    
    def handle_tcp_client(conn, addr):
        device_id = None
        try:
            while running:
                len_bytes = conn.recv(4)
                if not len_bytes:
                    break
                
                header_len = struct.unpack('!I', len_bytes)[0]
                
                if header_len > MAX_HEADER_SIZE:
                    logger.warning(f"Header too large from {addr}: {header_len}")
                    break
                
                json_bytes = recvall(conn, header_len)
                if not json_bytes:
                    break
                
                try:
                    header = json.loads(json_bytes.decode('utf-8'))
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error: {e}")
                    continue
                
                data_len = header.get("date_len", 0)
                
                if data_len > MAX_DATA_SIZE:
                    logger.warning(f"Data too large from {addr}: {data_len}")
                    break
                
                binary_data = recvall(conn, data_len) if data_len > 0 else b""
                
                requester = header.get('requester')
                if requester:
                    device_id = requester
                    with devices_lock:
                        if len(clients) >= MAX_CLIENTS and requester not in clients:
                            logger.warning(f"客户端数量已达上限，拒绝新连接: {requester}")
                            break
                        clients[device_id] = conn
                        ips[device_id] = addr[0]
                
                req_id = header.get('request_id')
                if req_id == "keep":
                    handle_request(header, binary_data)
                    continue
                
                resp, _, resp_binary = handle_request(header, binary_data)
                if req_id:
                    resp["request_id"] = req_id
                    conn.sendall(pack_data(resp, resp_binary))
                    
        except Exception as e:
            logger.error(f"TCP Error {addr}: {e}")
        finally:
            conn.close()
            if device_id:
                logger.info(f"TCP Client disconnected: {device_id}")
                with devices_lock:
                    clients.pop(device_id, None)
    
    while running:
        try:
            client, addr = server.accept()
            threading.Thread(target=handle_tcp_client, args=(client, addr), daemon=True).start()
        except Exception as e:
            logger.error(f"TCP accept error: {e}")


# ================= FastAPI 应用 =================

app = FastAPI(title="Remote Control & Image Stitching Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get("/")
async def index():
    """首页"""
    canvas_w = canvas_max_x + IMG_W if (canvas_max_x > 0 or canvas_max_y > 0) else 0
    canvas_h = canvas_max_y + IMG_H if (canvas_max_x > 0 or canvas_max_y > 0) else 0
    has_map = os.path.exists(MAP_PATH)
    
    # 统计已保存图片数量
    img_count = 0
    try:
        for ext in ['*.jpg', '*.jpeg', '*.png']:
            img_count += len(glob.glob(os.path.join(IMAGES_DIR, ext)))
    except Exception:
        pass
    
    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Remote Control & Image Stitching Server</title>
        <style>
            body {{ font-family: Arial, sans-serif; background: #1e1e1e; color: #ccc; padding: 40px; }}
            h1 {{ color: #007acc; }}
            .info {{ background: #252526; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            code {{ background: #333; padding: 2px 6px; border-radius: 4px; }}
            .section {{ margin: 10px 0; }}
            .badge {{ display: inline-block; background: #007acc; color: #fff; padding: 3px 10px; border-radius: 12px; font-size: 0.85em; }}
            .badge.green {{ background: #2ea043; }}
            .badge.orange {{ background: #d29922; }}
        </style>
    </head>
    <body>
        <h1>🚀 Remote Control & Image Stitching Server</h1>
        <div class="info">
            <div class="section">
                <p><strong>远程控制服务</strong></p>
                <p>TCP 端口: <code>{TCP_PORT}</code></p>
                <p>Web 端口: <code>{WEB_PORT}</code></p>
            </div>
            <hr style="border-color: #444;">
            <div class="section">
                <p><strong>图片拼接服务</strong></p>
                <p>地图文件: <code>{'存在' if has_map else '未创建'}</code>
                    <span class="badge {'green' if has_map else 'orange'}">{'✓' if has_map else '✗'}</span>
                </p>
                <p>画布尺寸: <code>{canvas_w}x{canvas_h}</code></p>
                <p>已保存图片: <code>{img_count}</code> 张</p>
            </div>
        </div>
        <div class="info">
            <p><strong>API 端点</strong></p>
            <ul>
                <li><code>GET /map</code> - 获取拼接地图</li>
                <li><code>GET /map/info</code> - 获取地图信息</li>
                <li><code>GET /map/tile?x=0&y=0&w=1920&h=1080</code> - 获取地图裁剪区域</li>
                <li><code>GET /images</code> - 获取已保存图片列表</li>
                <li><code>GET /images/{{filename}}</code> - 获取指定图片</li>
                <li><code>WS /ws</code> - WebSocket 连接</li>
            </ul>
        </div>
    </body>
    </html>
    """)


@app.get("/map")
async def get_map_image():
    """获取拼接地图图片"""
    if not os.path.exists(MAP_PATH):
        return JSONResponse(
            status_code=404,
            content={"status": "error", "msg": "地图不存在"}
        )
    
    try:
        with open(MAP_PATH, 'rb') as f:
            data = f.read()
        return Response(content=data, media_type="image/jpeg")
    except Exception as e:
        logger.error(f"读取地图失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "msg": f"读取地图失败: {e}"}
        )


@app.get("/map/info")
async def get_map_info():
    """获取地图信息"""
    has_map = os.path.exists(MAP_PATH)
    result = {
        "exists": has_map,
        "map_path": MAP_PATH,
        "canvas_max_x": canvas_max_x,
        "canvas_max_y": canvas_max_y,
    }
    
    if has_map:
        try:
            img = Image.open(MAP_PATH)
            w, h = img.size
            img.close()
            result["width"] = w
            result["height"] = h
            result["file_size"] = os.path.getsize(MAP_PATH)
        except Exception as e:
            result["error"] = str(e)
    
    return JSONResponse(content={"status": "success", "result": result})


@app.get("/map/tile")
async def get_map_tile(x: int = 0, y: int = 0, w: int = 1920, h: int = 1080):
    """获取地图裁剪区域"""
    if not os.path.exists(MAP_PATH):
        return JSONResponse(
            status_code=404,
            content={"status": "error", "msg": "地图不存在"}
        )
    
    try:
        img = Image.open(MAP_PATH)
        
        # 边界校验
        img_w, img_h = img.size
        x = max(0, min(x, img_w - 1))
        y = max(0, min(y, img_h - 1))
        x2 = min(x + w, img_w)
        y2 = min(y + h, img_h)
        
        tile = img.crop((x, y, x2, y2))
        img.close()
        
        buf = io.BytesIO()
        tile.save(buf, "JPEG", quality=90)
        buf.seek(0)
        
        return Response(content=buf.read(), media_type="image/jpeg")
    except Exception as e:
        logger.error(f"裁剪地图失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "msg": f"裁剪地图失败: {e}"}
        )


@app.get("/images")
async def list_saved_images(limit: int = 100, offset: int = 0):
    """获取已保存图片列表"""
    result = get_saved_image_list(limit, offset)
    return JSONResponse(content={"status": "success", "result": result})


@app.get("/images/{filename}")
async def get_saved_image(filename: str):
    """获取指定保存的图片"""
    # 安全性检查：防止路径遍历
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(IMAGES_DIR, safe_filename)
    
    if not os.path.exists(filepath):
        return JSONResponse(
            status_code=404,
            content={"status": "error", "msg": "图片不存在"}
        )
    
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
        
        # 根据扩展名返回对应的 content type
        ext = os.path.splitext(safe_filename)[1].lower()
        media_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
        }
        media_type = media_types.get(ext, 'image/jpeg')
        
        return Response(content=data, media_type=media_type)
    except Exception as e:
        logger.error(f"读取图片失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "msg": f"读取图片失败: {e}"}
        )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """WebSocket 连接端点 - 支持远程控制和图片拼接"""
    await ws.accept()
    temp_id = f"web_{id(ws)}"
    device_id = temp_id
    
    with devices_lock:
        clients[temp_id] = ws
        ips[temp_id] = ws.client.host if ws.client else "unknown"
    
    try:
        while True:
            data = await ws.receive_bytes()
            
            if len(data) < 4:
                continue
            
            header_len = struct.unpack('!I', data[:4])[0]
            
            if header_len > MAX_HEADER_SIZE:
                logger.warning(f"WS Header too large: {header_len}")
                continue
            
            header_bytes = data[4:4 + header_len]
            
            try:
                header = json.loads(header_bytes.decode('utf-8'))
            except json.JSONDecodeError:
                continue
            
            binary_data = data[4 + header_len:]
            
            requester = header.get('requester')
            if requester and requester != device_id:
                with devices_lock:
                    clients.pop(device_id, None)
                    device_id = requester
                    clients[device_id] = ws
                    ips[device_id] = ws.client.host if ws.client else "unknown"
            
            # 兼容 new.py 的直接拼接模式（无 action，有 x, y 坐标的二进制帧）
            if "action" not in header and "x" in header and "y" in header:
                x = header.get("x", 0)
                y = header.get("y", 0)
                
                if not isinstance(x, int) or not isinstance(y, int):
                    resp = {"status": "error", "msg": "坐标必须为整数"}
                    await ws.send_bytes(pack_data(resp))
                    continue
                
                if x < 0 or y < 0:
                    resp = {"status": "error", "msg": "坐标不能为负数"}
                    await ws.send_bytes(pack_data(resp))
                    continue
                
                if len(binary_data) == 0:
                    resp = {"status": "error", "msg": "未接收到图片数据"}
                    await ws.send_bytes(pack_data(resp))
                    continue
                
                if len(binary_data) > MAX_DATA_SIZE:
                    resp = {"status": "error", "msg": "图片数据过大"}
                    await ws.send_bytes(pack_data(resp))
                    continue
                
                # 在线程池中执行拼接
                loop = asyncio.get_event_loop()
                success = await loop.run_in_executor(
                    None, stitch_image, binary_data, x, y
                )
                
                # 同时保存单独图片
                await loop.run_in_executor(
                    None, save_individual_image, binary_data, x, y, device_id
                )
                
                if success:
                    resp = {
                        "status": "success",
                        "msg": "拼接成功",
                        "canvas_size": f"{canvas_max_x + IMG_W}x{canvas_max_y + IMG_H}"
                    }
                else:
                    resp = {"status": "error", "msg": "图片处理失败"}
                
                if "request_id" in header:
                    resp["request_id"] = header["request_id"]
                
                await ws.send_bytes(pack_data(resp))
                continue
            
            # 标准 action 模式
            resp, _, resp_binary = handle_request(header, binary_data)
            
            if "request_id" in header:
                resp["request_id"] = header["request_id"]
                await ws.send_bytes(pack_data(resp, resp_binary))
                
    except WebSocketDisconnect:
        logger.info(f"WS Disconnected: {device_id}")
    except Exception as e:
        logger.error(f"WS Error: {e}")
    finally:
        with devices_lock:
            clients.pop(device_id, None)


# ================= 清理任务 =================

def cleanup_loop():
    """定期清理超时设备"""
    while running:
        time.sleep(10)
        with devices_lock:
            now = time.time()
            to_remove = []
            for name, info in registered_devices.items():
                if now - info['last_registered'] > DEVICE_TIMEOUT:
                    to_remove.append(name)
            
            for name in to_remove:
                logger.info(f"Cleaning up timeout device: {name}")
                registered_devices.pop(name, None)
                clients.pop(name, None)
                ips.pop(name, None)


# ================= 启动入口 =================

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Remote Control & Image Stitching Server Starting...")
    logger.info("=" * 50)
    
    # 如果已存在 map.jpg，尝试加载其尺寸来恢复状态
    if os.path.exists(MAP_PATH):
        try:
            _img = Image.open(MAP_PATH)
            w, h = _img.size
            _img.close()
            # 反推最大坐标
            canvas_max_x = max(0, w - IMG_W)
            canvas_max_y = max(0, h - IMG_H)
            logger.info(f"已加载已有 map.jpg: {w}x{h}, 推断最大坐标=({canvas_max_x}, {canvas_max_y})")
        except Exception as e:
            logger.warning(f"无法读取已有 map.jpg: {e}")
    
    # 启动 TCP 线程
    t_tcp = threading.Thread(target=tcp_worker, daemon=True)
    t_tcp.start()
    
    # 启动清理线程
    t_clean = threading.Thread(target=cleanup_loop, daemon=True)
    t_clean.start()
    
    # 启动配置监听线程
    t_config = threading.Thread(target=config_watcher_loop, daemon=True)
    t_config.start()

    
    logger.info(f"Web Port: {WEB_PORT}, TCP Port: {TCP_PORT}")
    logger.info(f"Map save path: {MAP_PATH}")
    logger.info(f"Images save path: {IMAGES_DIR}")
    
    # 运行 FastAPI
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_loop = loop
    
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=WEB_PORT,
        loop="asyncio",
        ws_max_size=100 * 1024 * 1024,  # 100MB
        log_level="warning"
    )
    server = uvicorn.Server(config)
    loop.run_until_complete(server.serve())
