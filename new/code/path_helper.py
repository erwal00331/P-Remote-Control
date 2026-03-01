"""
路径辅助模块
用于解决 PyInstaller 打包后路径问题
打包后 exe 需要和 data 文件夹放在同一目录
"""
import os
import sys
import logging

logger = logging.getLogger(__name__)


def get_base_dir() -> str:
    """
    获取程序基础目录
    - 打包后: exe 所在目录
    - 开发时: code 文件夹的上级目录 (即 new 文件夹)
    
    Returns:
        str: 基础目录路径
    """
    try:
        if getattr(sys, 'frozen', False):
            # 打包后，返回 exe 所在目录
            return os.path.dirname(sys.executable)
        else:
            # 开发时，返回 code 文件夹的上级目录
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except Exception as e:
        logger.error(f"获取基础目录失败: {e}")
        return os.getcwd()


def get_data_dir() -> str:
    """
    获取 data 目录路径
    
    Returns:
        str: data 目录路径
    """
    data_dir = os.path.join(get_base_dir(), 'data')
    # 确保目录存在
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_data_file(filename: str) -> str:
    """
    获取 data 目录下的文件路径
    
    Args:
        filename: 文件名
        
    Returns:
        str: 完整文件路径
    """
    if not filename:
        raise ValueError("文件名不能为空")
    return os.path.join(get_data_dir(), filename)


def ensure_file_exists(filepath: str, default_content: str = "{}") -> bool:
    """
    确保文件存在，如果不存在则创建默认内容
    
    Args:
        filepath: 文件路径
        default_content: 默认内容
        
    Returns:
        bool: 是否成功
    """
    try:
        if not os.path.exists(filepath):
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(default_content)
            logger.info(f"创建默认文件: {filepath}")
        return True
    except Exception as e:
        logger.error(f"创建文件失败 {filepath}: {e}")
        return False
