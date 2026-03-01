"""
坐标转换工具模块
处理像素坐标和比例坐标之间的转换
"""
import logging
from typing import Tuple, List, Optional, Union

logger = logging.getLogger(__name__)


class CoordinateConverter:
    """
    坐标转换器
    
    支持两种坐标模式：
    1. 像素模式：直接使用屏幕像素坐标
    2. 比例模式：使用 0.0-1.0 的比例值，与分辨率无关
    
    存储时使用比例模式，执行时转换为像素模式
    """
    
    def __init__(self, screen_width: int = 1920, screen_height: int = 1080):
        """
        初始化坐标转换器
        
        Args:
            screen_width: 屏幕宽度
            screen_height: 屏幕高度
        """
        self._screen_width = screen_width
        self._screen_height = screen_height
    
    def update_screen_size(self, width: int, height: int):
        """
        更新屏幕尺寸
        
        Args:
            width: 新的屏幕宽度
            height: 新的屏幕高度
        """
        if width > 0 and height > 0:
            self._screen_width = width
            self._screen_height = height
    
    @property
    def screen_size(self) -> Tuple[int, int]:
        """获取当前屏幕尺寸"""
        return (self._screen_width, self._screen_height)
    
    def pixel_to_ratio(self, x: int, y: int, w: int = 0, h: int = 0) -> Tuple[float, float, float, float]:
        """
        像素坐标转换为比例坐标
        
        Args:
            x: 像素 X 坐标
            y: 像素 Y 坐标
            w: 宽度（像素）
            h: 高度（像素）
            
        Returns:
            (ratio_x, ratio_y, ratio_w, ratio_h) 比例坐标元组
        """
        if self._screen_width <= 0 or self._screen_height <= 0:
            logger.warning("屏幕尺寸无效，无法转换坐标")
            return (0.0, 0.0, 0.0, 0.0)
        
        ratio_x = float(x) / self._screen_width
        ratio_y = float(y) / self._screen_height
        ratio_w = float(w) / self._screen_width
        ratio_h = float(h) / self._screen_height
        
        # 限制范围 0-1
        ratio_x = max(0.0, min(1.0, ratio_x))
        ratio_y = max(0.0, min(1.0, ratio_y))
        ratio_w = max(0.0, min(1.0, ratio_w))
        ratio_h = max(0.0, min(1.0, ratio_h))
        
        return (ratio_x, ratio_y, ratio_w, ratio_h)
    
    def ratio_to_pixel(self, ratio_x: float, ratio_y: float, 
                       ratio_w: float = 0.0, ratio_h: float = 0.0) -> Tuple[int, int, int, int]:
        """
        比例坐标转换为像素坐标
        
        Args:
            ratio_x: 比例 X 坐标 (0.0-1.0)
            ratio_y: 比例 Y 坐标 (0.0-1.0)
            ratio_w: 比例宽度 (0.0-1.0)
            ratio_h: 比例高度 (0.0-1.0)
            
        Returns:
            (x, y, w, h) 像素坐标元组
        """
        # 确保比例值在有效范围内
        ratio_x = max(0.0, min(1.0, float(ratio_x)))
        ratio_y = max(0.0, min(1.0, float(ratio_y)))
        ratio_w = max(0.0, min(1.0, float(ratio_w)))
        ratio_h = max(0.0, min(1.0, float(ratio_h)))
        
        x = int(ratio_x * self._screen_width)
        y = int(ratio_y * self._screen_height)
        w = int(ratio_w * self._screen_width)
        h = int(ratio_h * self._screen_height)
        
        return (x, y, w, h)
    
    def position_to_ratio(self, position: List[Union[int, float]]) -> List[float]:
        """
        将位置列表转换为比例格式
        
        Args:
            position: [x, y, w, h] 可以是像素或比例
            
        Returns:
            [ratio_x, ratio_y, ratio_w, ratio_h] 比例格式
        """
        if not position or len(position) < 2:
            return [0.0, 0.0, 0.0, 0.0]
        
        # 扩展到4个元素
        pos = list(position) + [0, 0]
        pos = pos[:4]
        
        x, y, w, h = pos
        
        # 判断是否已经是比例值（所有值都 <= 1）
        if all(isinstance(v, float) and 0.0 <= v <= 1.0 for v in [x, y]):
            return [float(x), float(y), float(w), float(h)]
        
        # 转换像素到比例
        ratio = self.pixel_to_ratio(int(x), int(y), int(w), int(h))
        return list(ratio)
    
    def position_to_pixel(self, position: List[Union[int, float]]) -> List[int]:
        """
        将位置列表转换为像素格式
        
        Args:
            position: [x, y, w, h] 可以是像素或比例
            
        Returns:
            [x, y, w, h] 像素格式
        """
        if not position or len(position) < 2:
            return [0, 0, 0, 0]
        
        # 扩展到4个元素
        pos = list(position) + [0, 0]
        pos = pos[:4]
        
        x, y, w, h = pos
        
        # 判断是否是比例值（所有值都 <= 1 且是 float）
        is_ratio = all(
            isinstance(v, float) and 0.0 <= v <= 1.0 
            for v in [x, y]
        )
        
        if is_ratio:
            return list(self.ratio_to_pixel(x, y, w, h))
        else:
            return [int(x), int(y), int(w), int(h)]
    
    @staticmethod
    def is_ratio_position(position: List[Union[int, float]]) -> bool:
        """
        判断位置是否为比例格式
        
        Args:
            position: 位置列表
            
        Returns:
            bool: 是否为比例格式
        """
        if not position or len(position) < 2:
            return False
        
        x, y = position[0], position[1]
        return all(
            isinstance(v, float) and 0.0 <= v <= 1.0 
            for v in [x, y]
        )
    
    def validate_position(self, position: List[Union[int, float]]) -> Tuple[bool, str]:
        """
        验证位置是否有效
        
        Args:
            position: 位置列表
            
        Returns:
            (是否有效, 错误信息)
        """
        if not position:
            return False, "位置不能为空"
        
        if not isinstance(position, (list, tuple)):
            return False, "位置必须是列表或元组"
        
        if len(position) < 2:
            return False, "位置至少需要2个元素(x, y)"
        
        try:
            x, y = float(position[0]), float(position[1])
            w = float(position[2]) if len(position) > 2 else 0
            h = float(position[3]) if len(position) > 3 else 0
            
            # 检查是否为比例值
            if self.is_ratio_position(position):
                if not (0 <= x <= 1 and 0 <= y <= 1):
                    return False, "比例坐标必须在 0-1 之间"
                if w < 0 or w > 1 or h < 0 or h > 1:
                    return False, "比例尺寸必须在 0-1 之间"
            else:
                # 像素值检查
                if x < 0 or y < 0:
                    return False, "像素坐标不能为负数"
                if x > self._screen_width * 2 or y > self._screen_height * 2:
                    return False, "像素坐标超出合理范围"
                    
            return True, ""
            
        except (TypeError, ValueError) as e:
            return False, f"坐标转换失败: {e}"


# 全局默认转换器
_default_converter: Optional[CoordinateConverter] = None


def get_converter(screen_width: int = 0, screen_height: int = 0) -> CoordinateConverter:
    """
    获取全局坐标转换器
    
    Args:
        screen_width: 屏幕宽度（可选，用于更新）
        screen_height: 屏幕高度（可选，用于更新）
        
    Returns:
        CoordinateConverter 实例
    """
    global _default_converter
    
    if _default_converter is None:
        _default_converter = CoordinateConverter(
            screen_width or 1920,
            screen_height or 1080
        )
    elif screen_width > 0 and screen_height > 0:
        _default_converter.update_screen_size(screen_width, screen_height)
    
    return _default_converter
