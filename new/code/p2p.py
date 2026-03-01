"""
P2P 模块 - WebRTC 点对点连接
增强版：更好的资源管理和错误处理
"""
import asyncio
import threading
import gc
import cv2
import time
import logging
from typing import Optional, Callable, Any

import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, VideoStreamTrack
from av import VideoFrame

logger = logging.getLogger(__name__)

# ICE 服务器配置
ICE_SERVERS = RTCConfiguration(iceServers=[
    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
    RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
    RTCIceServer(urls=["stun:stun.cloudflare.com:3478"]),
    RTCIceServer(urls=["stun:stun.qq.com:3478"]),
    RTCIceServer(urls=["stun:stun.miwifi.com:3478"]),
])

# 编码器配置
CODEC_CONFIG = {
    "target_fps": 30,
    "max_resolution": 1920,
    "keyframe_interval": 60,
}

# 预分配的黑屏帧
_BLACK_FRAME_720P = None

def _get_black_frame():
    global _BLACK_FRAME_720P
    if _BLACK_FRAME_720P is None:
        # Blue background to distinguish from real black screen
        _BLACK_FRAME_720P = np.full((720, 1280, 3), (100, 0, 0), dtype=np.uint8) # BGR: Blue
        try:
            cv2.putText(_BLACK_FRAME_720P, "NO SIGNAL / CAPTURE ERROR", (300, 360), 
                       cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
            # Add timestamp to show it's alive
            cv2.putText(_BLACK_FRAME_720P, "CHECK SERVER LOGS", (400, 450), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (200, 200, 200), 2)
        except Exception:
            pass
    return _BLACK_FRAME_720P


class ScreenShareTrack(VideoStreamTrack):
    """屏幕共享视频轨道"""
    kind = "video"
    
    def __init__(self, frame_provider: Callable, target_fps: int = 30):
        super().__init__()
        self.frame_provider = frame_provider
        self.target_fps = target_fps
        self.frame_interval = 1.0 / target_fps
        self.last_frame_time = 0
        self._last_frame_bgr = None 
        self._cache_expire_time = 0
        self._CACHE_TTL = 2.0
        
    def _resize_frame(self, frame_bgr: np.ndarray, max_width: int = 1920) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        if w > max_width:
            scale = max_width / w
            new_h = int(h * scale)
            return cv2.resize(frame_bgr, (max_width, new_h), interpolation=cv2.INTER_LINEAR)
        return frame_bgr
        
    def _get_frame(self) -> VideoFrame:
        current_time = time.monotonic()
        
        try:
            frame_bgr = self.frame_provider()
        except Exception as e:
            # log error with throttling
            current_time = time.monotonic()
            if current_time - getattr(self, "_last_error_log_time", 0) > 5.0:
                logger.error(f"P2P capture error: {e}")
                self._last_error_log_time = current_time
            frame_bgr = None
            
        if frame_bgr is None:
            if self._last_frame_bgr is not None and current_time < self._cache_expire_time:
                return VideoFrame.from_ndarray(self._last_frame_bgr, format="bgr24")
            
            # log black frame generation (throttled)
            if current_time - getattr(self, "_last_black_log_time", 0) > 5.0:
                logger.warning("Generating P2P black frame (source returned None)")
                self._last_black_log_time = current_time
                
            return VideoFrame.from_ndarray(_get_black_frame(), format="bgr24")
        
        try:
            frame_bgr = self._resize_frame(frame_bgr, CODEC_CONFIG["max_resolution"])
            h, w = frame_bgr.shape[:2]
            if w % 2 != 0 or h % 2 != 0:
                new_w = w - (w % 2)
                new_h = h - (h % 2)
                frame_bgr = frame_bgr[:new_h, :new_w]
            
            frame = VideoFrame.from_ndarray(frame_bgr, format="bgr24")
            self._last_frame_bgr = frame_bgr
            self._cache_expire_time = current_time + self._CACHE_TTL
            return frame
        except Exception:
            if self._last_frame_bgr is not None:
                return VideoFrame.from_ndarray(self._last_frame_bgr, format="bgr24")
            return VideoFrame.from_ndarray(_get_black_frame(), format="bgr24")

    async def recv(self) -> VideoFrame:
        pts, time_base = await self.next_timestamp()
        
        current_time = time.monotonic()
        elapsed = current_time - self.last_frame_time
        if elapsed < self.frame_interval:
            await asyncio.sleep(self.frame_interval - elapsed)
        self.last_frame_time = time.monotonic()
        
        loop = asyncio.get_running_loop()
        try:
            frame = await loop.run_in_executor(None, self._get_frame)
        except Exception:
            frame = VideoFrame.from_ndarray(_get_black_frame(), format="bgr24")
        
        frame.pts = pts
        frame.time_base = time_base
        return frame


class P2PManager:
    """P2P 连接管理器"""
    
    def __init__(self, my_name: str, on_data: Callable, send_signal: Callable,
                 on_frame: Callable, on_status: Callable, frame_provider_func: Callable):
        self.my_name = my_name
        self.on_data = on_data
        self.send_signal = send_signal
        self.on_frame = on_frame
        self.on_status = on_status
        self.frame_provider_func = frame_provider_func
        
        self.pc: Optional[RTCPeerConnection] = None
        self.channel = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.running = False
        self.loop_ready = threading.Event()
        self._lock = asyncio.Lock()

    def start(self):
        if self.running:
            return
            
        def _run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop_ready.set()
            self.loop.run_forever()
            
        self.running = True
        threading.Thread(target=_run, daemon=True, name="P2P-Loop").start()
        self.loop_ready.wait()
        logger.info("P2P 管理器已启动")

    def connect_to(self, target: str):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._create_offer(target), self.loop)

    def handle_signaling(self, src: str, msg_type: str, sdp: str):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._handle_sdp(src, msg_type, sdp), self.loop)

    def send_data(self, data: bytes):
        if self.channel and self.channel.readyState == "open":
            try:
                self.loop.call_soon_threadsafe(self.channel.send, data)
            except Exception as e:
                logger.error(f"发送 P2P 数据失败: {e}")

    async def _close_pc(self):
        if self.pc:
            try:
                await self.pc.close()
            except Exception:
                pass
            self.pc = None
            self.channel = None
            gc.collect()

    async def _init_pc(self):
        await self._close_pc()
        pc = RTCPeerConnection(configuration=ICE_SERVERS)
        self.pc = pc
        
        pc.on("connectionstatechange", self._on_connection_state_change)
        pc.on("track", self._on_track)
        pc.on("datachannel", self._on_datachannel)

    async def _on_connection_state_change(self):
        if not self.pc:
            return
        state = self.pc.connectionState
        logger.info(f"P2P 状态: {state}")
        if self.on_status:
            self.on_status(state)

    def _on_track(self, track):
        logger.info("收到视频轨道")
        if track.kind == "video":
            asyncio.ensure_future(self._consume_video(track))

    async def _create_offer(self, target: str):
        try:
            await self._init_pc()
            self.pc.addTrack(ScreenShareTrack(
                self.frame_provider_func,
                target_fps=CODEC_CONFIG["target_fps"]
            ))
            c = self.pc.createDataChannel("control")
            c.on("message", self.on_data)
            c.on("open", self._on_channel_open)
            self.channel = c
            
            offer = await self.pc.createOffer()
            await self.pc.setLocalDescription(offer)
            await self._wait_for_ice_gathering(self.pc)
            
            if self.pc.localDescription:
                self.send_signal(target, "offer", self.pc.localDescription.sdp)
        except Exception as e:
            logger.error(f"创建 Offer 失败: {e}")
            await self._close_pc()

    def _on_channel_open(self):
        logger.info("P2P 数据通道已打开")
        if self.on_status:
            self.on_status("channel_open")

    def _on_datachannel(self, channel):
        self.channel = channel
        channel.on("message", self.on_data)
        channel.on("open", self._on_channel_open)

    async def _consume_video(self, track):
        frame_count = 0
        skip_frames = 0
        last_process_time = time.monotonic()
        jpeg_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
        
        while True:
            try:
                if self.pc and self.pc.connectionState in ["closed", "failed"]:
                    break
                
                frame = await track.recv()
                frame_count += 1
                
                if skip_frames > 0:
                    skip_frames -= 1
                    continue
                
                current_time = time.monotonic()
                if current_time - last_process_time < 0.015:
                    skip_frames = 2
                last_process_time = current_time
                
                img = frame.to_ndarray(format="bgr24")
                ret, jpeg = cv2.imencode('.jpg', img, jpeg_params)
                if ret and self.on_frame:
                    self.on_frame(jpeg.tobytes())
                
                if frame_count % 1000 == 0:
                    gc.collect()
                    
            except Exception as e:
                logger.info(f"视频流结束: {e}")
                break
        
        gc.collect()

    async def _wait_for_ice_gathering(self, pc: RTCPeerConnection, timeout: float = 5.0) -> bool:
        if pc.iceGatheringState == "complete":
            return True
        elapsed = 0
        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.1)
            elapsed += 0.1
            if elapsed > timeout:
                logger.warning("ICE 收集超时")
                return False
        return True



    async def _handle_sdp(self, src: str, sdp_type: str, sdp: str):
        try:
            if not self.pc:
                await self._init_pc()
            
            await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
            
            if sdp_type == "offer":
                self.pc.addTrack(ScreenShareTrack(
                    self.frame_provider_func,
                    target_fps=CODEC_CONFIG["target_fps"]
                ))
                ans = await self.pc.createAnswer()
                await self.pc.setLocalDescription(ans)
                await self._wait_for_ice_gathering(self.pc)
                if self.pc.localDescription:
                    self.send_signal(src, "answer", self.pc.localDescription.sdp)
                    
            if sdp_type == "answer":
                logger.info("P2P 连接成功")
                
        except Exception as e:
            logger.error(f"处理 SDP 失败: {e}")
            await self._close_pc()
    
    def stop(self):
        self.running = False
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._close_pc(), self.loop)
        logger.info("P2P 管理器已停止")
