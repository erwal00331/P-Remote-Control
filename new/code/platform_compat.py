"""
平台兼容性模块
自动检测运行环境，为非 Windows 系统提供降级方案

支持的平台:
- Windows: 完整功能 (dxcam + user32 API)
- Linux: 降级模式 (mss + pynput/xdotool)
- macOS: 降级模式 (mss + pynput)
"""
import os
import sys
import platform
import subprocess
import shutil
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)

# ================= 平台检测 =================

PLATFORM = platform.system().lower()  # 'windows', 'linux', 'darwin'
IS_WINDOWS = PLATFORM == 'windows'
IS_LINUX = PLATFORM == 'linux'
IS_MACOS = PLATFORM == 'darwin'
ARCH = platform.machine()  # 'x86_64', 'AMD64', 'aarch64', 'arm64'


class PlatformInfo:
    """平台信息收集器"""
    
    def __init__(self):
        self.platform = PLATFORM
        self.is_windows = IS_WINDOWS
        self.is_linux = IS_LINUX
        self.is_macos = IS_MACOS
        self.arch = ARCH
        self.python_version = platform.python_version()
        self.has_display = self._check_display()
        
        # 可用功能检测
        self.features: Dict[str, bool] = {}
        self.degraded_features: List[str] = []
        self.missing_features: List[str] = []
        self._detect_features()
    
    def _check_display(self) -> bool:
        """检测是否有显示器/图形界面"""
        if IS_WINDOWS:
            return True
        if IS_MACOS:
            return True  # macOS 通常有图形界面
        # Linux: 检查 DISPLAY 或 WAYLAND_DISPLAY 环境变量
        return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
    
    def _detect_features(self):
        """检测各项功能的可用性"""
        # 屏幕捕获
        self.features['dxcam'] = self._check_module('dxcam') and IS_WINDOWS
        self.features['mss'] = self._check_module('mss')
        self.features['screen_capture'] = self.features['dxcam'] or self.features['mss']
        
        # 输入模拟
        self.features['user32'] = IS_WINDOWS and self._check_user32()
        self.features['pynput'] = self._check_module('pynput')
        self.features['xdotool'] = IS_LINUX and self._check_command('xdotool')
        self.features['input_sim'] = self.features['user32'] or self.features['pynput'] or self.features['xdotool']
        
        # 剪贴板
        self.features['pyperclip'] = self._check_module('pyperclip')
        self.features['xclip'] = IS_LINUX and self._check_command('xclip')
        self.features['xsel'] = IS_LINUX and self._check_command('xsel')
        self.features['clipboard'] = self.features['pyperclip'] or self.features['xclip'] or self.features['xsel']
        
        # 图像处理 (跨平台)
        self.features['opencv'] = self._check_module('cv2')
        self.features['numpy'] = self._check_module('numpy')
        
        # 网络 (跨平台)
        self.features['uvicorn'] = self._check_module('uvicorn')
        self.features['aiortc'] = self._check_module('aiortc')
        
        # 构建降级/缺失列表
        if not IS_WINDOWS:
            if not self.features['dxcam']:
                self.degraded_features.append('屏幕捕获: dxcam 不可用，使用 mss 替代')
            if not self.features['user32']:
                if self.features['pynput']:
                    self.degraded_features.append('输入模拟: user32 不可用，使用 pynput 替代')
                elif self.features['xdotool']:
                    self.degraded_features.append('输入模拟: user32 不可用，使用 xdotool 替代')
                else:
                    self.missing_features.append('输入模拟: 无可用后端 (请安装 pynput 或 xdotool)')
            if not self.has_display:
                self.missing_features.append('无图形界面: 屏幕捕获和输入模拟不可用')
        
        if not self.features['screen_capture']:
            self.missing_features.append('屏幕捕获: 无可用后端 (请安装 mss: pip install mss)')
        if not self.features['opencv']:
            self.missing_features.append('OpenCV: 未安装 (pip install opencv-python)')
        if not self.features['numpy']:
            self.missing_features.append('NumPy: 未安装 (pip install numpy)')
    
    def _check_module(self, module_name: str) -> bool:
        """检测 Python 模块是否可用"""
        try:
            __import__(module_name)
            return True
        except ImportError:
            return False
    
    def _check_user32(self) -> bool:
        """检测 user32.dll 是否可用"""
        try:
            import ctypes
            ctypes.windll.user32
            return True
        except (AttributeError, OSError):
            return False
    
    def _check_command(self, cmd: str) -> bool:
        """检测系统命令是否可用"""
        return shutil.which(cmd) is not None
    
    def print_report(self):
        """打印平台检测报告"""
        logger.info("=" * 50)
        logger.info("  平台环境检测报告")
        logger.info("=" * 50)
        logger.info(f"  操作系统: {platform.system()} {platform.release()}")
        logger.info(f"  架构: {self.arch}")
        logger.info(f"  Python: {self.python_version}")
        logger.info(f"  图形界面: {'✓ 可用' if self.has_display else '✗ 不可用'}")
        logger.info("-" * 50)
        
        if IS_WINDOWS:
            logger.info("  ✓ Windows 环境 - 完整功能模式")
        else:
            logger.info(f"  ⚠ {platform.system()} 环境 - 降级模式")
        
        if self.degraded_features:
            logger.info("-" * 50)
            logger.warning("  降级的功能:")
            for f in self.degraded_features:
                logger.warning(f"    ⚠ {f}")
        
        if self.missing_features:
            logger.info("-" * 50)
            logger.error("  不可用的功能:")
            for f in self.missing_features:
                logger.error(f"    ✗ {f}")
        
        logger.info("=" * 50)
    
    def to_dict(self) -> dict:
        """返回平台信息字典 (用于 API 响应)"""
        return {
            "platform": self.platform,
            "arch": self.arch,
            "python_version": self.python_version,
            "has_display": self.has_display,
            "is_degraded": not IS_WINDOWS,
            "features": self.features,
            "degraded_features": self.degraded_features,
            "missing_features": self.missing_features,
        }


