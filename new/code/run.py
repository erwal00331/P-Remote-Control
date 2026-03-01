"""
远程控制客户端主程序
重构版：移除自动更新，增强健壮性，使用比例坐标，支持跨平台降级
"""
import threading
import queue
import time
import json
import struct
import uuid
import gc
import logging
import sys
import os
import psutil
import numpy as np
try:
    import cv2
except ImportError:
    cv2 = None

import uvicorn

# 平台检测 (必须在其他模块之前导入)
from platform_compat import init_platform, get_platform_info, IS_WINDOWS

try:
    import tkinter as tk
    from ui_app import App as UIApp
except ImportError:
    tk = None
    UIApp = None

from executor import CommandExecutor, InputSim
from p2p import P2PManager
from ws import NetworkManager
from automation import DataManager, OcrManager, ActionExecutor
from api_manager import create_ocr_client, create_ai_client, AITaskExecutor
from path_helper import get_data_file
from video_service import VideoService
from monitor import check_memory_and_gc

# ================= 日志配置 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ================= 配置加载 =================
config_path = get_data_file('配置.json')

try:
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
except Exception as e:
    logger.warning(f"加载配置失败: {e}, 使用默认配置")
    config = {}

SERVER_IP = config.get("server_ip", "127.0.0.1")
SERVER_PORT = config.get("server_port", 8100)
MY_KEY = config.get("my_key", str(uuid.uuid4()))
MY_NAME = config.get("my_name", f"设备_{uuid.uuid4().hex[:8]}")
WINDOW = config.get("window", 0)
ALLOW_CONTROL = config.get("allow_control", "admin")
WEB_PORT = config.get("web_port", 8000)
ENABLE_GUI = config.get("enable_gui", True)

# 服务端会通过 key 识别组别，客户端不再需要配置组别
MY_GROUPS = []  # 将在连接服务器后通过 query_authority 获取

# ================= 全局状态 =================
action_queue = queue.Queue(maxsize=500)
send_event = threading.Event()
send_event.set()
video_thread = None
V_stop_event = None
_last_gc_time = time.time()

# P2P 权限状态
p2p_peer_authority = None
p2p_auth_verified = False

# 智能帧率控制状态
current_fps = 30  # 初始帧率

def check_control_permission(peer_authority):
    """检查对方权限是否允许控制本设备"""
    if ALLOW_CONTROL == "any":
        return True
    elif ALLOW_CONTROL == "admin":
        return peer_authority in ["admin", "developer"]
    elif ALLOW_CONTROL == "none":
        return False
    return peer_authority in ["admin", "developer"]

# ================= 实例化模块 =================
cmd_executor = CommandExecutor()
auto_data_manager = DataManager()
auto_ocr_manager = OcrManager(auto_data_manager, cmd_executor.cam_manager)
auto_action_executor = ActionExecutor(auto_data_manager, auto_ocr_manager, InputSim)

# AI 任务执行器 (延迟初始化，需要网络管理器)
ai_task_executor = None
ui_app = None  # Global UI instance

from notification import notification_manager

