import time
import psutil
import gc
import logging

logger = logging.getLogger(__name__)

_last_gc_time = time.time()

def check_memory_and_gc(threshold_mb=500):
    """
    检查内存使用情况，超过阈值则GC
    """
    global _last_gc_time
    try:
        process = psutil.Process()
        mem_info = process.memory_info()
        rss_mb = mem_info.rss / 1024 / 1024
        
        # 如果内存 > 阈值 且 距离上次GC超过10秒
        if rss_mb > threshold_mb and time.time() - _last_gc_time > 10:
            logger.info(f"Memory usage high ({rss_mb:.1f} MB), triggering GC...")
            gc.collect()
            _last_gc_time = time.time()
            return True
    except Exception as e:
        logger.error(f"Memory check failed: {e}")
    return False
