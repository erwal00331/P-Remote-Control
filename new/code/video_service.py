import threading
import time
import gc
import logging
from executor import CommandExecutor
from monitor import check_memory_and_gc

logger = logging.getLogger(__name__)

class VideoService:
    """
    视频流服务
    负责采集屏幕、智能帧率控制、推流
    """
    def __init__(self, cmd_executor, send_func):
        self.cmd_executor = cmd_executor
        self.send_func = send_func  # 必须接受 (bytes) -> None
        
        self.running = False
        self.stop_event = threading.Event()
        self.video_thread = None
        self.send_event = threading.Event()
        self.send_event.set()
        
        # 智能帧率状态
        self._static_frame_count = 0
        self._last_frame_bytes = None

    def start(self):
        if self.video_thread and self.video_thread.is_alive():
            return
        self.running = True
        self.stop_event.clear()
        self.send_event.set()
        
        self.video_thread = threading.Thread(target=self._loop, daemon=True, name="Video-Loop")
        self.video_thread.start()
        logger.info("Video service started")

    def stop(self):
        self.running = False
        self.stop_event.set()
        if self.video_thread:
            self.video_thread.join(timeout=1.0)
        logger.info("Video service stopped")

    def trigger_send(self):
        """外部触发发送信号（例如P2P请求）"""
        self.send_event.set()

    def _loop(self):
        self._static_frame_count = 0
        self._last_frame_bytes = None
        frame_count = 0
        
        while not self.stop_event.is_set():
            if self.send_event.is_set():
                self.send_event.clear()
                
                # 获取截图
                jpg = self.cmd_executor.get_screenshot()
                
                if jpg:
                    # Smart Frame Rate Logic
                    is_static = False
                    if self._last_frame_bytes and len(jpg) == len(self._last_frame_bytes):
                         # Simple byte comparison
                         if jpg == self._last_frame_bytes:
                             is_static = True
                    
                    if is_static:
                        self._static_frame_count += 1
                        # 如果静止超过 30 帧 (约1秒)，降低采样率
                        if self._static_frame_count > 30:
                             time.sleep(0.2) # -> 5 FPS
                        else:
                             time.sleep(0.016) # ~60 FPS
                    else:
                        self._static_frame_count = 0
                        self._last_frame_bytes = jpg
                        
                        # 发送数据
                        packet = CommandExecutor.pack({"action": "video"}, jpg)
                        
                        # 使用传入的回调发送，需要确保是同步还是异步由回调决定
                        # 这里假设 send_func 处理完了会再次 set send_event，或者需要我们自己 set?
                        # 原逻辑是：ws.send_sync(data, done_callback) -> done_callback sets event
                        # 我们修改 send_func 的契约
                        
                        self.send_func(packet, self._on_send_complete)
                        frame_count += 1
                        
                        # 每 500 帧检查一次内存
                        if frame_count % 500 == 0:
                            check_memory_and_gc(threshold_mb=800)
                else:
                    self.send_event.set()
                    time.sleep(0.05)
            else:
                time.sleep(0.005)
                
        gc.collect()

    def _on_send_complete(self):
        self.send_event.set()