def init_ai_executor():
    """初始化 AI 任务执行器"""
    global ai_task_executor
    try:
        ai_client = create_ai_client()
        ocr_client = create_ocr_client()
        
        def send_ai_status(task_id, status, message, thought=None, target_device=None, **kwargs):
            """发送 AI 任务状态到控制端"""
            params = {
                "device_name": MY_NAME, "task_id": task_id,
                "status": status, "message": message, "timestamp": time.time()
            }
            if thought:
                params["thought"] = thought
            # 调试数据 (action_data, step_result)
            if kwargs.get("action_data"):
                params["action_data"] = kwargs["action_data"]
            if kwargs.get("step_result"):
                params["step_result"] = kwargs["step_result"]
            if kwargs.get("debug_info"):
                params["debug_info"] = kwargs["debug_info"]
            
            # 构造状态消息
            status_msg = CommandExecutor.pack({
                "action": "ai_task_status",
                "params": params
            })
            
            if target_device:
                # 定向发送给发起者
                if target_device == MY_NAME:
                    # 本地自控制
                    send_sync(status_msg)
                else:
                    # 远程控制：单播转发
                    logger.info(f"发送 AI 状态到特定设备: {target_device} ({status})")
                    payload = {
                        "requester": MY_NAME, "action": "forward", "key": MY_KEY,
                        "params": {
                            "device_name": target_device,
                            "params": {
                                "action": "ai_task_status",
                                "params": params
                            }
                        }
                    }
                    net_manager.send_tcp(CommandExecutor.pack(payload))
            else:
                # 默认广播 (兼容旧行为)
                # 1. 发送到本地 WebSocket
                send_sync(status_msg)
                
                # 2. 通过 TCP 广播到其他设备
                payload = {
                    "requester": MY_NAME, "action": "broadcast", "key": MY_KEY,
                    "params": {
                        "group": MY_GROUPS[0] if MY_GROUPS else "未分组",
                        "params": {
                            "action": "ai_task_status",
                            "params": params
                        }
                    }
                }
                net_manager.send_tcp(CommandExecutor.pack(payload))
        
        def send_ai_debug_frame(task_id, jpg_bytes):
            """发送 AI 视角调试截图 (带网格+光标标注)"""
            import base64
            frame_base64 = base64.b64encode(jpg_bytes).decode('utf-8')
            
            debug_msg = CommandExecutor.pack({
                "action": "ai_debug_frame",
                "params": {
                    "device_name": MY_NAME, "task_id": task_id,
                    "frame": frame_base64,
                    "timestamp": time.time()
                }
            })
            # 只发送到本地 WebSocket (调试帧不需要广播)
            send_sync(debug_msg)

        def send_ai_frame(task_id, jpg_bytes):
            """发送 AI 任务截图到控制端"""
            import base64
            frame_base64 = base64.b64encode(jpg_bytes).decode('utf-8')
            
            # 构造帧消息
            frame_params = {
                "device_name": MY_NAME, "task_id": task_id,
                "frame": frame_base64,
                "timestamp": time.time()
            }
            frame_msg = CommandExecutor.pack({
                "action": "ai_task_frame",
                "params": frame_params
            })
            
            # 1. 发送到本地 WebSocket (用于本地自控制模式) - 关键修复!
            send_sync(frame_msg)
            
            # 2. 通过 TCP 广播到其他设备 (用于远程控制模式)
            payload = {
                "requester": MY_NAME, "action": "broadcast", "key": MY_KEY,
                "params": {
                    "group": MY_GROUPS[0] if MY_GROUPS else "未分组",
                    "params": {
                        "action": "ai_task_frame",
                        "params": frame_params
                    }
                }
            }
            net_manager.send_tcp(CommandExecutor.pack(payload))
        
        ai_task_executor = AITaskExecutor(
            ai_client=ai_client,
            ocr_client=ocr_client,
            action_executor=auto_action_executor,
            cmd_executor=cmd_executor,
            send_status_func=send_ai_status,
            send_frame_func=send_ai_frame,
            send_debug_frame_func=send_ai_debug_frame,
            notification_func=notification_manager.show_toast
        )
        logger.info("AI 任务执行器初始化成功")
    except Exception as e:
        logger.warning(f"AI 任务执行器初始化失败: {e}")

def get_raw_frame_for_p2p():
    cmd_executor.ensure_camera_started()
    return cmd_executor.get_raw_frame()

def save_allow_control(value):
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        cfg["allow_control"] = value
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"保存配置失败: {e}")


# ================= 消息处理 =================

