"""
执行器模块 - 处理屏幕捕获和输入模拟
增强版：更好的错误处理和资源管理，支持跨平台降级
"""
import struct
import json
import time
import threading
import gc
import logging
import sys
from typing import Optional, Tuple, Any, Dict

import numpy as np
import cv2

# 平台兼容性模块
from platform_compat import (
    IS_WINDOWS, IS_LINUX, IS_MACOS, platform_info,
    cross_input, cross_camera
)

# 配置日志
logger = logging.getLogger(__name__)

# ================= 平台相关导入 =================

# dxcam: 仅 Windows
try:
    if IS_WINDOWS:
        import dxcam
    else:
        dxcam = None
except ImportError:
    dxcam = None
    if IS_WINDOWS:
        logger.warning("dxcam 未安装，将使用 MSS 作为屏幕捕获后端")

# ctypes/user32: 仅 Windows
try:
    if IS_WINDOWS:
        import ctypes
        user32 = ctypes.windll.user32
        # 设置 VkKeyScanW 参数类型，避免 TypeError: wrong type
        user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
        user32.VkKeyScanW.restype = ctypes.c_short
    else:
        ctypes = None
        user32 = None
except Exception as e:
    logger.error(f"无法加载 user32.dll: {e}")
    user32 = None

# pyperclip: 跨平台（但 Linux 需要 xclip/xsel）
try:
    import pyperclip
except ImportError:
    pyperclip = None
    logger.warning("pyperclip 未安装，剪贴板功能受限")

# ================= 基础配置与键码表 (Windows) =================
VK_MAP = {
    'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D, 'shift': 0x10, 'ctrl': 0x11, 'alt': 0x12,
    'pause': 0x13, 'caps_lock': 0x14, 'esc': 0x1B, 'space': 0x20, 'page_up': 0x21, 'page_down': 0x22,
    'end': 0x23, 'home': 0x24, 'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
    'print_screen': 0x2C, 'ins': 0x2D, 'del': 0x2E,
    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34, '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
    'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74, 'f6': 0x75, 'f7': 0x76, 'f8': 0x77, 'f9': 0x78,
    'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
    'lwin': 0x5B, 'rwin': 0x5C,
}

# MSS Fallback support
try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False
    if IS_WINDOWS:
        logger.warning("MSS module not found, fallback capture disabled")
    else:
        logger.error("MSS module not found! Screen capture will be unavailable on this platform (pip install mss)")

