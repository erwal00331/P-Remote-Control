# 远程自动化模块
from .data_manager import DataManager
from .action_executor import ActionExecutor
from .ocr_manager import OcrManager
from .coordinate_utils import CoordinateConverter

__all__ = ['DataManager', 'ActionExecutor', 'OcrManager', 'CoordinateConverter']