def process_msg(raw):
    """核心消息路由"""
    global p2p_peer_authority, p2p_auth_verified, _pending_auth_peer, WINDOW, ALLOW_CONTROL, MY_GROUPS
    try:
        if len(raw) < 4:
            return
        hlen = struct.unpack('!I', raw[:4])[0]
        header = json.loads(raw[4:4+hlen])
        body = raw[4+hlen:]
        
        if "status" in header:
            req_id = header.get("request_id", "")
            if req_id.startswith("auth_") and "result" in header:
                result = header.get("result", {})
                authority = result.get("authority", "unknown")
                logger.info(f"收到权限查询响应: {authority}")
                auth_msg = CommandExecutor.pack({
                    "action": "auth_result",
                    "params": {"authority": authority}
                })
                process_msg(auth_msg)
                return
            
            # 拦截组别查询响应
            if req_id.startswith("groups_query_") and "result" in header:
                result = header.get("result", {})
                groups = result.get("groups", [])
                MY_GROUPS.clear()
                MY_GROUPS.extend(groups)
                logger.info(f"从服务器获取组别: {MY_GROUPS}")
                return
            
            # 拦截 UI 的请求响应
            if req_id.startswith("ui_"):
                result = header.get("result", {})
                if req_id.startswith("ui_list_"):
                    # 设备列表响应
                    if ui_app:
                        devices = result if isinstance(result, dict) else {}
                        ui_app.queue_update("update_devices", devices)
                return

            send_sync(raw)
            return
            
        act = header.get("action")
        p = header.get("params", {})
        
        # 日志
        if act and act.startswith("remote"):
            logger.debug(f"收到消息 action={act}")
        
        if act == "server_request":
            real_action = p.get("real_action")
            
            # 优化：检查是否是转发到自己的请求
            if real_action == "forward":
                forward_params = p.get("params", {})
                target_device = forward_params.get("device_name")
                
                # 如果目标是自己，直接处理内部命令，避免服务器往返
                if target_device == MY_NAME:
                    logger.info(f"检测到本地自控制请求，直接处理")
                    inner_params = forward_params.get("params", {})
                    # 补充发起者信息，确保任务能正确识别来源
                    if "requester" not in inner_params:
                        inner_params["requester"] = MY_NAME
                    inner_msg = CommandExecutor.pack(inner_params)
                    # 异步处理，避免阻塞
                    threading.Thread(target=process_msg, args=(inner_msg,), daemon=True).start()
                    return
            
            # 其他情况正常转发到服务器
            req = {
                "requester": MY_NAME,
                "key": MY_KEY,
                "action": real_action,
                "request_id": p.get("request_id"),
                "params": p.get("params", {})
            }
            net_manager.send_tcp(CommandExecutor.pack(req))
            
        elif act == "P2P_SIGNALING":
            p2p_manager.handle_signaling(header["requester"], p["msg_type"], p["sdp_str"])
            
        elif act == "p2p_connect":
            target_device = p["target_device"]
            
            # 自连接检测：如果目标是自己，跳过P2P，进入自控制模式
            if target_device == MY_NAME:
                logger.info("检测到自连接请求，跳过 P2P，进入自控制模式")
                # 发送自连接状态
                send_sync(CommandExecutor.pack({
                    "action": "p2p_status",
                    "params": {"status": "self_connected"}
                }))
                # 自控模式：保留视频流（用户制作脚本时需要看到屏幕画面）
                # 浏览器端的鼠标/键盘输入控制已禁用，脚本端控制（AI任务等）正常
                cmd_executor.ensure_camera_started()
                start_video_loop()
            else:
                p2p_peer_authority = None
                p2p_auth_verified = False
                p2p_manager.connect_to(target_device)
            
        elif act == "p2p_proxy":
            p2p_manager.send_data(CommandExecutor.pack(p, body))
            
        elif act == "change_window":
            WINDOW = p.get('window', 0)
            cmd_executor.switch_monitor(WINDOW)
        
        elif act == "set_allow_control":
            new_value = p.get("value", "admin")
            if new_value in ["any", "admin", "none"]:
                ALLOW_CONTROL = new_value
                save_allow_control(new_value)
                send_sync(CommandExecutor.pack({
                    "action": "allow_control_updated",
                    "params": {"value": ALLOW_CONTROL, "success": True}
                }))
        
        elif act == "get_allow_control":
            send_sync(CommandExecutor.pack({
                "action": "allow_control_value",
                "params": {"value": ALLOW_CONTROL}
            }))
        
        elif act == "get_device_name":
            send_sync(CommandExecutor.pack({
                "action": "device_name_info",
                "params": {"name": MY_NAME}
            }))
        
        elif act == "get_platform_info":
            pinfo = get_platform_info()
            send_sync(CommandExecutor.pack({
                "action": "platform_info",
                "params": pinfo.to_dict()
            }))
            
        elif act == "start_video":
            cmd_executor.ensure_camera_started()
            start_video_loop()
            
        elif act == "stop_video":
            stop_video_loop()
        
        # ========== 自动化功能 ==========
        elif act == "get_buttons":
            result = auto_data_manager.get_all_buttons()
            send_sync(CommandExecutor.pack({"action": "buttons_data", "data": result}))
        
        elif act == "add_button":
            ok, msg = auto_data_manager.add_button(
                p.get("name"), p.get("position"), p.get("group", "默认分组")
            )
            send_sync(CommandExecutor.pack({"action": "button_result", "success": ok, "msg": msg}))
        
        elif act == "update_button":
            ok, msg = auto_data_manager.update_button(
                p.get("name"), position=p.get("position"),
                new_name=p.get("new_name"), group=p.get("group")
            )
            send_sync(CommandExecutor.pack({"action": "button_result", "success": ok, "msg": msg}))
        
        elif act == "delete_button":
            ok, msg = auto_data_manager.delete_button(p.get("name"))
            send_sync(CommandExecutor.pack({"action": "button_result", "success": ok, "msg": msg}))
        
        elif act == "click_button":
            ok, msg = auto_action_executor.click_button(p.get("name"))
            send_sync(CommandExecutor.pack({"action": "button_result", "success": ok, "msg": msg}))
        
        elif act == "get_ocr_regions":
            result = auto_data_manager.get_all_ocr_regions()
            send_sync(CommandExecutor.pack({"action": "ocr_data", "data": result}))
        
        elif act == "add_ocr_region":
            ok, msg = auto_data_manager.add_ocr_region(
                p.get("name"), p.get("position"),
                p.get("data_type", "字符串"), p.get("group")
            )
            send_sync(CommandExecutor.pack({"action": "ocr_result", "success": ok, "msg": msg}))
        
        elif act == "update_ocr_region":
            ok, msg = auto_data_manager.update_ocr_region(
                p.get("name"), position=p.get("position"), data_type=p.get("data_type")
            )
            send_sync(CommandExecutor.pack({"action": "ocr_result", "success": ok, "msg": msg}))
        
        elif act == "delete_ocr_region":
            ok, msg = auto_data_manager.delete_ocr_region(p.get("name"))
            send_sync(CommandExecutor.pack({"action": "ocr_result", "success": ok, "msg": msg}))
        
        elif act == "recognize_ocr":
            def do_ocr(name):
                try:
                    text = auto_ocr_manager.recognize_region(name)
                    send_sync(CommandExecutor.pack({
                        "action": "ocr_text", "success": True, "name": name, "text": text
                    }))
                except Exception as e:
                    send_sync(CommandExecutor.pack({
                        "action": "ocr_text", "success": False, "name": name, "msg": str(e)
                    }))
            
            threading.Thread(target=do_ocr, args=(p.get("name"),), daemon=True).start()
        
        elif act == "get_sequences":
            result = auto_data_manager.get_all_sequences()
            send_sync(CommandExecutor.pack({"action": "sequences_data", "data": result}))
        
        elif act == "save_sequence":
            ok, msg = auto_data_manager.add_sequence(p.get("name"), p.get("actions", []), p.get("group"))
            send_sync(CommandExecutor.pack({"action": "sequence_result", "success": ok, "msg": msg}))
        
        elif act == "delete_sequence":
            ok, msg = auto_data_manager.delete_sequence(p.get("name"))
            send_sync(CommandExecutor.pack({"action": "sequence_result", "success": ok, "msg": msg}))
        
        elif act == "run_sequence":
            name = p.get("name")
            def run_seq():
                ok, msg = auto_action_executor.execute_sequence(name)
                send_sync(CommandExecutor.pack({"action": "sequence_result", "success": ok, "msg": msg}))
            threading.Thread(target=run_seq, daemon=True).start()
            send_sync(CommandExecutor.pack({"action": "sequence_started", "name": name}))
        
        elif act == "stop_sequence":
            auto_action_executor.stop()
            send_sync(CommandExecutor.pack({"action": "sequence_stopped"}))
        
        elif act == "run_actions":
            actions = p.get("actions", [])
            def run_actions():
                ok, msg = auto_action_executor.execute_actions(actions)
                send_sync(CommandExecutor.pack({"action": "actions_result", "success": ok, "msg": msg}))
            threading.Thread(target=run_actions, daemon=True).start()
        
        # ========== 远程脚本执行 ==========
        elif act == "remote_run_sequence":
            seq_name = p.get("name")
            task_id = p.get("task_id", f"task_{time.time()}")
            logger.info(f"收到远程脚本执行请求: {seq_name}")
            
            def run_remote_seq(name, tid):
                send_tcp_status(tid, "running", f"开始执行: {name}")
                ok, msg = auto_action_executor.execute_sequence(name)
                status = "completed" if ok else "failed"
                send_tcp_status(tid, status, msg)
            
            threading.Thread(target=run_remote_seq, args=(seq_name, task_id), daemon=True).start()
            send_tcp_status(task_id, "accepted", f"已接收脚本: {seq_name}")
        
        elif act == "remote_run_actions":
            actions = p.get("actions", [])
            task_id = p.get("task_id", f"task_{time.time()}")
            
            def run_remote_actions(act_list, tid):
                send_tcp_status(tid, "running", "开始执行动作序列")
                ok, msg = auto_action_executor.execute_actions(act_list)
                status = "completed" if ok else "failed"
                send_tcp_status(tid, status, msg)
            
            threading.Thread(target=run_remote_actions, args=(actions, task_id), daemon=True).start()
            send_tcp_status(task_id, "accepted", f"已接收动作列表")
        
        elif act == "remote_stop_sequence":
            task_id = p.get("task_id", "")
            auto_action_executor.stop()
            send_tcp_status(task_id, "stopped", "已停止执行")
        
        elif act == "remote_script_status":
            send_sync(raw)
        
        # ========== AI 控制任务 ==========
        elif act == "start_ai_task":
            if ai_task_executor is None:
                send_tcp_status(p.get("task_id", ""), "error", "AI 执行器未初始化")
            else:
                task_id = p.get("task_id", f"ai_{time.time()}")
                goal = p.get("goal", "")
                max_steps = p.get("max_steps", 30)
                reasoning_effort = p.get("reasoning_effort", "medium")
                screen_index = p.get("screen_index", 0)
                enable_ocr = p.get("enable_ocr", False)
                initiator = header.get("requester") # 获取发起者
                logger.info(f"收到 AI 任务 (来自 {initiator}): {goal} (推理强度: {reasoning_effort}, 屏幕: {screen_index}, OCR: {'开' if enable_ocr else '关'})")
                ai_task_executor.start_task(task_id, goal, max_steps, reasoning_effort, screen_index, initiator=initiator, enable_ocr=enable_ocr)
        
        elif act == "stop_ai_task":
            if ai_task_executor:
                task_id = p.get("task_id", "")
                ai_task_executor.stop_task(task_id)
        
        elif act == "stop_all_remote_ai":
            # 停止所有正在运行的 AI 任务
            if ai_task_executor:
                logger.info("收到停止所有 AI 任务命令")
                ai_task_executor.stop_all_tasks()
        
        elif act == "ai_task_status":
            # 转发 AI 任务状态到 WebSocket 客户端
            send_sync(raw)
        
        elif act == "ai_task_frame":
            # 转发 AI 任务帧到 WebSocket 客户端
            send_sync(raw)

        elif act == "answer_ai_question":
            # 用户回复 AI 的提问
            task_id = p.get("task_id", "")
            answer = p.get("answer", "")
            if ai_task_executor:
                ai_task_executor.provide_input(task_id, answer)
                logger.info(f"收到用户对任务 {task_id} 的回复: {answer}")

            
        # ========== AI 聊天 (新增) ==========
        elif act == "chat":
            if ai_task_executor is None:
                send_sync(CommandExecutor.pack({"action": "chat_response", "params": {"response_text": "AI尚未初始化", "conv_id": p.get("conv_id")}}))
            else:
                def run_chat(params):
                    msg = params.get("message", "")
                    conv_id = params.get("conv_id")
                    
                    try:
                        res = ai_task_executor.ai_client.chat(msg, conv_id)
                        
                        # Check for tool_call
                        tc = res.get("tool_call")
                        if tc and tc.get("name") == "select_devices":
                            # Ask frontend to select
                            send_sync(CommandExecutor.pack({
                                "action": "device_selection_request",
                                "params": {
                                    "conv_id": res.get("conv_id"),
                                    "task_description": tc["params"].get("task_description"),
                                    "pre_selection": tc["params"].get("pre_selection", []),
                                    "script_content": tc["params"].get("script_content")
                                }
                            }))
                            
                            if ui_app:
                                ui_app.queue_update("device_selection", {
                                    "task_description": tc["params"].get("task_description"),
                                    "pre_selection": tc["params"].get("pre_selection", []),
                                    "devices_list": [] # Let UI use its cached list 
                                })
                        
                        resp_data = {
                            "action": "chat_response", 
                            "params": res
                        }
                        send_sync(CommandExecutor.pack(resp_data))
                        
                        if ui_app:
                            # 提取文本回复
                            txt = res.get("response_text", "")
                            # 如果有 tool_call，可能是 "正在执行..."
                            if res.get("tool_call"):
                                txt += f" [调用工具: {res['tool_call']['name']}]"
                            ui_app.queue_update("chat_msg", {"sender": "AI", "text": txt})

                    except Exception as e:
                        logger.error(f"Chat error: {e}")
                        err_msg = {"action": "chat_error", "params": str(e)}
                        send_sync(CommandExecutor.pack(err_msg))
                        if ui_app:
                            ui_app.queue_update("chat_msg", {"sender": "System", "text": f"Error: {e}"})

                threading.Thread(target=run_chat, args=(p,), daemon=True).start()

        elif act == "confirm_batch_task":
            # 用户确认设备选择，开始分发任务
            devices = p.get("devices", [])
            goal = p.get("task_description", "")
            device_configs = p.get("device_configs", {})
            
            logger.info(f"批量执行任务: {goal} -> {devices}")
            
            # 为每个选中的设备启动任务
            for dev in devices:
                task_id = f"batch_{int(time.time())}_{dev}"
                
                # 读取每台设备的独立配置（如无则用默认值）
                dev_cfg = device_configs.get(dev, {})
                dev_max_steps = dev_cfg.get("max_steps", 30)
                dev_reasoning = dev_cfg.get("reasoning_effort", "medium")
                
                logger.info(f"  设备 {dev}: max_steps={dev_max_steps}, reasoning={dev_reasoning}")
                
                # 构造远程启动 AI 任务的请求
                payload = {
                    "requester": MY_NAME, "action": "forward", "key": MY_KEY,
                    "params": {
                        "device_name": dev,
                        "params": {
                            "action": "start_ai_task",
                            "params": {
                                "task_id": task_id,
                                "goal": goal,
                                "max_steps": dev_max_steps,
                                "reasoning_effort": dev_reasoning,
                                "enable_ocr": p.get("enable_ocr", False)
                            }
                        }
                    }
                }
                net_manager.send_tcp(CommandExecutor.pack(payload))
            
            # 通知前端已分发
            send_sync(CommandExecutor.pack({
                "action": "batch_task_started",
                "params": {"count": len(devices), "devices": devices}
            }))
        
        # ========== P2P 权限验证 ==========
        elif act == "P2P_AUTH_KEY":
            peer_key = p.get("key", "")
            peer_name = p.get("name", "unknown")
            logger.info(f"收到控制端 {peer_name} 的 key，正在验证...")
            
            req_id = f"auth_{time.time()}"
            query_req = {
                "requester": MY_NAME, "key": MY_KEY, "action": "query_authority",
                "request_id": req_id, "params": {"key": peer_key}
            }
            _pending_auth_peer = {"name": peer_name, "request_id": req_id}
            net_manager.send_tcp(CommandExecutor.pack(query_req))
        
        elif act == "auth_result":
            authority = p.get("authority", "unknown")
            p2p_peer_authority = authority
            allowed = check_control_permission(authority)
            p2p_auth_verified = allowed
            
            result_msg = {
                "action": "P2P_AUTH_RESPONSE",
                "params": {
                    "allowed": allowed, "authority": authority, "require": ALLOW_CONTROL,
                    "message": "验证通过" if allowed else f"权限不足 (需要: {ALLOW_CONTROL})"
                }
            }
            p2p_manager.send_data(CommandExecutor.pack(result_msg))
        
        elif act == "P2P_AUTH_RESPONSE":
            allowed = p.get("allowed", False)
            send_sync(CommandExecutor.pack({
                "action": "p2p_auth_status",
                "params": {"allowed": allowed, "authority": p.get("authority"),
                          "require": p.get("require"), "message": p.get("message")}
            }))
            
        else:
            try:
                action_queue.put_nowait((act, p))
            except queue.Full:
                logger.warning("动作队列已满，丢弃消息")
                
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")