class CameraManager:
    """摄像头管理器，带内存保护和线程安全。支持跨平台降级。"""
    
    MAX_FRAME_PIXELS = 3840 * 2160  # 最大支持4K分辨率
    CLEANUP_INTERVAL = 300  # 5分钟无活动自动清理
    MAX_RETRY_COUNT = 3  # 最大重试次数
    
    def __init__(self):
        self.camera = None
        self.current_idx = -1
        self.lock = threading.RLock()  # 使用可重入锁
        self.last_error_time = 0
        self.last_active_time = time.time()
        self._retry_count = 0
        self._screen_size_cache: Optional[Tuple[int, int]] = None
        
        # 跨平台: 非 Windows 直接使用 mss
        self.use_cross_platform = not IS_WINDOWS
        
        # Fallback state
        self.use_mss = not IS_WINDOWS  # 非 Windows 默认用 mss
        self.mss_sct = None
        self._black_frame_count = 0
        
        # 光标位置缓存 (用于 AI 标注)
        self._last_cursor_ratio: Optional[Tuple[float, float]] = None
        
        if self.use_cross_platform:
            logger.info("CameraManager: 使用跨平台模式 (mss)")
        
    def switch_monitor(self, idx: int) -> bool:
        """
        切换到指定显示器
        """
        with self.lock:
            # 跨平台模式: 使用 cross_camera
            if self.use_cross_platform and cross_camera:
                self.current_idx = idx
                return cross_camera.switch_monitor(idx)
            
            # Check if we should use MSS due to previous failures
            if self.use_mss and HAS_MSS:
                self._init_mss()
                self.current_idx = idx
                return True
                
            if idx == self.current_idx and self.camera is not None:
                return True
                
            self._cleanup_camera()
            
            try:
                if dxcam is None:
                    raise ImportError("dxcam not available")
                self.camera = dxcam.create(output_idx=idx, output_color="BGR")
                self.current_idx = idx
                self._retry_count = 0
                self._update_screen_size_cache()
                logger.info(f"切换到显示器 {idx}")
                return True
            except Exception as e:
                logger.warning(f"无法初始化显示器 {idx}: {e}, 尝试 fallback 到 MSS")
                if HAS_MSS:
                    self.use_mss = True
                    self._init_mss()
                    self.current_idx = idx
                    return True
                return False
                
    def _init_mss(self):
        """Initialize MSS instance"""
        if HAS_MSS and self.mss_sct is None:
            try:
                self.mss_sct = mss.mss()
                self.use_mss = True
                logger.info("Initialized MSS screen capture")
            except Exception as e:
                logger.error(f"Failed to init MSS: {e}")
                
    def _get_mss_frame(self) -> Optional[np.ndarray]:
        """Capture frame using MSS"""
        if not self.mss_sct:
            self._init_mss()
            if not self.mss_sct: return None
            
        try:
            # MSS monitors: 0=All, 1=Primary, etc.
            monitors = self.mss_sct.monitors
            mon_idx = self.current_idx + 1
            if mon_idx >= len(monitors):
                mon_idx = 1
            
            monitor = monitors[mon_idx]
            # Grab frame
            sct_img = self.mss_sct.grab(monitor)
            # Convert directly to numpy array (BGRA)
            img = np.array(sct_img)
            # Drop Alpha channel to get BGR
            return img[:, :, :3]
        except Exception as e:
            logger.error(f"MSS Capture error: {e}")
            return None

    def _update_screen_size_cache(self):
        """更新屏幕尺寸缓存"""
        if self.camera:
            self._screen_size_cache = (self.camera.width, self.camera.height)
        elif self.use_mss and self.mss_sct:
             try:
                 monitors = self.mss_sct.monitors
                 mon_idx = self.current_idx + 1
                 if mon_idx < len(monitors):
                     m = monitors[mon_idx]
                     self._screen_size_cache = (m['width'], m['height'])
             except:
                 self._screen_size_cache = None
        else:
            self._screen_size_cache = None
    
    def get_screen_size(self) -> Tuple[int, int]:
        """获取当前屏幕尺寸"""
        with self.lock:
            if self._screen_size_cache:
                return self._screen_size_cache
            if self.camera:
                return (self.camera.width, self.camera.height)
            if self.use_mss and self.mss_sct:
                # refresh cache logic
                self._update_screen_size_cache()
                if self._screen_size_cache: return self._screen_size_cache
                
        # 回退: 跨平台模式使用 cross_camera 或 cross_input
        if self.use_cross_platform:
            if cross_camera:
                return cross_camera.get_screen_size()
            if cross_input:
                return cross_input.get_screen_size()
        return InputSim.get_screen_size()

    def _cleanup_camera(self):
        """安全清理摄像头资源"""
        if self.camera:
            try:
                self.camera.stop()
            except Exception:
                pass
            try:
                del self.camera
            except Exception:
                pass
            self.camera = None
        
        # Don't cleanup MSS here to persist fallback mode
        # self._screen_size_cache = None 
        # 强制垃圾回收以释放 GPU 资源
        gc.collect()

    def get_raw_frame(self, target_fps: int = 60) -> Optional[np.ndarray]:
        """
        获取原始帧，带错误节流和超时检测
        """
        current_time = time.time()
        
        # 跨平台模式: 使用 cross_camera
        if self.use_cross_platform and cross_camera:
            return cross_camera.get_raw_frame(target_fps)
        
        # Check MSS Fallback
        if self.use_mss:
            return self._get_mss_frame()
            
        # 防止错误死循环导致的过高CPU/内存占用
        if current_time - self.last_error_time < 1:
            return None

        with self.lock:
            try:
                if self.camera is None:
                    if not self.switch_monitor(0):
                        return None
                
                # Double check after switch
                if self.use_mss:
                     return self._get_mss_frame()
                     
                if self.camera is None:
                    return None
                
                if not self.camera.is_capturing:
                    logger.info(f"Starting camera capture with target_fps={target_fps}")
                    self.camera.start(target_fps=target_fps)
                    time.sleep(0.05) # Warmup
                    
                frame = self.camera.get_latest_frame()
                
                # Check if frame is valid
                if frame is None:
                    # Log warning (throttled)
                    if current_time - getattr(self, "_last_none_frame_log", 0) > 5.0:
                        logger.warning("Camera returned None frame")
                        self._last_none_frame_log = current_time
                    
                    self._retry_count += 1
                    if self._retry_count > 10 and HAS_MSS:
                        logger.warning("Too many None frames, switching to MSS")
                        self.use_mss = True
                        self._cleanup_camera()
                        return self._get_mss_frame()
                    return None
                
                # Check for Black Screen (dxcam sometimes captures all zeros)
                # Ensure we don't check every single frame to save CPU, maybe every 30 frames
                # But here we are debugging.
                # Use a lightweight check: check center pixel or mean of small slice
                if self._black_frame_count < 100: # Only check occasionally or if suspicious
                     if np.sum(frame[::100, ::100]) == 0: # Simple sparse check
                         if np.max(frame) == 0: # Full check if sparse check fails
                             self._black_frame_count += 1
                             if self._black_frame_count > 30: # 30 consecutive black frames
                                 logger.warning("Detected continuous black frames from DXCam. Switching to MSS.")
                                 if HAS_MSS:
                                     self.use_mss = True
                                     self._cleanup_camera()
                                     return self._get_mss_frame()
                         else:
                             self._black_frame_count = 0
                     else:
                         self._black_frame_count = 0
                
                # 更新活动时间
                self.last_active_time = current_time
                self._retry_count = 0
                
                # 基本的帧验证
                if frame is not None:
                    h, w = frame.shape[:2]
                    if h * w > self.MAX_FRAME_PIXELS:
                        logger.warning(f"帧尺寸过大: {w}x{h}")
                        return None
                        
                return frame
                
            except Exception as e:
                logger.error(f"截图失败: {e}")
                self.last_error_time = current_time
                self._retry_count += 1
                
                # 超过重试次数后尝试重置
                if self._retry_count >= self.MAX_RETRY_COUNT:
                    if HAS_MSS:
                        logger.warning("DXCam failed repeatedly, switching to MSS")
                        self.use_mss = True
                        self._cleanup_camera()
                        return self._get_mss_frame()
                    else:
                        self._cleanup_camera()
                        self._retry_count = 0
                    
                return None

    @staticmethod
    def _get_cursor_position() -> Optional[Tuple[int, int]]:
        """获取当前鼠标光标位置 (屏幕像素坐标)"""
        if IS_WINDOWS and user32:
            try:
                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
                pt = POINT()
                if user32.GetCursorPos(ctypes.byref(pt)):
                    return (pt.x, pt.y)
            except Exception:
                pass
        # 跨平台降级: 暂不支持，返回 None
        return None

    @staticmethod
    def _draw_cursor_on_frame(frame: np.ndarray, cursor_x: int, cursor_y: int,
                               frame_origin_x: int = 0, frame_origin_y: int = 0) -> np.ndarray:
        """
        在帧上绘制鼠标光标箭头
        
        Args:
            frame: BGR 帧 (numpy array)
            cursor_x: 光标在屏幕上的 X 像素坐标
            cursor_y: 光标在屏幕上的 Y 像素坐标
            frame_origin_x: 帧左上角在屏幕上的 X 偏移 (多显示器)
            frame_origin_y: 帧左上角在屏幕上的 Y 偏移 (多显示器)
            
        Returns:
            绘制了光标的帧
        """
        h, w = frame.shape[:2]
        # 转换为帧内坐标
        fx = cursor_x - frame_origin_x
        fy = cursor_y - frame_origin_y
        
        # 如果光标不在帧范围内，直接返回
        if fx < 0 or fy < 0 or fx >= w or fy >= h:
            return frame
        
        # 光标箭头形状 (相对于光标尖端的偏移列表)
        # 模拟标准 Windows 箭头光标，尺寸约 20x20 像素
        cursor_size = max(12, min(w, h) // 80)  # 自适应大小
        s = cursor_size / 20.0  # 缩放因子
        
        # 箭头多边形顶点 (相对于光标尖端)
        arrow_pts = np.array([
            [0, 0],
            [0, int(20 * s)],
            [int(4 * s), int(16 * s)],
            [int(8 * s), int(24 * s)],
            [int(11 * s), int(23 * s)],
            [int(7 * s), int(15 * s)],
            [int(13 * s), int(15 * s)],
        ], dtype=np.int32)
        
        # 偏移到实际位置
        arrow_pts[:, 0] += fx
        arrow_pts[:, 1] += fy
        
        # 裁剪到帧内
        arrow_pts[:, 0] = np.clip(arrow_pts[:, 0], 0, w - 1)
        arrow_pts[:, 1] = np.clip(arrow_pts[:, 1], 0, h - 1)
        
        # 绘制: 先黑色描边，再白色填充
        cv2.fillPoly(frame, [arrow_pts], (0, 0, 0))  # 黑色边框
        # 稍微缩小的内部白色填充
        inner_pts = np.array([
            [0, int(1 * s)],
            [0, int(18 * s)],
            [int(4 * s), int(15 * s)],
            [int(7 * s), int(22 * s)],
            [int(9 * s), int(21 * s)],
            [int(6 * s), int(14 * s)],
            [int(11 * s), int(14 * s)],
        ], dtype=np.int32)
        inner_pts[:, 0] += fx
        inner_pts[:, 1] += fy
        inner_pts[:, 0] = np.clip(inner_pts[:, 0], 0, w - 1)
        inner_pts[:, 1] = np.clip(inner_pts[:, 1], 0, h - 1)
        cv2.fillPoly(frame, [inner_pts], (255, 255, 255))  # 白色填充
        
        return frame

    def get_jpeg_bytes(self, target_width: int = 1280, quality: int = 50) -> Optional[bytes]:
        """
        获取 JPEG 编码的帧，带尺寸保护
        截图后会在帧上绘制鼠标光标
        
        Args:
            target_width: 目标宽度
            quality: JPEG 质量 (1-100)
            
        Returns:
            JPEG 字节数据或 None
        """
        frame_bgr = self.get_raw_frame()
        if frame_bgr is None:
            return None
            
        try:
            h, w = frame_bgr.shape[:2]
            
            # 限制最大尺寸以防内存溢出
            if w > 3840 or h > 2160:
                logger.warning(f"帧尺寸超限: {w}x{h}")
                return None
            
            # ===== 绘制鼠标光标 =====
            cursor_pos = self._get_cursor_position()
            if cursor_pos:
                # 获取帧的屏幕偏移 (多显示器支持)
                origin_x, origin_y = 0, 0
                if self.use_mss and self.mss_sct:
                    try:
                        monitors = self.mss_sct.monitors
                        mon_idx = self.current_idx + 1
                        if mon_idx < len(monitors):
                            origin_x = monitors[mon_idx].get('left', 0)
                            origin_y = monitors[mon_idx].get('top', 0)
                    except Exception:
                        pass
                
                frame_bgr = self._draw_cursor_on_frame(
                    frame_bgr, cursor_pos[0], cursor_pos[1],
                    origin_x, origin_y
                )
                
                # 保存光标在帧中的比例位置 (供 AI 标注使用)
                fx = cursor_pos[0] - origin_x
                fy = cursor_pos[1] - origin_y
                if w > 0 and h > 0:
                    self._last_cursor_ratio = (fx / w, fy / h)
                else:
                    self._last_cursor_ratio = None
            else:
                self._last_cursor_ratio = None
            
            if w > target_width:
                scale = target_width / w
                new_w = int(w * scale)
                new_h = int(h * scale)
                # 使用 INTER_LINEAR 以提高速度
                frame_bgr = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            
            # 限制质量范围
            quality = max(1, min(100, quality))
            ret, jpg_data = cv2.imencode('.jpg', frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            
            if ret:
                return jpg_data.tobytes()
                
        except Exception as e:
            logger.error(f"OpenCV编码错误: {e}")
            
        return None
    
    @staticmethod
    def draw_grid_overlay(jpg_bytes: bytes, quality: int = 75) -> Optional[bytes]:
        """
        在 JPEG 截图上绘制 10x10 半透明网格线和坐标轴标签 (0.1, 0.2, ..., 0.9)
        帮助 AI 更准确地理解屏幕坐标位置
        
        Args:
            jpg_bytes: 原始 JPEG 字节
            quality: 重编码 JPEG 质量
            
        Returns:
            带网格的 JPEG 字节
        """
        try:
            jpg_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(jpg_arr, cv2.IMREAD_COLOR)
            if frame is None:
                return jpg_bytes
            
            h, w = frame.shape[:2]
            
            # ---- 第一步: 绘制半透明网格线 ----
            overlay = frame.copy()
            
            grid_color = (0, 200, 200)  # 亮青色网格线 (高对比度)
            line_thickness = 2  # 2px 线宽，更清晰
            alpha = 0.5  # 50% 透明度
            
            for i in range(1, 10):
                ratio = i / 10.0
                x = int(w * ratio)
                y = int(h * ratio)
                cv2.line(overlay, (x, 0), (x, h), grid_color, line_thickness)
                cv2.line(overlay, (0, y), (w, y), grid_color, line_thickness)
            
            # 混合网格线 (半透明)
            cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
            
            # ---- 第二步: 在混合后的 frame 上直接绘制坐标标签 (全不透明) ----
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = max(0.45, min(w, h) / 1800.0)
            thickness = max(1, int(font_scale * 2))
            label_color = (0, 255, 255)  # 亮黄色标签 (BGR)
            label_bg_color = (0, 0, 0)  # 黑色背景
            
            for i in range(1, 10):
                ratio = i / 10.0
                label = f"{ratio:.1f}"
                (tw, th_text), baseline = cv2.getTextSize(label, font, font_scale, thickness)
                
                # X 轴标签 (顶部)
                x = int(w * ratio)
                lx = x - tw // 2
                ly = th_text + 6
                pad = 3
                cv2.rectangle(frame, (lx - pad, ly - th_text - pad), (lx + tw + pad, ly + baseline + pad), label_bg_color, -1)
                cv2.putText(frame, label, (lx, ly), font, font_scale, label_color, thickness, cv2.LINE_AA)
                
                # Y 轴标签 (左侧)
                y = int(h * ratio)
                ly2 = y + th_text // 2
                cv2.rectangle(frame, (1, ly2 - th_text - pad), (tw + pad * 2 + 2, ly2 + baseline + pad), label_bg_color, -1)
                cv2.putText(frame, label, (pad + 1, ly2), font, font_scale, label_color, thickness, cv2.LINE_AA)
            
            # 重新编码
            quality = max(1, min(100, quality))
            ret, jpg_data = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ret:
                return jpg_data.tobytes()
                
        except Exception as e:
            logger.error(f"网格绘制失败: {e}")
        
        return jpg_bytes  # 失败时返回原图
    
    def annotate_jpeg_with_cursor_label(self, jpg_bytes: bytes, quality: int = 50) -> Optional[bytes]:
        """
        在已有的 JPEG 截图上添加鼠标光标文字标注 "(此处为鼠标)"
        用于发送给 AI，防止某些厂商将图像转为文本时丢失鼠标位置信息
        
        Args:
            jpg_bytes: 原始 JPEG 字节
            quality: 重编码 JPEG 质量
            
        Returns:
            带标注的 JPEG 字节，或在无法标注时返回原始字节
        """
        if not self._last_cursor_ratio:
            return jpg_bytes  # 没有光标位置信息，直接返回原图
        
        try:
            # 解码 JPEG
            jpg_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(jpg_arr, cv2.IMREAD_COLOR)
            if frame is None:
                return jpg_bytes
            
            fh, fw = frame.shape[:2]
            # 还原光标在当前帧中的像素坐标
            cx = int(self._last_cursor_ratio[0] * fw)
            cy = int(self._last_cursor_ratio[1] * fh)
            
            # 确保在帧范围内
            if cx < 0 or cy < 0 or cx >= fw or cy >= fh:
                return jpg_bytes
            
            # 绘制文字标注
            label = "(mouse here)"
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = max(0.4, min(fw, fh) / 1500.0)  # 自适应字体大小
            thickness = max(1, int(font_scale * 2))
            
            # 计算文字尺寸
            (text_w, text_h), baseline = cv2.getTextSize(label, font, font_scale, thickness)
            
            # 文字放置位置: 光标左上方
            tx = cx - text_w - 5
            ty = cy - 5
            
            # 边界调整: 如果超出左边界，放到光标右侧
            if tx < 0:
                tx = cx + 15
            # 如果超出上边界，放到光标下方
            if ty - text_h < 0:
                ty = cy + text_h + 15
            # 如果超出右边界
            if tx + text_w >= fw:
                tx = fw - text_w - 5
            
            # 绘制背景矩形 (半透明效果通过实色背景模拟)
            pad = 3
            cv2.rectangle(frame,
                          (tx - pad, ty - text_h - pad),
                          (tx + text_w + pad, ty + baseline + pad),
                          (0, 0, 0), -1)  # 黑色背景
            
            # 绘制文字 (亮绿色，醒目)
            cv2.putText(frame, label, (tx, ty),
                        font, font_scale, (0, 255, 0), thickness, cv2.LINE_AA)
            
            # 重新编码
            quality = max(1, min(100, quality))
            ret, jpg_data = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ret:
                return jpg_data.tobytes()
                
        except Exception as e:
            logger.error(f"AI 光标标注失败: {e}")
        
        return jpg_bytes  # 失败时返回原图

    def cleanup(self):
        """完全清理资源"""
        with self.lock:
            self._cleanup_camera()


# ================= 输入模拟器 =================
class InputSim:
    """输入模拟器 - 处理鼠标和键盘输入。支持跨平台降级。"""
    
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    KEYEVENTF_KEYUP = 0x0002
    VK_SHIFT = 0x10
    VK_CONTROL = 0x11
    VK_MENU = 0x12

    @staticmethod
    def get_screen_size() -> Tuple[int, int]:
        """获取屏幕尺寸"""
        if user32:
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        # 跨平台降级
        if cross_input:
            return cross_input.get_screen_size()
        return (1920, 1080)  # 默认值

    @staticmethod
    def move(x: float, y: float, screen_w: Optional[int] = None, screen_h: Optional[int] = None):
        """
        移动鼠标到指定位置
        
        Args:
            x: X 坐标（像素）
            y: Y 坐标（像素）
            screen_w: 屏幕宽度
            screen_h: 屏幕高度
        """
        # 跨平台降级
        if not user32 and cross_input:
            cross_input.move(x, y, screen_w, screen_h)
            return
        if not user32:
            return
            
        if screen_w is None or screen_h is None:
            screen_w, screen_h = InputSim.get_screen_size()
            
        if screen_w == 0 or screen_h == 0:
            return
            
        # 转换为绝对坐标 (0-65535)
        fx = int(x * 65535 / screen_w)
        fy = int(y * 65535 / screen_h)
        
        # 限制范围
        fx = max(0, min(65535, fx))
        fy = max(0, min(65535, fy))
        
        user32.mouse_event(InputSim.MOUSEEVENTF_ABSOLUTE | InputSim.MOUSEEVENTF_MOVE, fx, fy, 0, 0)

    @staticmethod
    def click(x: float, y: float, btn: str, action: str, w: int, h: int):
        """
        执行鼠标点击
        
        Args:
            x: X 坐标
            y: Y 坐标
            btn: 按钮 ('left', 'right', 'middle')
            action: 动作 ('down', 'up', 'click')
            w: 屏幕宽度
            h: 屏幕高度
        """
        # 跨平台降级
        if not user32 and cross_input:
            cross_input.click(x, y, btn, action, w, h)
            return
        if not user32:
            return
            
        InputSim.move(x, y, w, h)
        
        flags = 0
        btn = btn.lower() if btn else 'left'
        action = action.lower() if action else 'click'
        
        if btn == 'left':
            if action == 'down':
                flags = InputSim.MOUSEEVENTF_LEFTDOWN
            elif action == 'up':
                flags = InputSim.MOUSEEVENTF_LEFTUP
            else:
                flags = InputSim.MOUSEEVENTF_LEFTDOWN | InputSim.MOUSEEVENTF_LEFTUP
        elif btn == 'right':
            if action == 'down':
                flags = InputSim.MOUSEEVENTF_RIGHTDOWN
            elif action == 'up':
                flags = InputSim.MOUSEEVENTF_RIGHTUP
            else:
                flags = InputSim.MOUSEEVENTF_RIGHTDOWN | InputSim.MOUSEEVENTF_RIGHTUP
        elif btn == 'middle':
            if action == 'down':
                flags = InputSim.MOUSEEVENTF_MIDDLEDOWN
            elif action == 'up':
                flags = InputSim.MOUSEEVENTF_MIDDLEUP
            else:
                flags = InputSim.MOUSEEVENTF_MIDDLEDOWN | InputSim.MOUSEEVENTF_MIDDLEUP
                
        if flags:
            user32.mouse_event(flags, 0, 0, 0, 0)
        
    @staticmethod
    def double_click(x: float, y: float, btn: str, w: int, h: int):
        """执行双击"""
        # 跨平台降级
        if not user32 and cross_input:
            cross_input.double_click(x, y, btn, w, h)
            return
        InputSim.click(x, y, btn, 'click', w, h)
        time.sleep(0.05)
        InputSim.click(x, y, btn, 'click', w, h)

    @staticmethod
    def scroll(clicks: int):
        """滚动鼠标滚轮"""
        if user32:
            user32.mouse_event(InputSim.MOUSEEVENTF_WHEEL, 0, 0, int(clicks), 0)
        elif cross_input:
            cross_input.scroll(clicks)
    
    @staticmethod
    def _press_key(vk_code: int):
        """按下按键"""
        if user32:
            user32.keybd_event(vk_code, 0, 0, 0)
        # 跨平台: _press_key 不单独降级，由 press_sequence 统一处理
            
    @staticmethod
    def _release_key(vk_code: int):
        """释放按键"""
        if user32:
            user32.keybd_event(vk_code, 0, InputSim.KEYEVENTF_KEYUP, 0)
        # 跨平台: _release_key 不单独降级，由 press_sequence 统一处理

    @staticmethod
    def send_char_safe(char: str):
        """安全发送单个字符"""
        if not user32:
            return
            
        res = user32.VkKeyScanW(char) # VkKeyScanW expects a WCHAR, not an int
        if res == -1:
            return
            
        vk_code = res & 0xff
        shift_state = (res >> 8) & 0xff
        
        if shift_state & 1:
            InputSim._press_key(InputSim.VK_SHIFT)
        if shift_state & 2:
            InputSim._press_key(InputSim.VK_CONTROL)
        if shift_state & 4:
            InputSim._press_key(InputSim.VK_MENU)
            
        InputSim._press_key(vk_code)
        InputSim._release_key(vk_code)
        
        if shift_state & 4:
            InputSim._release_key(InputSim.VK_MENU)
        if shift_state & 2:
            InputSim._release_key(InputSim.VK_CONTROL)
        if shift_state & 1:
            InputSim._release_key(InputSim.VK_SHIFT)

    @staticmethod
    def paste_text(text: str):
        """通过剪贴板粘贴文本"""
        # 跨平台降级
        if not user32 and cross_input:
            cross_input.paste_text(text)
            return
        
        if not pyperclip:
            logger.warning("pyperclip 未安装，无法粘贴")
            return
        
        original = ""
        try:
            original = pyperclip.paste()
        except Exception:
            pass
            
        try:
            pyperclip.copy(text)
            InputSim._press_key(InputSim.VK_CONTROL)
            InputSim._press_key(ord('V'))
            time.sleep(0.02)
            InputSim._release_key(ord('V'))
            InputSim._release_key(InputSim.VK_CONTROL)
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"粘贴失败: {e}")
        finally:
            try:
                if original:
                    pyperclip.copy(original)
            except Exception:
                pass

    @staticmethod
    def write_text(text: str):
        """输入文本（ASCII 逐字符，其他使用粘贴）"""
        if not text:
            return
        
        # 跨平台降级
        if not user32 and cross_input:
            cross_input.write_text(text)
            return
            
        is_ascii = all(ord(c) < 128 for c in text)
        if is_ascii:
            for char in text:
                InputSim.send_char_safe(char)
                time.sleep(0.002)
        else:
            InputSim.paste_text(text)

    @staticmethod
    def press_sequence(keys: list):
        """按下组合键序列"""
        if not keys:
            return
        
        # 跨平台降级
        if not user32 and cross_input:
            cross_input.press_sequence(keys)
            return
            
        vks = []
        for k in keys:
            k = str(k).lower()
            if k in VK_MAP:
                vks.append(VK_MAP[k])
            elif len(k) == 1:
                vks.append(ord(k.upper()))
                
        for vk in vks:
            InputSim._press_key(vk)
            
        time.sleep(0.05)
        
        for vk in reversed(vks):
            InputSim._release_key(vk)

    @staticmethod
    def activate_window(title_part: str):
        """激活窗口 (部分匹配标题)"""
        # 跨平台降级
        if not user32:
            if cross_input:
                cross_input.activate_window(title_part)
            return
        
        title_part = title_part.lower()
        
        def enum_window_callback(hwnd, lParam):
            if not user32.IsWindowVisible(hwnd):
                return 1
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return 1
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            if title_part in buff.value.lower():
                try:
                    # 尝试恢复最小化的窗口
                    if user32.IsIconic(hwnd):
                        user32.ShowWindow(hwnd, 9) # SW_RESTORE
                    
                    # 尝试置顶
                    user32.SetForegroundWindow(hwnd)
                except:
                    pass
                return 0 # Stop
            return 1 # Continue

        # WINFUNCTYPE(return_type, arg_types...)
        CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        try:
            user32.EnumWindows(CMPFUNC(enum_window_callback), 0)
        except Exception as e:
            logger.error(f"EnumWindows error: {e}")


# ================= 执行门面 =================
class CommandExecutor:
    """命令执行器 - 统一处理输入命令"""
    
    def __init__(self):
        self.cam_manager = CameraManager()
        
    def handle_input(self, action: str, params: Dict[str, Any]):
        """
        处理输入命令
        
        坐标处理：
        - 如果 x/y 是 float 且 <= 1.0，视为比例值
        - 否则视为像素值
        
        Args:
            action: 动作类型
            params: 参数字典
        """
        try:
            # 获取屏幕尺寸
            if self.cam_manager.camera:
                w, h = self.cam_manager.camera.width, self.cam_manager.camera.height
            else:
                w, h = InputSim.get_screen_size()
                
            # 处理坐标 - 支持比例和像素两种模式
            x_in = params.get('x')
            y_in = params.get('y')
            
            # 转换坐标
            x = self._convert_coordinate(x_in, w)
            y = self._convert_coordinate(y_in, h)
            
            # 限制坐标范围
            if x is not None:
                x = max(0, min(x, w))
            if y is not None:
                y = max(0, min(y, h))
                
            btn = params.get('button', 'left')
            
            # 执行动作
            if action == "move_mouse":
                if x is not None and y is not None:
                    InputSim.move(x, y, w, h)
            elif action == "mouse_down":
                if x is not None and y is not None:
                    InputSim.click(x, y, btn, 'down', w, h)
            elif action == "mouse_up":
                if x is not None and y is not None:
                    InputSim.click(x, y, btn, 'up', w, h)
            elif action == "click_mouse":
                if x is not None and y is not None:
                    InputSim.click(x, y, btn, 'click', w, h)
            elif action == "double_click":
                if x is not None and y is not None:
                    InputSim.double_click(x, y, btn, w, h)
            elif action == "scroll_mouse":
                clicks = params.get('clicks', 0)
                InputSim.scroll(int(clicks))
            elif action == "write_keyboard":
                text = params.get('key', '')
                if text:
                    InputSim.write_text(text)
            elif action == "keyPress_keyboard":
                keys = params.get('key_list', [])
                InputSim.press_sequence(keys)
            elif action == "activate_window":
                title = params.get('title', '')
                if title:
                    InputSim.activate_window(title)
            else:
                logger.warning(f"未知动作类型: {action}")
                
        except Exception as e:
            logger.error(f"执行动作错误 [{action}]: {e}")
    
    def _convert_coordinate(self, value: Any, max_val: int) -> Optional[int]:
        """
        转换坐标值
        
        Args:
            value: 输入值（可能是比例或像素）
            max_val: 最大值（屏幕宽度或高度）
            
        Returns:
            像素值或 None
        """
        if value is None:
            return None
            
        try:
            if isinstance(value, float) and 0.0 <= value <= 1.0:
                # 比例值 -> 像素值
                return int(value * max_val)
            else:
                # 像素值
                return int(value)
        except (TypeError, ValueError):
            return None

    def switch_monitor(self, idx: int) -> bool:
        """切换显示器"""
        return self.cam_manager.switch_monitor(idx)

    def get_screenshot(self, target_width: int = 1280) -> Optional[bytes]:
        """获取屏幕截图 JPEG (带光标箭头)"""
        return self.cam_manager.get_jpeg_bytes(target_width=target_width)
    
    def get_screenshot_for_ai(self, target_width: int = 1280) -> Optional[bytes]:
        """获取用于 AI 分析的屏幕截图 JPEG (带光标标注 + 坐标网格)"""
        jpg = self.cam_manager.get_jpeg_bytes(target_width=target_width)
        if jpg:
            # 1. 添加鼠标光标标注
            jpg = self.cam_manager.annotate_jpeg_with_cursor_label(jpg)
            # 2. 添加坐标网格线
            if jpg:
                jpg = CameraManager.draw_grid_overlay(jpg)
            return jpg
        return None
    
    def get_raw_frame(self) -> Optional[np.ndarray]:
        """获取原始帧"""
        return self.cam_manager.get_raw_frame()
    
    def get_screen_size(self) -> Tuple[int, int]:
        """获取屏幕尺寸"""
        return self.cam_manager.get_screen_size()
    
    def ensure_camera_started(self):
        """确保摄像头已初始化并开始捕获，供P2P模式使用"""
        # 跨平台模式: mss 不需要 start/stop，直接确保 switch_monitor 已调用
        if self.cam_manager.use_cross_platform:
            if self.cam_manager.current_idx < 0:
                self.cam_manager.switch_monitor(0)
            return
        
        if self.cam_manager.camera is None:
            self.cam_manager.switch_monitor(0)
        if self.cam_manager.camera and not self.cam_manager.camera.is_capturing:
            try:
                self.cam_manager.camera.start(target_fps=60)
            except Exception as e:
                logger.error(f"启动摄像头失败: {e}")
    
    def cleanup(self):
        """清理资源"""
        self.cam_manager.cleanup()

    @staticmethod
    def pack(header: dict, data: bytes = b"") -> bytes:
        """
        打包消息
        
        Args:
            header: 消息头字典
            data: 消息体字节
            
        Returns:
            打包后的字节数据
        """
        header["date_len"] = len(data)
        j_bytes = json.dumps(header, ensure_ascii=False).encode("utf-8")
        return struct.pack('!I', len(j_bytes)) + j_bytes + data