# ================= 跨平台输入模拟器 =================

class CrossPlatformInputSim:
    """
    跨平台输入模拟器
    在 Windows 上使用 user32 API，在 Linux/macOS 上使用 pynput 或 xdotool
    """
    
    # 通用键码映射 (用于 pynput 后端)
    KEY_MAP = {
        'backspace': 'BackSpace', 'tab': 'Tab', 'enter': 'Return',
        'shift': 'Shift_L', 'ctrl': 'Control_L', 'alt': 'Alt_L',
        'esc': 'Escape', 'space': 'space', 'del': 'Delete',
        'caps_lock': 'Caps_Lock', 'page_up': 'Prior', 'page_down': 'Next',
        'end': 'End', 'home': 'Home',
        'left': 'Left', 'up': 'Up', 'right': 'Right', 'down': 'Down',
        'ins': 'Insert', 'print_screen': 'Print',
        'f1': 'F1', 'f2': 'F2', 'f3': 'F3', 'f4': 'F4',
        'f5': 'F5', 'f6': 'F6', 'f7': 'F7', 'f8': 'F8',
        'f9': 'F9', 'f10': 'F10', 'f11': 'F11', 'f12': 'F12',
        'lwin': 'Super_L', 'rwin': 'Super_R',
    }

    def __init__(self):
        self._backend = None  # 'pynput', 'xdotool', or None
        self._mouse = None
        self._keyboard = None
        self._screen_size_cache = None
        
        if IS_WINDOWS:
            # Windows 不使用此类，使用原始 InputSim
            return
        
        # 尝试 pynput
        try:
            from pynput.mouse import Controller as MouseController, Button
            from pynput.keyboard import Controller as KeyboardController, Key
            self._mouse = MouseController()
            self._keyboard = KeyboardController()
            self._backend = 'pynput'
            self._pynput_button = Button
            self._pynput_key = Key
            logger.info("输入模拟后端: pynput")
        except (ImportError, Exception) as e:
            logger.warning(f"pynput 不可用: {e}")
            
            # 尝试 xdotool (仅 Linux)
            if IS_LINUX and shutil.which('xdotool'):
                self._backend = 'xdotool'
                logger.info("输入模拟后端: xdotool")
            else:
                logger.error("无可用的输入模拟后端！输入功能将被禁用。")
    
    def get_screen_size(self) -> Tuple[int, int]:
        """获取屏幕尺寸"""
        if self._screen_size_cache:
            return self._screen_size_cache
        
        try:
            if IS_LINUX:
                result = subprocess.run(
                    ['xdpyinfo'], capture_output=True, text=True, timeout=3
                )
                for line in result.stdout.split('\n'):
                    if 'dimensions:' in line:
                        dims = line.split(':')[1].strip().split(' ')[0]
                        w, h = dims.split('x')
                        self._screen_size_cache = (int(w), int(h))
                        return self._screen_size_cache
            elif IS_MACOS:
                result = subprocess.run(
                    ['system_profiler', 'SPDisplaysDataType'],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.split('\n'):
                    if 'Resolution:' in line:
                        parts = line.split(':')[1].strip().split(' x ')
                        if len(parts) >= 2:
                            w = int(parts[0].strip())
                            h = int(parts[1].strip().split(' ')[0])
                            self._screen_size_cache = (w, h)
                            return self._screen_size_cache
        except Exception as e:
            logger.warning(f"获取屏幕尺寸失败: {e}")
        
        # 如果有 mss 可以尝试从 mss 获取
        try:
            import mss
            with mss.mss() as sct:
                m = sct.monitors[1]  # 主显示器
                self._screen_size_cache = (m['width'], m['height'])
                return self._screen_size_cache
        except Exception:
            pass
        
        return (1920, 1080)  # 默认值
    
    def move(self, x: float, y: float, screen_w: Optional[int] = None, screen_h: Optional[int] = None):
        """移动鼠标"""
        if self._backend == 'pynput' and self._mouse:
            self._mouse.position = (int(x), int(y))
        elif self._backend == 'xdotool':
            self._xdotool_run(['mousemove', '--', str(int(x)), str(int(y))])
    
    def click(self, x: float, y: float, btn: str, action: str, w: int, h: int):
        """执行鼠标点击"""
        self.move(x, y, w, h)
        
        if self._backend == 'pynput' and self._mouse:
            button = {
                'left': self._pynput_button.left,
                'right': self._pynput_button.right,
                'middle': self._pynput_button.middle,
            }.get(btn.lower(), self._pynput_button.left)
            
            action = action.lower() if action else 'click'
            if action == 'down':
                self._mouse.press(button)
            elif action == 'up':
                self._mouse.release(button)
            else:
                self._mouse.click(button)
                
        elif self._backend == 'xdotool':
            btn_map = {'left': '1', 'right': '3', 'middle': '2'}
            btn_num = btn_map.get(btn.lower(), '1')
            action = action.lower() if action else 'click'
            if action == 'down':
                self._xdotool_run(['mousedown', btn_num])
            elif action == 'up':
                self._xdotool_run(['mouseup', btn_num])
            else:
                self._xdotool_run(['click', btn_num])
    
    def double_click(self, x: float, y: float, btn: str, w: int, h: int):
        """双击"""
        if self._backend == 'pynput' and self._mouse:
            self.move(x, y, w, h)
            button = {
                'left': self._pynput_button.left,
                'right': self._pynput_button.right,
                'middle': self._pynput_button.middle,
            }.get(btn.lower(), self._pynput_button.left)
            self._mouse.click(button, 2)
        elif self._backend == 'xdotool':
            self.move(x, y, w, h)
            btn_map = {'left': '1', 'right': '3', 'middle': '2'}
            btn_num = btn_map.get(btn.lower(), '1')
            self._xdotool_run(['click', '--repeat', '2', btn_num])
        else:
            import time
            self.click(x, y, btn, 'click', w, h)
            time.sleep(0.05)
            self.click(x, y, btn, 'click', w, h)
    
    def scroll(self, clicks: int):
        """滚动鼠标"""
        if self._backend == 'pynput' and self._mouse:
            self._mouse.scroll(0, int(clicks))
        elif self._backend == 'xdotool':
            if clicks > 0:
                self._xdotool_run(['click', '--repeat', str(abs(int(clicks))), '4'])
            else:
                self._xdotool_run(['click', '--repeat', str(abs(int(clicks))), '5'])
    
    def write_text(self, text: str):
        """输入文本"""
        if not text:
            return
        
        if self._backend == 'pynput' and self._keyboard:
            self._keyboard.type(text)
        elif self._backend == 'xdotool':
            self._xdotool_run(['type', '--delay', '5', '--', text])
    
    def press_sequence(self, keys: list):
        """按下组合键"""
        if not keys:
            return
        
        if self._backend == 'pynput' and self._keyboard:
            pressed = []
            for k in keys:
                k = str(k).lower()
                pynput_key = self._resolve_pynput_key(k)
                if pynput_key:
                    self._keyboard.press(pynput_key)
                    pressed.append(pynput_key)
            
            import time
            time.sleep(0.05)
            
            for pk in reversed(pressed):
                self._keyboard.release(pk)
                
        elif self._backend == 'xdotool':
            # xdotool 风格: key ctrl+c
            xkeys = [self._resolve_xdotool_key(str(k).lower()) for k in keys]
            combo = '+'.join(xkeys)
            self._xdotool_run(['key', combo])
    
    def paste_text(self, text: str):
        """通过剪贴板粘贴文本"""
        try:
            import pyperclip
            original = ""
            try:
                original = pyperclip.paste()
            except Exception:
                pass
            
            pyperclip.copy(text)
            self.press_sequence(['ctrl', 'v'])
            
            import time
            time.sleep(0.1)
            
            if original:
                try:
                    pyperclip.copy(original)
                except Exception:
                    pass
        except ImportError:
            # 直接输入
            self.write_text(text)
    
    def activate_window(self, title_part: str):
        """激活窗口"""
        if self._backend == 'xdotool':
            try:
                result = subprocess.run(
                    ['xdotool', 'search', '--name', title_part],
                    capture_output=True, text=True, timeout=3
                )
                windows = result.stdout.strip().split('\n')
                if windows and windows[0]:
                    self._xdotool_run(['windowactivate', windows[0]])
            except Exception as e:
                logger.error(f"激活窗口失败: {e}")
        elif IS_MACOS:
            try:
                subprocess.run([
                    'osascript', '-e',
                    f'tell application "System Events" to set frontmost of every process whose name contains "{title_part}" to true'
                ], timeout=5)
            except Exception as e:
                logger.error(f"激活窗口失败: {e}")
    
    def _resolve_pynput_key(self, key_name: str):
        """将键名解析为 pynput Key"""
        from pynput.keyboard import Key
        key_map = {
            'ctrl': Key.ctrl_l, 'shift': Key.shift_l, 'alt': Key.alt_l,
            'enter': Key.enter, 'tab': Key.tab, 'esc': Key.esc,
            'space': Key.space, 'backspace': Key.backspace,
            'del': Key.delete, 'ins': Key.insert,
            'home': Key.home, 'end': Key.end,
            'page_up': Key.page_up, 'page_down': Key.page_down,
            'left': Key.left, 'up': Key.up, 'right': Key.right, 'down': Key.down,
            'caps_lock': Key.caps_lock,
            'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
            'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
            'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
            'lwin': Key.cmd_l, 'rwin': Key.cmd_r,
        }
        if key_name in key_map:
            return key_map[key_name]
        if len(key_name) == 1:
            return key_name  # pynput 可以直接用字符
        return None
    
    def _resolve_xdotool_key(self, key_name: str) -> str:
        """将键名解析为 xdotool keyname"""
        key_map = {
            'ctrl': 'ctrl', 'shift': 'shift', 'alt': 'alt',
            'enter': 'Return', 'tab': 'Tab', 'esc': 'Escape',
            'space': 'space', 'backspace': 'BackSpace',
            'del': 'Delete', 'ins': 'Insert',
            'home': 'Home', 'end': 'End',
            'page_up': 'Prior', 'page_down': 'Next',
            'left': 'Left', 'up': 'Up', 'right': 'Right', 'down': 'Down',
            'caps_lock': 'Caps_Lock',
            'f1': 'F1', 'f2': 'F2', 'f3': 'F3', 'f4': 'F4',
            'f5': 'F5', 'f6': 'F6', 'f7': 'F7', 'f8': 'F8',
            'f9': 'F9', 'f10': 'F10', 'f11': 'F11', 'f12': 'F12',
            'lwin': 'super', 'rwin': 'super',
        }
        return key_map.get(key_name, key_name)
    
    def _xdotool_run(self, args: list):
        """执行 xdotool 命令"""
        try:
            subprocess.run(['xdotool'] + args, timeout=3, 
                         capture_output=True, check=False)
        except Exception as e:
            logger.error(f"xdotool 执行失败: {e}")


# ================= 跨平台屏幕捕获管理器 =================

class CrossPlatformCameraManager:
    """
    跨平台屏幕捕获管理器
    Windows: dxcam (优先) -> mss (降级)
    Linux/macOS: mss
    """
    
    def __init__(self):
        self._backend = None  # 'dxcam' or 'mss'
        self._mss_sct = None
        self._current_idx = 0
        
        import threading
        self.lock = threading.RLock()
        
        if IS_WINDOWS:
            # Windows 不使用此类，使用原始 CameraManager
            return
        
        # 非 Windows 只使用 mss
        try:
            import mss
            self._mss_sct = mss.mss()
            self._backend = 'mss'
            logger.info("屏幕捕获后端: mss")
        except ImportError:
            logger.error("mss 未安装！屏幕捕获将不可用 (pip install mss)")
        except Exception as e:
            logger.error(f"初始化 mss 失败: {e}")
    
    def is_available(self) -> bool:
        return self._backend is not None
    
    def get_screen_size(self) -> Tuple[int, int]:
        """获取屏幕尺寸"""
        if self._backend == 'mss' and self._mss_sct:
            try:
                monitors = self._mss_sct.monitors
                mon_idx = self._current_idx + 1
                if mon_idx >= len(monitors):
                    mon_idx = 1
                m = monitors[mon_idx]
                return (m['width'], m['height'])
            except Exception:
                pass
        return (1920, 1080)
    
    def switch_monitor(self, idx: int) -> bool:
        """切换显示器"""
        with self.lock:
            self._current_idx = idx
            return self.is_available()
    
    def get_raw_frame(self, target_fps: int = 60):
        """获取原始帧"""
        import numpy as np
        
        if self._backend == 'mss' and self._mss_sct:
            try:
                monitors = self._mss_sct.monitors
                mon_idx = self._current_idx + 1
                if mon_idx >= len(monitors):
                    mon_idx = 1
                monitor = monitors[mon_idx]
                sct_img = self._mss_sct.grab(monitor)
                img = np.array(sct_img)
                return img[:, :, :3]  # BGRA -> BGR
            except Exception as e:
                logger.error(f"mss 截图失败: {e}")
                return None
        return None
    
    def cleanup(self):
        """清理资源"""
        if self._mss_sct:
            try:
                self._mss_sct.close()
            except Exception:
                pass
            self._mss_sct = None


# ================= 全局平台信息实例 =================

platform_info = PlatformInfo()

# 跨平台输入模拟器实例（仅非 Windows 使用）
cross_input = CrossPlatformInputSim() if not IS_WINDOWS else None

# 跨平台屏幕捕获实例（仅非 Windows 使用）
cross_camera = CrossPlatformCameraManager() if not IS_WINDOWS else None


def init_platform():
    """
    初始化平台检测，打印检测报告
    应在程序启动时调用
    """
    platform_info.print_report()
    return platform_info


def get_platform_info() -> PlatformInfo:
    """获取平台信息"""
    return platform_info