def action_worker():
    """动作消费者线程"""
    global _last_gc_time
    action_count = 0
    
    while True:
        try:
            action, params = action_queue.get(timeout=30)
            cmd_executor.handle_input(action, params)
            action_queue.task_done()
            action_count += 1
            
            current_time = time.time()
            if action_count % 100 == 0:
                check_memory_and_gc(threshold_mb=600)
                
        except queue.Empty:
            check_memory_and_gc(threshold_mb=500)
        except Exception as e:
            logger.error(f"执行动作错误: {e}")


# ================= 网络回调 =================

def send_sync(data):
    def done():
        send_event.set()
    net_manager.send_ws_sync(data, done)

def send_tcp_status(task_id, status, message):
    payload = {
        "requester": MY_NAME, "action": "broadcast", "key": MY_KEY,
        "params": {
            "group": MY_GROUPS[0] if MY_GROUPS else "未分组",
            "params": {
                "action": "remote_script_status",
                "params": {
                    "device_name": MY_NAME, "task_id": task_id,
                    "status": status, "message": message, "timestamp": time.time()
                }
            }
        }
    }
    net_manager.send_tcp(CommandExecutor.pack(payload))

def p2p_on_data(msg):
    process_msg(msg)

def p2p_on_signal(target, type, sdp):
    payload = {
        "requester": MY_NAME, "action": "forward", "key": MY_KEY,
        "request_id": f"s_{time.time()}",
        "params": {
            "device_name": target,
            "params": {
                "requester": MY_NAME, "action": "P2P_SIGNALING",
                "params": {"sdp_str": sdp, "msg_type": type}
            }
        }
    }
    net_manager.send_tcp(CommandExecutor.pack(payload))

