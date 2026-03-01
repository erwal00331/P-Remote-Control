"""
远程自动化动作执行器
使用比例坐标，更好的错误处理
"""
import time
import random
import re
import logging
from typing import Dict, List, Optional, Tuple, Any, Set

from .coordinate_utils import CoordinateConverter

logger = logging.getLogger(__name__)


class ActionExecutor:
    """负责执行自动化动作序列，使用比例坐标"""
    
    def __init__(self, data_manager, ocr_manager, input_sim):
        self.data_manager = data_manager
        self.ocr_manager = ocr_manager
        self.input_sim = input_sim
        self.stop_flag = False
        self.is_running = False
        self._screen_size: Optional[Tuple[int, int]] = None
        self._coord_converter: Optional[CoordinateConverter] = None
    
    def _get_screen_size(self) -> Tuple[int, int]:
        if self._screen_size is None:
            self._screen_size = self.input_sim.get_screen_size()
        return self._screen_size
    
    def _get_coord_converter(self) -> CoordinateConverter:
        if self._coord_converter is None:
            w, h = self._get_screen_size()
            self._coord_converter = CoordinateConverter(w, h)
        return self._coord_converter
    
    def refresh_screen_size(self):
        self._screen_size = self.input_sim.get_screen_size()
        if self._coord_converter:
            w, h = self._screen_size
            self._coord_converter.update_screen_size(w, h)
    
    def stop(self):
        self.stop_flag = True
        logger.info("收到停止信号")
    
    def execute_sequence(self, sequence_name: str) -> Tuple[bool, str]:
        if self.is_running:
            return False, "另一个序列正在执行中"
        
        self.is_running = True
        self.stop_flag = False
        
        try:
            self.refresh_screen_size()
            self._execute_recursive(sequence_name, call_stack=set())
            return True, f"序列 '{sequence_name}' 执行完成"
        except StopIteration:
            return True, "执行被用户停止"
        except Exception as e:
            logger.error(f"执行序列时出错: {e}")
            return False, f"执行错误: {e}"
        finally:
            self.is_running = False
    
    def execute_actions(self, actions: List[Dict]) -> Tuple[bool, str]:
        if self.is_running:
            return False, "另一个序列正在执行中"
        
        self.is_running = True
        self.stop_flag = False
        
        try:
            self.refresh_screen_size()
            self._execute_actions(actions, call_stack=set())
            return True, "执行完成"
        except StopIteration:
            return True, "执行被用户停止"
        except Exception as e:
            return False, f"执行错误: {e}"
        finally:
            self.is_running = False
    
    def _execute_recursive(self, name: str, actions=None, call_stack=None):
        if call_stack is None:
            call_stack = set()
        
        if name in call_stack:
            raise Exception(f"检测到循环调用: {name}")
        call_stack.add(name)
        
        try:
            if actions is None:
                seq = self.data_manager.get_sequence(name)
                if not seq:
                    raise Exception(f"未找到动作序列: {name}")
                actions = seq.get("actions", [])
            self._execute_actions(actions, call_stack)
        finally:
            call_stack.discard(name)
    
    def _execute_actions(self, actions: List[Dict], call_stack: Set[str]):
        if not isinstance(actions, list):
            raise Exception("动作列表格式不正确")
        
        loop_map = self._build_loop_map(actions)
        if_map = self._build_if_map(actions)
        jump_map = self._build_jump_map(actions)
        
        pc = 0
        loop_stack = []
        
        while pc < len(actions):
            if self.stop_flag:
                raise StopIteration("用户停止")
            
            action = actions[pc]
            atype = action.get("type", "")
            param = action.get("param", "")
            
            if atype == "start_jump":
                pc = jump_map.get(pc, pc) + 1
                continue
            elif atype == "end_jump":
                pc += 1
                continue
            elif atype == "if":
                if not self._evaluate_condition(param):
                    t = if_map.get(pc, {})
                    pc = t.get("else", t.get("end_if", pc)) + 1
                else:
                    pc += 1
                continue
            elif atype == "else":
                for idx, t in if_map.items():
                    if t.get("else") == pc:
                        pc = if_map[idx].get("end_if", pc) + 1
                        break
                else:
                    pc += 1
                continue
            elif atype == "end_if":
                pc += 1
                continue
            elif atype == "start_loop":
                params = self._parse_loop_params(param)
                loop_type = "condition" if "condition" in params else "count"
                info = {"start_pc": pc, "end_pc": loop_map.get(pc), "iteration": 0, "loop_type": loop_type}
                
                if loop_type == "count":
                    info["count"] = int(params.get("count", 1))
                    if info["count"] <= 0:
                        pc = info["end_pc"] + 1
                        continue
                else:
                    info["condition"] = params["condition"]
                    info["max_loops"] = int(params.get("max", 999))
                    if self._evaluate_condition(info["condition"]):
                        pc = info["end_pc"] + 1
                        continue
                
                loop_stack.append(info)
                pc += 1
            elif atype == "end_loop":
                if not loop_stack:
                    raise Exception("end_loop 不匹配")
                loop = loop_stack[-1]
                loop["iteration"] += 1
                cont = False
                if loop["loop_type"] == "count":
                    cont = loop["iteration"] < loop["count"]
                else:
                    cont = loop["iteration"] < loop["max_loops"] and not self._evaluate_condition(loop["condition"])
                if cont:
                    pc = loop["start_pc"] + 1
                else:
                    loop_stack.pop()
                    pc += 1
            else:
                self._execute_single_action(atype, param, call_stack)
                pc += 1
    
    def _execute_single_action(self, atype: str, param: Any, call_stack: Set[str]):
        sw, sh = self._get_screen_size()
        conv = self._get_coord_converter()
        
        if atype == "click":
            pos_data = self.data_manager.get_button(param)
            if not pos_data:
                raise Exception(f"未找到按钮: {param}")
            pos = conv.position_to_pixel(pos_data)
            x, y, w, h = pos
            w, h = max(1, w), max(1, h)
            cx = x + random.uniform(0.2*w, 0.8*w)
            cy = y + random.uniform(0.2*h, 0.8*h)
            self.input_sim.move(cx, cy, sw, sh)
            time.sleep(0.05)
            self.input_sim.click(cx, cy, "left", "click", sw, sh)
            time.sleep(0.1)
        elif atype == "double_click":
            pos_data = self.data_manager.get_button(param)
            if not pos_data:
                raise Exception(f"未找到按钮: {param}")
            pos = conv.position_to_pixel(pos_data)
            x, y, w, h = pos
            w, h = max(1, w), max(1, h)
            cx = x + random.uniform(0.2*w, 0.8*w)
            cy = y + random.uniform(0.2*h, 0.8*h)
            self.input_sim.move(cx, cy, sw, sh)
            time.sleep(0.05)
            self.input_sim.double_click(cx, cy, "left", sw, sh)
            time.sleep(0.1)
        elif atype == "activate_window":
            if param:
                self.input_sim.activate_window(str(param))
                time.sleep(0.5)
        elif atype == "wait":
            wt = min(3600, max(0, float(param)))
            elapsed = 0
            while elapsed < wt:
                if self.stop_flag:
                    raise StopIteration()
                time.sleep(min(0.1, wt - elapsed))
                elapsed += 0.1
        elif atype == "type":
            if param:
                self.input_sim.write_text(str(param))
        elif atype == "press":
            keys = param if isinstance(param, list) else [k.strip() for k in param.split("+")] if "+" in param else [param]
            self.input_sim.press_sequence(keys)
        elif atype == "call":
            self._execute_recursive(param, call_stack=call_stack)
    
    def _build_jump_map(self, actions):
        starts, ends = {}, {}
        for i, a in enumerate(actions):
            p = a.get("param", "")
            if a.get("type") == "start_jump":
                starts[p] = i
            elif a.get("type") == "end_jump":
                ends[p] = i
        return {starts[l]: ends[l] for l in starts if l in ends}
    
    def _build_loop_map(self, actions):
        m, s = {}, []
        for i, a in enumerate(actions):
            if a.get("type") == "start_loop":
                s.append(i)
            elif a.get("type") == "end_loop":
                if s:
                    m[s.pop()] = i
        return m
    
    def _build_if_map(self, actions):
        m, s = {}, []
        for i, a in enumerate(actions):
            t = a.get("type")
            if t == "if":
                s.append(i)
            elif t == "else" and s:
                m.setdefault(s[-1], {})["else"] = i
            elif t == "end_if" and s:
                m.setdefault(s.pop(), {})["end_if"] = i
        return m
    
    def _parse_loop_params(self, s):
        s = str(s).strip()
        if s.isdigit():
            return {"count": s}
            
        p = {}
        for part in s.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                p[k.strip()] = v.strip()
            elif part.strip():
                p["condition"] = part.strip()
        return p
    
    def _evaluate_condition(self, cond):
        ops = {">=": lambda a,b: float(a)>=float(b), "<=": lambda a,b: float(a)<=float(b),
               "==": lambda a,b: str(a).strip()==str(b).strip(), "!=": lambda a,b: str(a).strip()!=str(b).strip(),
               ">": lambda a,b: float(a)>float(b), "<": lambda a,b: float(a)<float(b)}
        op = next((o for o in sorted(ops, key=len, reverse=True) if o in cond), None)
        
        if op:
            l, r = [p.strip() for p in cond.split(op, 1)]
            lv, rv = self._get_operand_value(l), self._get_operand_value(r)
            try:
                return ops[op](float(lv), float(rv))
            except:
                return ops[op](str(lv), str(rv))
        
        # 无运算符，检查真值
        val = self._get_operand_value(cond.strip())
        try:
            return float(val) != 0
        except:
            return str(val).lower() not in ("false", "0", "", "none", "null")
    
    def _get_operand_value(self, s):
        m = re.match(r'(.+)\[(\d+)\]', s)
        if m:
            n, i = m.group(1), int(m.group(2))
            if self.data_manager.get_ocr_region(n):
                txt = self.ocr_manager.recognize_region(n)
                parts = txt.split("/")
                return parts[i].strip() if i < len(parts) else ""
        if self.data_manager.get_ocr_region(s):
            txt = self.ocr_manager.recognize_region(s)
            return txt.split("/")[0].strip() if "/" in txt else txt
        return s
    
    def click_button(self, name: str) -> Tuple[bool, str]:
        pos = self.data_manager.get_button(name)
        if not pos:
            return False, f"未找到按钮: {name}"
        try:
            sw, sh = self._get_screen_size()
            conv = self._get_coord_converter()
            x, y, w, h = conv.position_to_pixel(pos)
            w, h = max(1, w), max(1, h)
            cx, cy = x + random.uniform(0.2*w, 0.8*w), y + random.uniform(0.2*h, 0.8*h)
            self.input_sim.move(cx, cy, sw, sh)
            time.sleep(0.05)
            self.input_sim.click(cx, cy, "left", "click", sw, sh)
            return True, f"已点击 '{name}'"
        except Exception as e:
            return False, f"点击失败: {e}"
    
    def click_position(self, x: float, y: float, w: float = 0.01, h: float = 0.01) -> Tuple[bool, str]:
        try:
            sw, sh = self._get_screen_size()
            conv = self._get_coord_converter()
            px, py, pw, ph = conv.position_to_pixel([x, y, w, h])
            pw, ph = max(1, pw), max(1, ph)
            cx, cy = px + random.uniform(0, pw), py + random.uniform(0, ph)
            self.input_sim.move(cx, cy, sw, sh)
            time.sleep(0.05)
            self.input_sim.click(cx, cy, "left", "click", sw, sh)
            return True, f"已点击 ({x:.4f}, {y:.4f})"
        except Exception as e:
            return False, f"点击失败: {e}"
