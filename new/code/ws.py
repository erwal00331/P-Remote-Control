"""
网络管理模块
处理 TCP 和 WebSocket 通信，增强健壮性
"""
import socket
import struct
import json
import asyncio
import threading
import time
import logging
from typing import Optional, Callable, Any

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class NetworkManager:
    """网络管理器 - 处理 TCP 和 WebSocket 通信"""
    
    # 配置常量
    TCP_TIMEOUT = 10
    HEARTBEAT_INTERVAL = 20
    MAX_HEADER_SIZE = 1024 * 1024  # 1MB
    MAX_DATA_SIZE = 50 * 1024 * 1024  # 50MB
    RECONNECT_DELAY = 5
    
    def __init__(self, server_ip: str, server_port: int, 
                 my_name: str, my_key: str,
                 on_message_callback: Callable[[bytes], None]):
        """
        初始化网络管理器
        
        Args:
            server_ip: 服务器 IP
            server_port: 服务器端口
            my_name: 设备名称
            my_key: 设备密钥
            on_message_callback: 消息回调函数
        """
        self.server_ip = server_ip
        self.server_port = server_port
        self.my_name = my_name
        self.my_key = my_key
        
        self.cli: Optional[socket.socket] = None
        self.ws_cli: Optional[WebSocket] = None
        self.event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.on_message = on_message_callback
        
        self.running = True
        self.lock = threading.RLock()
        self._connected = False
        self._reconnect_count = 0
        
        # FastAPI App
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            self.event_loop = asyncio.get_running_loop()
            yield
        
        self.app = FastAPI(lifespan=lifespan)
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"]
        )
        
        @self.app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket):
            await ws.accept()
            self.ws_cli = ws
            logger.info("WebSocket 客户端已连接")
            try:
                while True:
                    data = await ws.receive_bytes()
                    if self.on_message:
                        try:
                            self.on_message(data)
                        except Exception as e:
                            logger.error(f"处理 WebSocket 消息时出错: {e}")
            except Exception as e:
                logger.info(f"WebSocket 断开: {e}")
            finally:
                self.ws_cli = None

    def start_tcp_worker(self):
        """启动 TCP 工作线程"""
        threading.Thread(target=self._tcp_loop, daemon=True, name="TCP-Worker").start()
        threading.Thread(target=self._heartbeat, daemon=True, name="Heartbeat").start()
        logger.info("TCP 工作线程已启动")

    def _tcp_loop(self):
        """TCP 连接主循环"""
        while self.running:
            try:
                self._connect()
                self._receive_loop()
            except Exception as e:
                logger.error(f"TCP 连接错误: {e}")
            finally:
                self._close_socket()
                self._connected = False
                self._reconnect_count += 1
                
            if self.running:
                delay = min(self.RECONNECT_DELAY * (1 + self._reconnect_count // 3), 30)
                logger.info(f"{delay} 秒后重连...")
                time.sleep(delay)
    
    def _connect(self):
        """建立 TCP 连接"""
        self.cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.cli.settimeout(self.TCP_TIMEOUT)
        self.cli.connect((self.server_ip, self.server_port))
        self.cli.settimeout(None)
        self._connected = True
        self._reconnect_count = 0
        logger.info(f"TCP 已连接到 {self.server_ip}:{self.server_port}")
    
    def _receive_loop(self):
        """接收消息循环"""
        while self.running and self._connected:
            h_len_b = self._recv_exact(4)
            if not h_len_b:
                break
                
            h_len = struct.unpack('!I', h_len_b)[0]
            
            if h_len > self.MAX_HEADER_SIZE:
                raise Exception(f"头部长度过大: {h_len}")

            head_b = self._recv_exact(h_len)
            if not head_b:
                break
            
            try:
                header = json.loads(head_b)
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析失败: {e}")
                continue
                
            d_len = header.get("date_len", 0)
            
            if d_len > self.MAX_DATA_SIZE:
                raise Exception(f"数据长度过大: {d_len}")

            body = self._recv_exact(d_len) if d_len > 0 else b""
            if len(body) != d_len:
                break
            
            # 重组并回调
            j_bytes = json.dumps(header, ensure_ascii=False).encode("utf-8")
            raw_packet = struct.pack('!I', len(j_bytes)) + j_bytes + body
            
            if self.on_message:
                try:
                    self.on_message(raw_packet)
                except Exception as e:
                    logger.error(f"处理 TCP 消息时出错: {e}")

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """精确接收 n 字节"""
        buf = bytearray(n)
        view = memoryview(buf)
        pos = 0
        while pos < n:
            try:
                nbytes = self.cli.recv_into(view[pos:])
                if not nbytes:
                    return None
                pos += nbytes
            except socket.timeout:
                continue
            except Exception:
                return None
        return bytes(buf)

    def _close_socket(self):
        """关闭 Socket"""
        with self.lock:
            if self.cli:
                try:
                    self.cli.close()
                except Exception:
                    pass
                self.cli = None

    def _heartbeat(self):
        """心跳线程"""
        while self.running:
            with self.lock:
                cli = self.cli
            
            if cli and self._connected:
                try:
                    hb = {
                        "requester": self.my_name,
                        "action": "register",
                        "key": self.my_key,
                        "request_id": "keep",
                        "params": {"device_name": self.my_name},
                        "date_len": 0
                    }
                    j_bytes = json.dumps(hb, ensure_ascii=False).encode("utf-8")
                    packet = struct.pack('!I', len(j_bytes)) + j_bytes
                    cli.sendall(packet)
                except Exception as e:
                    logger.warning(f"心跳发送失败: {e}")
            
            time.sleep(self.HEARTBEAT_INTERVAL)

    def send_tcp(self, data_bytes: bytes) -> bool:
        """
        发送 TCP 数据
        
        Args:
            data_bytes: 要发送的数据
            
        Returns:
            是否成功
        """
        with self.lock:
            if self.cli and self._connected:
                try:
                    self.cli.sendall(data_bytes)
                    return True
                except Exception as e:
                    logger.error(f"TCP 发送失败: {e}")
        return False

    def send_ws_sync(self, data_bytes: bytes, done_callback: Callable = None) -> bool:
        """
        线程安全地通过 WebSocket 发送数据
        
        Args:
            data_bytes: 要发送的数据
            done_callback: 完成回调
            
        Returns:
            是否成功调度
        """
        if self.ws_cli and self.event_loop:
            async def _wrapper():
                try:
                    await self.ws_cli.send_bytes(data_bytes)
                except Exception as e:
                    # 尝试解析动作名称以便调试
                    action_name = "unknown"
                    try:
                        if len(data_bytes) >= 4:
                            h_len = struct.unpack('!I', data_bytes[:4])[0]
                            if 4 + h_len <= len(data_bytes):
                                header = json.loads(data_bytes[4:4+h_len])
                                action_name = header.get("action", "unknown")
                    except:
                        pass
                    logger.error(f"WebSocket 发送失败 [{action_name}]: {e!r}")
                finally:
                    if done_callback:
                        done_callback()
                        
            try:
                asyncio.run_coroutine_threadsafe(_wrapper(), self.event_loop)
                return True
            except Exception as e:
                logger.error(f"调度 WebSocket 发送失败: {e!r}")
                if done_callback:
                    done_callback()
                return False
        else:
            if done_callback:
                done_callback()
            return False
    
    def is_connected(self) -> bool:
        """检查 TCP 是否已连接"""
        return self._connected
    
    def stop(self):
        """停止网络管理器"""
        self.running = False
        self._close_socket()
        logger.info("网络管理器已停止")