def p2p_on_video(jpg_bytes):
    send_sync(CommandExecutor.pack({"action": "video"}, jpg_bytes))

def p2p_on_status(status):
    send_sync(CommandExecutor.pack({"action": "p2p_status", "params": {"status": status}}))
    
    if status == "channel_open":
        logger.info("P2P 数据通道就绪，发送认证 key...")
        auth_msg = {"action": "P2P_AUTH_KEY", "params": {"key": MY_KEY, "name": MY_NAME}}
        p2p_manager.send_data(CommandExecutor.pack(auth_msg))



# ================= 视频推流 (委托给 video_service) =================

# start_video_loop, stop_video_loop moved to initialization section where video_service is created
# but because process_msg calls them using global scope, we define global wrappers below.



# ================= 初始化和启动 =================

net_manager = NetworkManager(
    SERVER_IP, SERVER_PORT, MY_NAME, MY_KEY,
    on_message_callback=process_msg
)

# 视频服务
def send_ws_data_sync(packet, done_callback=None):
    net_manager.send_ws_sync(packet, done_callback)

video_service = VideoService(cmd_executor, send_ws_data_sync)

def start_video_loop():
    cmd_executor.ensure_camera_started()
    video_service.start()

def stop_video_loop():
    video_service.stop()

p2p_manager = P2PManager(
    MY_NAME,
    on_data=p2p_on_data,
    send_signal=p2p_on_signal,
    on_frame=p2p_on_video,
    on_status=p2p_on_status,
    frame_provider_func=get_raw_frame_for_p2p
)


