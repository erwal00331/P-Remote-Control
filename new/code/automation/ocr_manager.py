"""
远程自动化 OCR 管理器
使用比例坐标，增强健壮性
"""
import re
import io
import sys
import os
import logging
from typing import Optional, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
import numpy as np

from .coordinate_utils import CoordinateConverter

logger = logging.getLogger(__name__)


class OcrManager:
    """OCR 识别管理器，使用比例坐标"""
    
    def __init__(self, data_manager, camera_manager=None):
        self.data_manager = data_manager
        self.camera_manager = camera_manager
        self._ocr_client = None
        self._coord_converter: Optional[CoordinateConverter] = None
    
    def _get_ocr_client(self):
        if self._ocr_client is None:
            try:
                from api_manager import create_ocr_client
                self._ocr_client = create_ocr_client()
            except Exception as e:
                logger.error(f"初始化 OCR 客户端失败: {e}")
                raise
        return self._ocr_client
    
    def _get_coord_converter(self) -> CoordinateConverter:
        if self._coord_converter is None:
            w, h = self._get_screen_size()
            self._coord_converter = CoordinateConverter(w, h)
        return self._coord_converter
    
    def _get_screen_size(self):
        if self.camera_manager:
            try:
                return self.camera_manager.get_screen_size()
            except:
                pass
        # 回退到默认值
        return (1920, 1080)
    
    def recognize_region(self, region_name: str) -> str:
        """识别指定区域的文字，支持 Region[Index] 语法"""
        # 解析索引
        index = None
        match = re.match(r'(.+)\[(\d+)\]', region_name)
        if match:
            region_name = match.group(1)
            index = int(match.group(2))

        region_data = self.data_manager.get_ocr_region(region_name)
        if not region_data:
            raise ValueError(f"未找到 OCR 区域: {region_name}")
        
        position = region_data.get("position")
        data_type = region_data.get("data_type", "字符串")
        
        if not isinstance(position, list) or len(position) < 4:
            raise ValueError(f"区域坐标格式不正确: {position}")
        
        # 截取屏幕区域 (传入原始位置信息，内部处理分辨率匹配)
        img_bytes = self._capture_region(position)
        
        # 调用 OCR
        ocr_client = self._get_ocr_client()
        result = ocr_client.recognize(img_bytes)
        
        # 提取文本
        text = self._extract_text(result)
        
        # 后处理
        final_text = self._post_process(text, data_type)
        
        # 应用索引筛选
        if index is not None:
            # 优先尝试用 / 分割
            if "/" in final_text:
                parts = final_text.split("/")
                if 0 <= index < len(parts):
                    return parts[index].strip()
            # 其次尝试用换行符分割
            parts = final_text.split("\n")
            if 0 <= index < len(parts):
                return parts[index].strip()
            # 如果索引越界或无法分割，返回空字符串
            return ""
            
        return final_text
    
    def recognize_image(self, image_bytes: bytes, data_type: str = "字符串") -> str:
        """直接识别图片数据"""
        ocr_client = self._get_ocr_client()
        result = ocr_client.recognize(image_bytes)
        text = self._extract_text(result)
        return self._post_process(text, data_type)
    
    def _capture_region(self, position: list) -> bytes:
        """
        截取屏幕指定区域
        
        Args:
            position: [x, y, w, h] 坐标列表 (可以是比例或像素)
            
        Returns:
            bytes: 图片字节数据
        """
        # 尝试使用 CameraManager (高性能，物理分辨率)
        if self.camera_manager:
            try:
                frame = self.camera_manager.get_raw_frame()
                if frame is not None:
                    h_real, w_real = frame.shape[:2]
                    
                    frame_converter = CoordinateConverter(w_real, h_real)
                    x, y, w, h = frame_converter.position_to_pixel(position)
                    logger.info(f"Capture Region: Input={position} -> Pixel={x},{y},{w},{h} (Screen={w_real}x{h_real})")
                    
                    
                    x1, y1 = max(0, x), max(0, y)
                    x2 = min(w_real, x + w)
                    y2 = min(h_real, y + h)
                    
                    if x2 > x1 and y2 > y1:
                        cropped = frame[y1:y2, x1:x2]
                        if cropped.size > 0:
                            import cv2
                            # 使用最高质量 JPEG
                            success, buffer = cv2.imencode('.jpg', cropped, 
                                [cv2.IMWRITE_JPEG_QUALITY, 99])
                            if success:
                                return buffer.tobytes()
            except Exception as e:
                logger.warning(f"使用 camera_manager 截图失败: {e}")
        
        # 备选：使用 PIL ImageGrab (兼容性好，逻辑分辨率)
        try:
            from PIL import ImageGrab
            
            # 使用默认转换器 (基于系统逻辑分辨率)
            sys_converter = self._get_coord_converter()
            x, y, w, h = sys_converter.position_to_pixel(position)
            
            if w <= 0 or h <= 0:
                 raise ValueError(f"无效的截图区域: {w}x{h}")
                 
            screenshot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            
            # 移除过度对比度增强
            
            img_byte_arr = io.BytesIO()
            screenshot.save(img_byte_arr, format='JPEG', quality=95)
            return img_byte_arr.getvalue()
        except Exception as e:
            raise RuntimeError(f"截屏失败: {e}")
    
    def _extract_text(self, ocr_result: Any) -> str:
        """从 OCR 结果中提取纯文本"""
        if isinstance(ocr_result, str):
            return ocr_result
        
        if isinstance(ocr_result, dict):
            # 优先检查直接存在的 line_texts (适配 api_manager 返回的 data 内容)
            if "line_texts" in ocr_result:
                return "\n".join(ocr_result["line_texts"])
                
            if "data" in ocr_result:
                data = ocr_result["data"]
                if isinstance(data, dict) and "line_texts" in data:
                    return "\n".join(data["line_texts"])
            if "text" in ocr_result:
                return str(ocr_result["text"])
            if "results" in ocr_result:
                texts = []
                for item in ocr_result["results"]:
                    if isinstance(item, dict) and "text" in item:
                        texts.append(item["text"])
                    elif isinstance(item, str):
                        texts.append(item)
                return "\n".join(texts)
        
        if isinstance(ocr_result, list):
            texts = []
            for item in ocr_result:
                if isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
                elif isinstance(item, str):
                    texts.append(item)
            return "\n".join(texts)
        
        return str(ocr_result)
    
    def _post_process(self, text: str, data_type: str) -> str:
        """根据数据类型后处理文本"""
        if not text:
            return ""
        
        text = text.strip()
        
        if data_type == "时间":
            return re.sub(r'[^\d:]', '', text)
        elif data_type == "分数":
            return re.sub(r'[^\d/]', '', text)
        elif data_type == "数字":
            return re.sub(r'[^\d.]', '', text)
        else:
            return text
    
    def update_screen_size(self, width: int, height: int):
        """更新屏幕尺寸"""
        if self._coord_converter:
            self._coord_converter.update_screen_size(width, height)
        else:
            self._coord_converter = CoordinateConverter(width, height)
