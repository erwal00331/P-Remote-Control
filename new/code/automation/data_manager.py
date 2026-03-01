"""
远程自动化数据管理器
增强版本：使用比例坐标存储，增强健壮性
"""
import json
import os
import sys
import logging
from typing import Dict, List, Optional, Tuple, Any, Union
from threading import RLock

# 导入路径辅助模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from path_helper import get_data_dir

logger = logging.getLogger(__name__)


class DataManager:
    """
    处理所有自动化数据的加载和保存
    
    数据存储格式：
    - 按钮位置：使用比例坐标 [ratio_x, ratio_y, ratio_w, ratio_h]
    - OCR 区域：使用比例坐标 [ratio_x, ratio_y, ratio_w, ratio_h]
    - 动作序列：包含动作类型和参数的列表
    """
    
    # 版本号，用于数据迁移
    DATA_VERSION = 2
    
    def __init__(self, data_dir: Optional[str] = None):
        """
        初始化数据管理器
        
        Args:
            data_dir: 数据目录路径，默认使用 path_helper 获取
        """
        if data_dir is None:
            data_dir = get_data_dir()
        
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # 文件路径
        self.button_positions_file = os.path.join(data_dir, "buttons.json")
        self.ocr_positions_file = os.path.join(data_dir, "ocr_regions.json")
        self.action_sequences_file = os.path.join(data_dir, "sequences.json")
        
        # 内存数据
        self.button_positions: Dict[str, List[float]] = {}  # {name: [ratio_x, ratio_y, ratio_w, ratio_h]}
        self.button_groups: Dict[str, List[str]] = {}       # {group_name: [button_names...]}
        self.ocr_positions: Dict[str, Any] = {}             # {name: {position: [...], data_type: str}}
        self.action_sequences: Dict[str, Any] = {}          # {name: {type: "sequence", actions: [...]}}
        
        # 线程锁
        self._lock = RLock()
        
        self.refresh_data()
    
    def _load_json(self, file_path: str, default: Any = None) -> Any:
        """
        从 JSON 文件安全加载数据
        
        Args:
            file_path: 文件路径
            default: 默认值
            
        Returns:
            加载的数据或默认值
        """
        if default is None:
            default = {}
            
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                # 检查数据版本并迁移
                if isinstance(data, dict):
                    version = data.get("_version", 1)
                    if version < self.DATA_VERSION:
                        data = self._migrate_data(data, version)
                        
                return data
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析错误 {file_path}: {e}")
            # 备份损坏的文件
            self._backup_corrupted_file(file_path)
        except IOError as e:
            logger.error(f"读取文件失败 {file_path}: {e}")
            
        return default
    
    def _save_json(self, file_path: str, data: Any) -> bool:
        """
        安全保存数据到 JSON 文件
        
        Args:
            file_path: 文件路径
            data: 要保存的数据
            
        Returns:
            是否成功
        """
        try:
            # 添加版本号
            if isinstance(data, dict) and "_version" not in data:
                data["_version"] = self.DATA_VERSION
                
            # 先写入临时文件
            temp_path = file_path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # 原子替换
            if os.path.exists(file_path):
                backup_path = file_path + ".bak"
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                os.rename(file_path, backup_path)
                
            os.rename(temp_path, file_path)
            return True
            
        except IOError as e:
            logger.error(f"保存文件失败 {file_path}: {e}")
            return False
    
    def _backup_corrupted_file(self, file_path: str):
        """备份损坏的文件"""
        try:
            if os.path.exists(file_path):
                import time
                backup_path = f"{file_path}.corrupted.{int(time.time())}"
                os.rename(file_path, backup_path)
                logger.info(f"已备份损坏文件到: {backup_path}")
        except Exception as e:
            logger.error(f"备份文件失败: {e}")
    
    def _migrate_data(self, data: dict, from_version: int) -> dict:
        """
        数据迁移
        
        Args:
            data: 旧版本数据
            from_version: 源版本号
            
        Returns:
            迁移后的数据
        """
        # V1 -> V2: 像素坐标迁移到比例坐标
        # 这里暂不自动迁移，保持向后兼容
        logger.info(f"数据版本迁移: v{from_version} -> v{self.DATA_VERSION}")
        data["_version"] = self.DATA_VERSION
        return data
    
    def refresh_data(self):
        """从文件重新加载所有数据"""
        with self._lock:
            buttons_data = self._load_json(self.button_positions_file, {
                "positions": {},
                "groups": {"默认分组": []}
            })
            self.button_positions = buttons_data.get("positions", {})
            self.button_groups = buttons_data.get("groups", {"默认分组": []})
            
            self.ocr_positions = self._load_json(self.ocr_positions_file, {})
            self.action_sequences = self._load_json(self.action_sequences_file, {})
    
    def save_buttons(self) -> bool:
        """保存按钮数据"""
        with self._lock:
            data = {
                "_version": self.DATA_VERSION,
                "positions": self.button_positions,
                "groups": self.button_groups
            }
            return self._save_json(self.button_positions_file, data)
    
    def save_ocr_positions(self) -> bool:
        """保存 OCR 区域数据"""
        with self._lock:
            data = dict(self.ocr_positions)
            data["_version"] = self.DATA_VERSION
            return self._save_json(self.ocr_positions_file, data)
    
    def save_sequences(self) -> bool:
        """保存动作序列数据"""
        with self._lock:
            data = dict(self.action_sequences)
            data["_version"] = self.DATA_VERSION
            return self._save_json(self.action_sequences_file, data)
    
    # ============ 按钮管理 ============
    
    def add_button(self, name: str, position: List[float], group: str = "默认分组") -> Tuple[bool, str]:
        """
        添加按钮
        
        Args:
            name: 按钮名称
            position: 位置 [ratio_x, ratio_y, ratio_w, ratio_h] 或 [x, y, w, h] 像素
            group: 分组名称
            
        Returns:
            (是否成功, 消息)
        """
        if not name or not isinstance(name, str):
            return False, "按钮名称无效"
        
        name = name.strip()
        if not name:
            return False, "按钮名称不能为空"
        
        if not self._validate_position(position):
            return False, "位置坐标无效"
        
        with self._lock:
            if name in self.button_positions:
                return False, "按钮名称已存在"
            
            if group not in self.button_groups:
                self.button_groups[group] = []
            
            # 存储位置（确保是列表格式）
            self.button_positions[name] = list(position)[:4]
            self.button_groups[group].append(name)
            self.save_buttons()
            
        return True, "添加成功"
    
    def update_button(self, name: str, position: Optional[List[float]] = None,
                      new_name: Optional[str] = None, group: Optional[str] = None) -> Tuple[bool, str]:
        """
        更新按钮
        
        Args:
            name: 按钮名称
            position: 新位置（可选）
            new_name: 新名称（可选）
            group: 新分组（可选）
            
        Returns:
            (是否成功, 消息)
        """
        with self._lock:
            if name not in self.button_positions:
                return False, "按钮不存在"
            
            if position is not None:
                if not self._validate_position(position):
                    return False, "位置坐标无效"
                self.button_positions[name] = list(position)[:4]
            
            target_name = name
            
            if new_name and new_name != name:
                new_name = new_name.strip()
                if not new_name:
                    return False, "新名称不能为空"
                if new_name in self.button_positions:
                    return False, "新名称已存在"
                    
                # 更新位置数据
                self.button_positions[new_name] = self.button_positions.pop(name)
                
                # 更新分组中的引用
                for g, buttons in self.button_groups.items():
                    if name in buttons:
                        idx = buttons.index(name)
                        buttons[idx] = new_name
                        break
                        
                target_name = new_name
            
            if group is not None:
                # 从旧分组移除
                for g, buttons in self.button_groups.items():
                    if target_name in buttons:
                        buttons.remove(target_name)
                        break
                        
                # 添加到新分组
                if group not in self.button_groups:
                    self.button_groups[group] = []
                self.button_groups[group].append(target_name)
            
            self.save_buttons()
            
        return True, "更新成功"
    
    def delete_button(self, name: str) -> Tuple[bool, str]:
        """删除按钮"""
        with self._lock:
            if name not in self.button_positions:
                return False, "按钮不存在"
            
            del self.button_positions[name]
            
            for buttons in self.button_groups.values():
                if name in buttons:
                    buttons.remove(name)
                    break
            
            self.save_buttons()
            
        return True, "删除成功"
    
    def get_button(self, name: str) -> Optional[List[float]]:
        """获取按钮位置"""
        with self._lock:
            return self.button_positions.get(name)
    
    def get_all_buttons(self) -> Dict[str, Any]:
        """获取所有按钮数据"""
        with self._lock:
            return {
                "positions": dict(self.button_positions),
                "groups": dict(self.button_groups)
            }
    
    # ============ OCR 区域管理 ============
    
    def add_ocr_region(self, name: str, position: List[float], 
                       data_type: str = "字符串", group: Optional[str] = None) -> Tuple[bool, str]:
        """
        添加 OCR 区域
        
        Args:
            name: 区域名称
            position: 位置 [ratio_x, ratio_y, ratio_w, ratio_h]
            data_type: 数据类型 (字符串, 数字, 时间, 分数)
            group: 分组名称（可选）
            
        Returns:
            (是否成功, 消息)
        """
        if not name or not isinstance(name, str):
            return False, "区域名称无效"
        
        name = name.strip()
        if not name:
            return False, "区域名称不能为空"
        
        if not self._validate_position(position):
            return False, "位置坐标无效"
        
        with self._lock:
            target = self.ocr_positions
            if group:
                if group not in self.ocr_positions:
                    self.ocr_positions[group] = {"type": "group", "children": {}}
                target = self.ocr_positions[group].get("children", {})
            
            if name in target:
                return False, "区域名称已存在"
            
            target[name] = {
                "position": list(position)[:4],
                "type": "region",
                "data_type": data_type
            }
            self.save_ocr_positions()
            
        return True, "添加成功"
    
    def update_ocr_region(self, name: str, position: Optional[List[float]] = None,
                          data_type: Optional[str] = None, new_name: Optional[str] = None) -> Tuple[bool, str]:
        """更新 OCR 区域"""
        with self._lock:
            region = self._find_ocr_region(name)
            if not region:
                return False, "区域不存在"
            
            if position is not None:
                if not self._validate_position(position):
                    return False, "位置坐标无效"
                region["position"] = list(position)[:4]
                
            if data_type is not None:
                region["data_type"] = data_type
            
            self.save_ocr_positions()
            
        return True, "更新成功"
    
    def delete_ocr_region(self, name: str) -> Tuple[bool, str]:
        """删除 OCR 区域"""
        with self._lock:
            deleted = self._delete_from_dict(self.ocr_positions, name)
            if deleted:
                self.save_ocr_positions()
                return True, "删除成功"
        return False, "区域不存在"
    
    def _find_ocr_region(self, name: str, data: Optional[Dict] = None) -> Optional[Dict]:
        """递归查找 OCR 区域"""
        if data is None:
            data = self.ocr_positions
        
        if name in data:
            item = data[name]
            if isinstance(item, dict) and item.get("type") != "group":
                return item
        
        for key, value in data.items():
            if isinstance(value, dict) and value.get("type") == "group":
                found = self._find_ocr_region(name, value.get("children", {}))
                if found:
                    return found
        return None
    
    def _delete_from_dict(self, data: Dict, name: str) -> bool:
        """递归删除"""
        if name in data:
            del data[name]
            return True
        
        for key, value in data.items():
            if isinstance(value, dict) and value.get("type") == "group":
                if self._delete_from_dict(value.get("children", {}), name):
                    return True
        return False
    
    def get_ocr_region(self, name: str) -> Optional[Dict]:
        """获取 OCR 区域数据"""
        with self._lock:
            return self._find_ocr_region(name)
    
    def get_all_ocr_regions(self) -> Dict:
        """获取所有 OCR 区域"""
        with self._lock:
            return dict(self.ocr_positions)
    
    # ============ 动作序列管理 ============
    
    def add_sequence(self, name: str, actions: List[Dict], group: Optional[str] = None) -> Tuple[bool, str]:
        """
        添加动作序列
        
        Args:
            name: 序列名称
            actions: 动作列表
            group: 分组名称（可选）
            
        Returns:
            (是否成功, 消息)
        """
        if not name or not isinstance(name, str):
            return False, "序列名称无效"
        
        name = name.strip()
        if not name:
            return False, "序列名称不能为空"
        
        if not isinstance(actions, list):
            return False, "动作列表格式无效"
        
        with self._lock:
            target = self.action_sequences
            if group:
                if group not in self.action_sequences:
                    self.action_sequences[group] = {"type": "group", "children": {}}
                target = self.action_sequences[group].get("children", {})
            
            target[name] = {
                "type": "sequence",
                "actions": actions
            }
            self.save_sequences()
            
        return True, "保存成功"
    
    def get_sequence(self, name: str, data: Optional[Dict] = None) -> Optional[Dict]:
        """获取动作序列"""
        with self._lock:
            if data is None:
                data = self.action_sequences
            
            if name in data:
                item = data[name]
                if isinstance(item, dict) and item.get("type") == "sequence":
                    return item
            
            for key, value in data.items():
                if isinstance(value, dict) and value.get("type") == "group":
                    found = self.get_sequence(name, value.get("children", {}))
                    if found:
                        return found
            return None
    
    def delete_sequence(self, name: str) -> Tuple[bool, str]:
        """删除动作序列"""
        with self._lock:
            deleted = self._delete_from_dict(self.action_sequences, name)
            if deleted:
                self.save_sequences()
                return True, "删除成功"
        return False, "序列不存在"
    
    def get_all_sequences(self) -> Dict:
        """获取所有动作序列"""
        with self._lock:
            return dict(self.action_sequences)
    
    # ============ 辅助方法 ============
    
    def _validate_position(self, position: Any) -> bool:
        """
        验证位置是否有效
        
        Args:
            position: 位置数据
            
        Returns:
            是否有效
        """
        if not isinstance(position, (list, tuple)):
            return False
        
        if len(position) < 2:
            return False
        
        try:
            # 尝试转换为数值
            for i, v in enumerate(position[:4]):
                float(v)
            return True
        except (TypeError, ValueError):
            return False