def _elevate_admin():
    """检查并请求管理员权限 (仅 Windows)"""
    if not IS_WINDOWS:
        return  # 非 Windows 跳过
    
    import ctypes
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return  # 已经是管理员
    except Exception:
        return  # 无法检测，继续运行
    
    # 不是管理员，重新以管理员身份启动
    logger.info("当前非管理员权限，正在请求提升权限...")
    try:
        import ctypes
        # 获取 Python 解释器路径
        python_exe = sys.executable
        # 获取当前脚本路径
        script = os.path.abspath(sys.argv[0])
        # 构造参数
        params = f'"{script}"'
        if len(sys.argv) > 1:
            params += ' ' + ' '.join(f'"{a}"' for a in sys.argv[1:])
        
        # 使用 ShellExecuteW 以 "runas" 方式启动
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,           # hwnd
            "runas",        # lpOperation (请求提升权限)
            python_exe,     # lpFile
            params,          # lpParameters
            None,           # lpDirectory
            1               # nShowCmd (SW_SHOWNORMAL)
        )
        
        if ret > 32:
            # 成功启动了管理员进程，退出当前进程
            logger.info("已启动管理员进程，当前进程退出")
            sys.exit(0)
        else:
            logger.warning(f"请求管理员权限失败 (返回值: {ret})，以普通权限继续运行")
    except Exception as e:
        logger.warning(f"请求管理员权限时出错: {e}，以普通权限继续运行")


if __name__ == "__main__":
    # ====== 请求管理员权限 ======
    _elevate_admin()
    
    # ====== 平台环境检测 ======
    platform_result = init_platform()
    
    if platform_result.missing_features:
        logger.warning("\u26a0\ufe0f 部分功能不可用，程序将以降级模式运行")
    if not IS_WINDOWS:
        logger.info("\u2139\ufe0f 非 Windows 环境，已自动启用跨平台降级方案")
    
    logger.info(f"启动远程控制客户端: {MY_NAME}")
    logger.info(f"服务器: {SERVER_IP}:{SERVER_PORT}")
    logger.info(f"Web 端口: {WEB_PORT}")
    
    p2p_manager.start()
    net_manager.start_tcp_worker()
    
    # 查询本 KEY 的组别
    def query_my_groups():
        """连接服务器后查询本 KEY 的组别"""
        time.sleep(3)  # 等待 TCP 连接建立
        req = {
            "requester": MY_NAME,
            "key": MY_KEY,
            "action": "query_authority",
            "request_id": f"groups_query_{time.time()}",
            "params": {"key": MY_KEY}
        }
        net_manager.send_tcp(CommandExecutor.pack(req))
        logger.info("已发送组别查询请求")
    
    threading.Thread(target=query_my_groups, daemon=True, name="Groups-Query").start()
    
    # 初始化 AI 执行器
    init_ai_executor()
    
    threading.Thread(target=action_worker, daemon=True, name="Action-Worker").start()
    

    
    # UI 事件处理
    def handle_ui_event(action, params):
        try:
            if action == "start_video":
                start_video_loop()
            elif action == "stop_video":
                stop_video_loop()
            elif action == "chat":
                # 构造 chat 消息
                msg = {
                    "action": "chat",
                    "params": {
                        "message": params.get("message"),
                        "conv_id": "ui_chat", # 固定 session for UI
                        "reasoning_effort": params.get("reasoning")
                    }
                }
                # 直接调用处理逻辑
                process_msg(CommandExecutor.pack(msg))
            elif action == "confirm_device_selection":
                # Handle batch confirmation from UI
                msg = {
                    "action": "confirm_batch_task",
                    "params": {
                        "devices": params.get("devices"),
                        "task_description": params.get("task_description"),
                        "device_configs": params.get("device_configs", {})
                    }
                }
                process_msg(CommandExecutor.pack(msg))
            elif action == "list_devices":
                # 发送请求到服务器
                req = {
                    "requester": MY_NAME,
                    "key": MY_KEY,
                    "action": "list",
                    "request_id": f"ui_list_{time.time()}",
                    "params": {}
                }
                net_manager.send_tcp(CommandExecutor.pack(req))
            elif action == "connect_p2p":
                target = params.get("target")
                if target:
                    process_msg(CommandExecutor.pack({
                        "action": "p2p_connect",
                        "params": {"target_device": target}
                    }))
            elif action == "set_mode":
                # 只需更新变量，run.py 中 global WINDOW, ALLOW_CONTROL 等
                pass
            elif action == "set_allow_control":
                # 复用 process_msg 逻辑
                process_msg(CommandExecutor.pack({
                    "action": "set_allow_control",
                    "params": {"value": params.get("value")}
                }))
            elif action == "change_window":
                process_msg(CommandExecutor.pack({
                    "action": "change_window",
                    "params": {"window": params.get("window")}
                }))
            elif action == "load_scripts":
                # 脚本就在本地 DataManager
                seqs = auto_data_manager.get_all_sequences()
                if ui_app:
                    ui_app.queue_update("update_scripts", seqs)
            elif action == "run_script":
                name = params.get("name")
                if name:
                    process_msg(CommandExecutor.pack({
                        "action": "run_sequence", 
                        "params": {"name": name}
                    }))
            elif action == "stop_scripts":
                process_msg(CommandExecutor.pack({"action": "stop_sequence"}))
                
        except Exception as e:
            logger.error(f"UI Event Error: {e}")

    # 启动 FastAPI (Thread)
    def start_server():
        uvicorn.run(net_manager.app, host="0.0.0.0", port=WEB_PORT, log_level="warning")

    server_thread = threading.Thread(target=start_server, daemon=True, name="Uvicorn-Thread")
    server_thread.start()
    
    # 启动 GUI (Main Thread)
    # 启动 GUI (Main Thread)
    if tk and UIApp and ENABLE_GUI:
        logger.info("启动本地 GUI...")
        root = tk.Tk()
        ui_app = UIApp(root, handle_ui_event)
        
        # 初始加载
        root.after(1000, lambda: handle_ui_event("list_devices", {}))
        root.after(1000, lambda: handle_ui_event("load_scripts", {}))
        
        root.mainloop()
    else:
        if not ENABLE_GUI:
            logger.info("配置已禁用本地 GUI，仅运行后台服务")
        else:
            logger.warning("Tkinter 未在主线程启动或导入失败，仅运行后台服务")
        server_thread.join()
