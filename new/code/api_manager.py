"""
火山引擎 OCR API 封装
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode
from typing import Dict, Any, Optional, Callable

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from path_helper import get_data_file
try:
    from notification import notification_manager
except ImportError:
    notification_manager = None

logger = logging.getLogger(__name__)


class VolcengineOCR:
    """火山引擎通用文字识别 API 封装"""
    
    HOST = "visual.volcengineapi.com"
    ENDPOINT = "https://visual.volcengineapi.com"
    SERVICE = "cv"
    REGION = "cn-north-1"
    ACTION = "OCRNormal"
    VERSION = "2020-08-26"
    REQUEST_TIMEOUT = 30
    
    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = get_data_file("api_data.json")
        self._load_config(config_path)
    
    def _load_config(self, config_path: str):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.access_key_id = config.get("ocr_keyid")
            self.secret_access_key = config.get("ocr_key")
            if not self.access_key_id or not self.secret_access_key:
                raise ValueError("配置文件中缺少 ocr_keyid 或 ocr_key")
        except FileNotFoundError:
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        except json.JSONDecodeError:
            raise ValueError(f"配置文件格式错误: {config_path}")
    
    def _get_current_time(self):
        now = datetime.now(timezone.utc)
        return now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    
    def _hmac_sha256(self, key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()
    
    def _sha256_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def _get_signature_key(self, secret_key: str, date: str, region: str, service: str) -> bytes:
        k_date = self._hmac_sha256(secret_key.encode('utf-8'), date)
        k_region = self._hmac_sha256(k_date, region)
        k_service = self._hmac_sha256(k_region, service)
        k_signing = self._hmac_sha256(k_service, "request")
        return k_signing
    
    def _create_authorization_header(self, method: str, query_string: str,
                                      headers: Dict, body: str,
                                      x_date: str, short_date: str) -> str:
        canonical_uri = "/"
        signed_headers_list = sorted(headers.keys())
        canonical_headers = "".join(f"{k.lower()}:{headers[k].strip()}\n" for k in signed_headers_list)
        signed_headers = ";".join(h.lower() for h in signed_headers_list)
        payload_hash = self._sha256_hash(body)
        
        canonical_request = f"{method}\n{canonical_uri}\n{query_string}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        
        algorithm = "HMAC-SHA256"
        credential_scope = f"{short_date}/{self.REGION}/{self.SERVICE}/request"
        string_to_sign = f"{algorithm}\n{x_date}\n{credential_scope}\n{self._sha256_hash(canonical_request)}"
        
        signing_key = self._get_signature_key(self.secret_access_key, short_date, self.REGION, self.SERVICE)
        signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
        
        return f"{algorithm} Credential={self.access_key_id}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    
    def recognize(self, image_data: bytes, **kwargs) -> Dict[str, Any]:
        """识别图片中的文字"""
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        body_params = {"image_base64": image_base64}
        
        for key in ["approximate_pixel", "mode", "filter_thresh", "half_to_full"]:
            if key in kwargs:
                body_params[key] = kwargs[key]
        
        body = urlencode(body_params)
        query_string = urlencode({"Action": self.ACTION, "Version": self.VERSION})
        x_date, short_date = self._get_current_time()
        
        headers_to_sign = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": self.HOST,
            "X-Date": x_date
        }
        
        authorization = self._create_authorization_header(
            "POST", query_string, headers_to_sign, body, x_date, short_date
        )
        
        request_headers = {**headers_to_sign, "Authorization": authorization}
        url = f"{self.ENDPOINT}?{query_string}"
        
        try:
            response = requests.post(url, headers=request_headers, data=body, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            
            if result.get("code") != 10000:
                raise ValueError(f"OCR 识别失败: {result.get('message')} (code: {result.get('code')})")
            
            return result.get("data", {})
            
        except requests.exceptions.Timeout:
            raise ValueError("OCR 请求超时")
        except requests.exceptions.RequestException as e:
            raise ValueError(f"OCR 请求失败: {e}")
    
    def recognize_and_get_text(self, image_data: bytes, **kwargs) -> str:
        """识别图片并返回合并后的文本"""
        result = self.recognize(image_data, **kwargs)
        return "\n".join(result.get("line_texts", []))


try:
    from dashscope import Generation
    import dashscope
except ImportError:
    Generation = None
    dashscope = None
    dashscope = None
    logger.warning("dashscope module not found. AI features will be disabled.")

class ChatManager:
    """Simple in-memory chat history manager"""
    def __init__(self):
        self.conversations = {} # id -> list of messages

    def create_conversation(self):
        import uuid
        conv_id = str(uuid.uuid4())
        self.conversations[conv_id] = []
        return conv_id

    def add_message(self, conv_id, role, content, tool_calls=None):
        if conv_id not in self.conversations:
             self.conversations[conv_id] = []
        
        msg = {"role": role, "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        
        self.conversations[conv_id].append(msg)
        
        # Limit history length
        if len(self.conversations[conv_id]) > 50:
            self.conversations[conv_id] = self.conversations[conv_id][-50:]

    def get_history(self, conv_id):
        return self.conversations.get(conv_id, [])

    def clear(self, conv_id):
         self.conversations[conv_id] = []


class AIManager:
    """AI 管理器: 处理屏幕理解和动作生成 (Step-by-Step)"""
    
    # Doubao Configuration
    DOUBAO_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    DOUBAO_MODEL = "doubao-seed-1-8-251228"
    
    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = get_data_file("api_data.json")
        self._load_config(config_path)
        self.reasoning_effort = "medium"
        self.chat_manager = ChatManager()
        
        # Chat System Prompt
        self.chat_system_prompt = """You are an intelligent central control AI. 
You can control multiple devices connected to this network.
Your goal is to help the user perform tasks across these devices efficiently.

### Capabilities:
1. **Chat**: Answer questions and discuss plans.
2. **Device Control**: You can execute commands or AI tasks on connected devices.
3. **Drafting**: When a user wants to perform an action, you should PLAN it first, then ask for CONFIRMATION.
4. **Device Selection**: You can ask the user to select which devices to target.

### Tool Use:
When the user wants to control devices, you MUST use the `select_devices` tool to ask the user to choose/confirm the target devices.
Don't just assume "all" unless explicitly told "all". Even then, it is safer to pop up the selection dialog.

### Response Format:
You are chatting with the user. Keep it concise + helpful.
"""

        # Enhanced system prompt for better Chain-of-Thought and robustness

        self.system_prompt = """You are an expert AI agent controlling a Windows computer.
Your goal is to achieve the user's request by interacting with the GUI.

### Constraints:
1. You can see the screen and previous history.
2. You must output ONE step at a time.
3. Coordinates MUST be relative (0.0 to 1.0). Center of screen is (0.5, 0.5).
4. If the request is complex, break it down step-by-step.
5. If a popup or unexpected window appears, handle it (e.g., close it or confirm).
6. **CRITICAL**: Before 'type' action, ALWAYS visually check if the Input Method (IME) is in English mode.
   - If you see a '中' icon or Chinese composition window, you MUST press 'shift' or 'ctrl+space' to switch to English/ASCII mode first.
   - Failure to switch to English will cause the task to fail.
7. **STATE CHECK**: Before generating an action, carefully analyze the current screenshot. Does it match the expected state after your previous action?
8. **COMPLETION CHECK**: Do NOT mark status as 'completed' unless the goal is UNDENIABLY finished based on visual evidence. If unsure, verify or wait. Use 'continue' if any doubt exists.
9. **SUGGESTION**: Generally speaking, except for switching input methods, please use the mouse click instead of the shortcut keys.
10. **ATTENTION**: If you find the webpage "P-Remote Control", "P-Remote" or any interface that looks like a remote control dashboard (with tabs like "设备", "分发", "AI", etc.), YOU MUST NOT click any of its controls, buttons, or interact with it in any way. If the task requires using the browser, please create a new tab or switch away from the control panel.
11. **FORBIDDEN**: Do NOT interact with the AI Agent's own control panel or any "Confirm Execution" buttons on the web interface.
12. **ASK USER**: If you are stuck, confused, or hit a critical error where you need user guidance (e.g. "Password required" or "2FA code needed"), use action type 'ask_user' and put your question in 'value'.
13. **MOUSE CURSOR**: The screenshot provided to you will have a visible mouse cursor drawn on it, annotated with "(mouse here)". Pay close attention to the mouse position — it reflects where the mouse currently is on screen. When planning your next action, consider the cursor's current position to decide if you need to move it or click elsewhere.
14. **COORDINATE GRID**: The screenshot has a coordinate grid overlay to help you locate positions precisely:
    - There are 9 vertical and 9 horizontal cyan lines, dividing the screen into a 10x10 grid.
    - The lines are labeled with coordinates: 0.1, 0.2, 0.3, ..., 0.9 (shown as yellow labels at the top edge for X-axis and left edge for Y-axis).
    - Use these grid lines as reference points! For example, if a button is right at the intersection of vertical line 0.3 and horizontal line 0.7, click at target [0.3, 0.7].
    - If a target is between grid lines, estimate the fraction. E.g., halfway between 0.2 and 0.3 is approximately 0.25.


### Action 'press' supports:
- Modifiers: shift, ctrl, alt, lwin
- Functional: enter, esc, backspace, tab, space, capslock, up, down, left, right, home, end, pgup, pgdn
- F-keys: f1-f12
- Usage: "ctrl+c", "shift+a", "alt+f4"

### Action 'move' supports:
- Moving the mouse cursor to a specific coordinate without clicking.
- Useful for hovering over elements or resetting focus.

### Output JSON Format:
{
    "thought": "Deep analysis of the visual state. What is currently on screen? Does it match my expectation? What should I do next?",
    "action_queue": [
        {
            "type": "click" | "double_click" | "right_click" | "type" | "press" | "scroll" | "wait" | "ask_user" | "done" | "fail",
            "target": [x, y],  // Required for click events.
            "value": "string"  // Required for type, press, wait, or ask_user (the question).
        }
    ], // Provide 1 to 5 actions to execute sequentially. Use multiple actions only if you are VERY confident.
    "status": "continue" | "completed" | "failed"
}
""" 
        
    def _load_config(self, config_path: str):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get("ai_key")
            if not self.api_key:
                logger.warning("配置文件中缺少 ai_key, AI 功能将无法使用")
            elif dashscope:
                 dashscope.api_key = self.api_key
        except Exception as e:
            logger.error(f"加载 AI 配置失败: {e}")


    def _call_doubao(self, messages):
        """调用豆包 API (使用结构化输出)"""
        if not self.api_key:
             return {"error": "API key missing"}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # JSON Schema 定义
        schema = {
            "name": "ai_action_execution",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "thought": {
                        "type": "string",
                        "description": "Analysis of the screen and decision making process."
                    },
                    "action_queue": {
                        "type": "array",
                        "description": "Queue of actions to execute sequentially. Provide 1 to 5 actions. Use multiple actions only if you are VERY confident.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["click", "double_click", "right_click", "move", "press", "type", "wait", "scroll", "ask_user", "done", "fail"],
                                    "description": "The type of action to perform."
                                },
                                "target": {
                                    "type": "array",
                                    "items": { "type": "number" },
                                    "description": "Coordinates [x, y]. Use relative coordinates (0.0-1.0) for click/double_click/right_click/move. Use [0, 0] if not applicable."
                                },
                                "value": {
                                    "type": "string",
                                    "description": "Parameter for the action (e.g. key to press, text to type, time to wait, or question for user). Empty string if not applicable."
                                }
                            },
                            "required": ["type", "target", "value"],
                            "additionalProperties": False
                        },
                        "maxItems": 5,
                        "minItems": 1
                    },
                    "status": {
                        "type": "string",
                        "enum": ["continue", "completed", "failed"],
                        "description": "Current status of the task."
                    }
                },
                "required": ["thought", "action_queue", "status"],
                "additionalProperties": False
            }
        }
        
        return self._make_request(messages, schema)

    def chat(self, user_input: str, conv_id: str, system_prompt: str = None) -> Dict[str, Any]:
        """Chat with the AI"""
        if not conv_id:
            conv_id = self.chat_manager.create_conversation()
            
        self.chat_manager.add_message(conv_id, "user", user_input)
        history = self.chat_manager.get_history(conv_id)
        
        messages = [{"role": "system", "content": system_prompt or self.chat_system_prompt}]
        messages.extend(history)
        
        # Schema for Chat Response with potential Device Selection
        schema = {
            "name": "ai_chat_response",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "thought": { "type": "string", "description": "Reasoning about user intent" },
                    "response_text": { "type": "string", "description": "The text response to the user" },
                    "tool_call": {
                        "type": "object",
                        "properties": {
                            "name": { "type": "string", "enum": ["select_devices", "execute_script", "none"]},
                            "params": { 
                                "type": "object",
                                "properties": {
                                    "pre_selection": { "type": "array", "items": {"type":"string"}, "description": "Suggested device names if any"},
                                    "task_description": { "type": "string", "description": "Description of task to perform"},
                                    "script_content": { "type": "string", "description": "The logic or script to execute if applicable"}
                                },
                                "required": ["task_description"],
                                "additionalProperties": False
                            }
                        },
                        "required": ["name", "params"],
                        "additionalProperties": False
                    }
                },
                "required": ["thought", "response_text", "tool_call"],
                "additionalProperties": False
            }
        }
        
        result = self._make_request(messages, schema)
        
        # Parse result
        try:
            if isinstance(result, str):
                parsed = json.loads(result)
            else:
                parsed = result
                
            self.chat_manager.add_message(conv_id, "assistant", parsed.get("response_text", ""))
            return {"conv_id": conv_id, **parsed}
            
        except Exception as e:
            return {"error": str(e), "raw": result}


    def _make_request(self, messages, schema):
        if not self.api_key:
             return {"error": "API key missing"}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": self.DOUBAO_MODEL,
            "max_completion_tokens": 4096, 
            "reasoning_effort": self.reasoning_effort,
            "temperature": 0.3,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": schema
            }
        }
        
        try:
            response = requests.post(
                self.DOUBAO_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=60
            )
            
            if response.status_code != 200:
                return f'{{"error": "API Error {response.status_code}: {response.text}"}}'
                
            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            else:
                 return f'{{"error": "Invalid API response: {result}"}}'

        except Exception as e:
            logger.error(f"Doubao API error: {e}")
            return f'{{"error": "Doubao API request failed: {e}"}}'

    def _call_llm(self, messages):
        # Deprecated: Qwen fallback if needed
        # ... logic unchanged ...
        return {"error": "Legacy LLM call not supported in this version"}

    def step(self, user_goal: str, ocr_results: Dict[str, Any], history: list, image_bytes: bytes = None) -> Dict[str, Any]:
        """执行单步思考"""
        
        if image_bytes:
            # 使用多模态 API (豆包) + 结构化输出
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                },
                {
                    "type": "text",
                    "text": f"GOAL: {user_goal}\n\nOCR TEXT ON SCREEN:\n{json.dumps(ocr_results, ensure_ascii=False)}\n\nHISTORY:\n{json.dumps(history, ensure_ascii=False, indent=2)}\n\nAnalyze the screen and determine the next action. Use the grid lines on the image (labeled 0.1, 0.2, ..., 0.9) to estimate target coordinates precisely."
                }
            ]
            
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content}
            ]
            
            raw_response = self._call_doubao(messages)
            
        else:
            return {"error": "Image input is required for this model"}
        
        # 解析响应
        try:
            if isinstance(raw_response, str):
                return json.loads(raw_response)
            elif isinstance(raw_response, dict):
                 return raw_response
            return {"error": "Invalid response format"}
        except Exception as e:
            logger.error(f"Failed to parse AI response: {e}, RAW: {raw_response}")
            return {"error": str(e), "raw": raw_response}

def create_ocr_client(config_path: Optional[str] = None) -> VolcengineOCR:
    """创建 OCR 客户端"""
    return VolcengineOCR(config_path)

def create_ai_client(config_path: Optional[str] = None) -> AIManager:
    """创建 AI 客户端"""
    return AIManager(config_path)

class AITaskExecutor:
    def __init__(self, ai_client: AIManager, ocr_client: VolcengineOCR,
                 action_executor, cmd_executor, send_status_func: Callable,
                 send_frame_func: Optional[Callable] = None,
                 send_debug_frame_func: Optional[Callable] = None,
                 notification_func: Optional[Callable] = None):
        """
        初始化 AI 任务执行器
        """
        self.ai_client = ai_client
        self.ocr_client = ocr_client
        self.action_executor = action_executor
        self.cmd_executor = cmd_executor
        self.send_status = send_status_func
        self.send_frame = send_frame_func
        self.send_debug_frame = send_debug_frame_func
        self.notification_func = notification_func
        
        # 任务状态
        self._running_tasks: Dict[str, bool] = {}  # task_id -> stop_flag
        self._paused = False
        
        # Conflict Detection (Simple timestamp based)
        self._last_user_activity = 0
        self._input_sim_hook()
        
        # User Interaction Events
        self._task_input_events: Dict[str, threading.Event] = {}
        self._task_answers: Dict[str, str] = {}
        self._task_initiators: Dict[str, str] = {}

    def provide_input(self, task_id: str, text: str):
        """Provide input to a paused task"""
        if task_id in self._task_input_events:
            self._task_answers[task_id] = text
            self._task_input_events[task_id].set()


    def _input_sim_hook(self):
        """Hook into the input simulator (or listen generally) to detect user activity"""
        # Note: This is a simplified simulation. In a real scenario, we'd need a global mouse/keyboard listener 
        # (e.g. pynput) to detect *physical* user input vs *simulated* input.
        # Here we mock it or rely on external signals.
        pass

    def check_conflict_and_pause(self):
        """Check for external user interaction and pause if needed"""
        # Placeholder for actual conflict detection logic
        # For now, we assume no conflict unless explicit stop
        return False
        
    def start_task(self, task_id: str, goal: str, max_steps: int = 30, reasoning_effort: str = "medium", screen_index: int = 0, initiator: str = None, enable_ocr: bool = False):
        """启动 AI 任务 (在新线程中运行)"""
        import threading
        
        if task_id in self._running_tasks:
            self.send_status(task_id, "error", "任务已在运行中")
            return
        
        # Validate reasoning_effort
        if reasoning_effort not in ["low", "medium", "high"]:
            reasoning_effort = "medium"
            
        self._running_tasks[task_id] = False  # stop_flag = False
        self._task_initiators[task_id] = initiator
        
        def run():
            try:
                self._execute_task(task_id, goal, max_steps, reasoning_effort, screen_index, enable_ocr=enable_ocr)
            finally:
                self._running_tasks.pop(task_id, None)
                self._task_initiators.pop(task_id, None)
                
        threading.Thread(target=run, daemon=True, name=f"AI-Task-{task_id}").start()
        self.send_status(task_id, "started", f"开始任务: {goal} (推理强度: {reasoning_effort}, 屏幕: {screen_index}, OCR: {'开' if enable_ocr else '关'})")
    
    def stop_task(self, task_id: str):
        """停止指定任务"""
        if task_id in self._running_tasks:
            self._running_tasks[task_id] = True
            self.send_status(task_id, "stopping", "正在停止任务...")
    
    def stop_all_tasks(self):
        """停止所有正在运行的任务"""
        stopped_count = 0
        for task_id in list(self._running_tasks.keys()):
            if not self._running_tasks.get(task_id, True):  # Only stop if not already stopping
                self._running_tasks[task_id] = True
                self.send_status(task_id, "stopping", "正在停止任务...")
                stopped_count += 1
        logger.info(f"已标记 {stopped_count} 个任务为停止")
    
    def _execute_task(self, task_id: str, goal: str, max_steps: int, reasoning_effort: str = "medium", screen_index: int = 0, enable_ocr: bool = False):
        """执行 AI 任务循环"""
        import time
        
        # Set reasoning effort for this task
        old_reasoning_effort = self.ai_client.reasoning_effort
        self.ai_client.reasoning_effort = reasoning_effort
        
        # Switch to specified screen/monitor
        try:
            if screen_index != 0:  # Only switch if not default screen
                logger.info(f"切换到屏幕 {screen_index} 以执行 AI 任务")
                self.cmd_executor.switch_monitor(screen_index)
            else:
                # Ensure default monitor is active
                self.cmd_executor.ensure_camera_started()
        except Exception as e:
            logger.warning(f"切换屏幕失败: {e}, 继续使用当前屏幕")
        
        history = []
        
        for step_num in range(max_steps):
            # 检查停止标志 (True = 停止)
            if self._running_tasks.get(task_id, False):  # 默认False表示未停止
                self.send_status(task_id, "stopped", "任务已停止")
                # Restore original reasoning effort before return
                self.ai_client.reasoning_effort = old_reasoning_effort
                return
            
            # 冲突检测 (简单的暂停机制)
            if self.check_conflict_and_pause():
                self.send_status(task_id, "paused", "检测到用户操作，任务暂停...")
                time.sleep(2)
                continue

            
            # 1. 截图
            self.cmd_executor.ensure_camera_started()
            img_bytes = self.cmd_executor.get_screenshot()
            if not img_bytes:
                self.send_status(task_id, "error", "无法获取屏幕截图")
                return
            
            # 获取 AI 专用截图 (带鼠标文字标注)
            img_bytes_ai = self.cmd_executor.get_screenshot_for_ai()
            if not img_bytes_ai:
                img_bytes_ai = img_bytes  # 降级: 使用普通截图
            
            # 发送当前帧给前端 (无文字标注的普通截图)
            if self.send_frame:
                self.send_frame(task_id, img_bytes)
            
            # 发送 AI 视角截图 (带网格+光标标注) 用于调试模式
            if self.send_debug_frame and img_bytes_ai:
                self.send_debug_frame(task_id, img_bytes_ai)
            
            # 2. OCR 识别 (可通过 UI 开关控制)
            ocr_data = {}
            if enable_ocr and self.ocr_client:
                try:
                    ocr_result = self.ocr_client.recognize(img_bytes_ai)
                    line_texts = ocr_result.get("line_texts", [])
                    if line_texts:
                        ocr_data = {"screen_text": "\n".join(line_texts)}
                        logger.info(f"OCR 识别到 {len(line_texts)} 行文本")
                except Exception as e:
                    logger.warning(f"OCR 识别失败: {e}")
            
            # 3. AI 思考
            debug_info_str = f"GOAL: {goal}\nOCR TEXT: {json.dumps(ocr_data, ensure_ascii=False)}\nHISTORY:\n{json.dumps(history, ensure_ascii=False, indent=2)}"
            self.send_status(task_id, "running", f"步骤 {step_num + 1}: 豆包AI ({self.ai_client.DOUBAO_MODEL}) 思考中...", debug_info=debug_info_str)
            
            # 传入 img_bytes_ai (带鼠标标注) 启用多模态模式
            step_result = self.ai_client.step(goal, ocr_data, history, image_bytes=img_bytes_ai)
            
            if "error" in step_result:
                self.send_status(task_id, "error", f"AI 错误: {step_result['error']}")
                # 发送错误时的截图
                if self.send_frame and img_bytes:
                    self.send_frame(task_id, img_bytes)
                # Restore reasoning effort
                self.ai_client.reasoning_effort = old_reasoning_effort
                return
            
            thought = step_result.get("thought", "")
            status = step_result.get("status", "continue")
            
            action_queue = step_result.get("action_queue", [])
            # Fallback for backward compatibility
            if "action" in step_result and not action_queue:
                action_queue = [step_result["action"]]
                
            logger.info(f"[AI Task {task_id}] Step {step_num}: {thought} | ActionQueue: {action_queue}")
            
            if not action_queue:
                if status == "completed":
                    self.send_status(task_id, "completed", f"任务完成: {thought}", thought=thought)
                    if self.send_frame: self.send_frame(task_id, img_bytes)
                    if self.notification_func: self.notification_func("任务完成 ✅", f"{thought}\n请回到浏览器确认结果。")
                    return
                elif status == "failed":
                    self.send_status(task_id, "failed", f"任务失败: {thought}", thought=thought)
                    if self.send_frame: self.send_frame(task_id, img_bytes)
                    if self.notification_func: self.notification_func("任务失败 ❌", f"{thought}\n请回到浏览器查看详情。")
                    return
                # if continuing but empty queue
                action_queue = [{"type": "wait", "target": [0,0], "value": "1"}]
                
            queue_aborted = False

            for action_idx, action in enumerate(action_queue):
                act_type = action.get("type", "")
                
                # 4. Handle 'ask_user'
                if act_type == "ask_user":
                    question = action.get("value", "AI request attention")
                    initiator = self._task_initiators.get(task_id)
                    self.send_status(task_id, "asking", question, thought=thought, target_device=initiator)
                    
                    if self.notification_func:
                        self.notification_func("AI 需要您的协助", f"{question}\n请回到浏览器进行回答。")
                    
                    # Setup wait event
                    evt = threading.Event()
                    self._task_input_events[task_id] = evt
                    self._task_answers[task_id] = "" # Reset
                    
                    import time
                    while not evt.is_set():
                        if not self._running_tasks.get(task_id, False): # If stopped
                             return
                        time.sleep(0.5)
                    
                    user_ans = self._task_answers.get(task_id, "")
                    
                    del self._task_input_events[task_id]
                    self._task_answers.pop(task_id, None)
                    
                    history.append({
                        "step": step_num,
                        "thought": thought,
                        "action": action,
                        "result": f"User Answered: {user_ans}"
                    })
                    queue_aborted = True
                    break

                # 5. 检查完成状态
                if status == "completed" or act_type == "done":
                    self.send_status(task_id, "completed", f"任务完成: {thought}", thought=thought)
                    if self.send_frame:
                        self.send_frame(task_id, img_bytes)
                    if self.notification_func:
                        self.notification_func("任务完成 ✅", f"{thought}\n请回到浏览器确认结果。")
                    return
                
                if status == "failed" or act_type == "fail":
                    self.send_status(task_id, "failed", f"任务失败: {thought}", thought=thought)
                    if self.send_frame:
                        self.send_frame(task_id, img_bytes)
                    if self.notification_func:
                        self.notification_func("任务失败 ❌", f"{thought}\n请回到浏览器查看详情。")
                    return
                
                # 5. 执行动作
                step_display = f"{step_num + 1}.{action_idx + 1}" if len(action_queue) > 1 else str(step_num + 1)
                self.send_status(task_id, "running", f"步骤 {step_display}: 执行 {act_type}", thought=thought, action_data=action)
                
                success = False
                error_detail = ""
                try:
                    success = self._execute_action(action)
                except Exception as e:
                    error_detail = str(e)
                    logger.error(f"Action execution error: {e}")
                
                # 6. 记录历史
                step_record = {
                    "step": step_num,
                    "thought": thought if action_idx == 0 else "后续动作...",
                    "action": action,
                    "result": "success" if success else f"failed: {error_detail}" if error_detail else "failed"
                }
                history.append(step_record)
                
                if not success:
                    fail_msg = f"步骤 {step_display}: 动作执行失败"
                    if error_detail:
                        fail_msg += f" ({error_detail})"
                        goal = f"{goal}\n\n[PREVIOUS ERROR at step {step_display}]: Action '{act_type}' failed with error: {error_detail}."
                    self.send_status(task_id, "running", fail_msg + "，中止队列...", action_data=action, step_result=step_record)
                    queue_aborted = True
                    break
                else:
                    self.send_status(task_id, "running", f"步骤 {step_display}: {act_type} 执行成功", thought=thought, action_data=action, step_result=step_record)
                
                # 7. 发送帧并等待响应 (对于队列中的每一个动作)
                time.sleep(1.0)  # 程序每执行一步自动插入一个等待1秒以等待UI响应

                if self.send_frame:
                    new_img = self.cmd_executor.get_screenshot()
                    if new_img:
                        self.send_frame(task_id, new_img)
                
                if self.notification_func:
                     self.notification_func(f"步骤 {step_display} 完成", f"AI 已执行: {act_type}\n请回到浏览器查看。")
            
            if queue_aborted:
                continue
        
        # 达到最大步骤 - 发送最终帧
        self.send_status(task_id, "stopped", "达到最大步骤限制")
        if self.notification_func:
            self.notification_func("任务停止", "达到最大步骤数限制")
        if self.send_frame:
            final_img = self.cmd_executor.get_screenshot()
            if final_img:
                self.send_frame(task_id, final_img)
        
        # Restore original reasoning effort
        self.ai_client.reasoning_effort = old_reasoning_effort

    def _execute_action(self, action: Dict) -> bool:
        """执行单个 AI 生成的动作"""
        import time
        
        act_type = action.get("type", "")
        # Handle coordinate list from schema
        target = action.get("target", [0, 0])
        value = action.get("value", "")
        
        sw, sh = self.cmd_executor.get_screen_size()
        
        if act_type in ["click", "double_click", "right_click", "move"]:
            if isinstance(target, list) and len(target) >= 2:
                x, y = target[0], target[1]
                
                # Intelligent coordinate parsing
                # If x,y <= 1.0, treat as ratio. If > 1.0, treat as pixels.
                ratio_x = x
                ratio_y = y
                
                if x > 1.0 or y > 1.0:
                    if sw > 0 and sh > 0:
                        ratio_x = x / sw
                        ratio_y = y / sh
                    else:
                         ratio_x = 0.5 # Safe fallback
                         ratio_y = 0.5
                
                # Perform Action
                if act_type == "click":
                    ok, msg = self.action_executor.click_position(ratio_x, ratio_y)
                elif act_type == "move":
                    try:
                        # Convert ratio to pixels directly
                        px = int(ratio_x * sw)
                        py = int(ratio_y * sh)
                        self.action_executor.input_sim.move(px, py, sw, sh)
                        ok, msg = True, f"Move to ({ratio_x:.2f}, {ratio_y:.2f})"
                    except Exception as e:
                        ok, msg = False, str(e)
                elif act_type == "right_click":
                     try:
                        # Convert ratio to pixels for input_sim
                        px, py, pw, ph = self.action_executor._get_coord_converter().position_to_pixel([ratio_x, ratio_y, 0.01, 0.01])
                        # Randomize slightly for human-like behavior
                        import random
                        cx = px + random.uniform(0, pw)
                        cy = py + random.uniform(0, ph)
                        
                        self.action_executor.input_sim.move(cx, cy, sw, sh)
                        time.sleep(0.05)
                        self.action_executor.input_sim.click(cx, cy, "right", "click", sw, sh)
                        ok, msg = True, f"Right click at ({ratio_x:.2f}, {ratio_y:.2f})"
                     except Exception as e:
                        ok, msg = False, str(e)
                else: # double_click
                    # Manually call input_sim via action_executor or add helper
                    # Using action_executor public interface if possible, or direct input_sim
                    try:
                        # Convert ratio to pixels for input_sim
                        px, py, pw, ph = self.action_executor._get_coord_converter().position_to_pixel([ratio_x, ratio_y, 0.01, 0.01])
                        # Randomize slightly for human-like behavior
                        import random
                        cx = px + random.uniform(0, pw)
                        cy = py + random.uniform(0, ph)
                        
                        self.action_executor.input_sim.move(cx, cy, sw, sh)
                        time.sleep(0.05)
                        self.action_executor.input_sim.double_click(cx, cy, "left", sw, sh)
                        ok, msg = True, f"Double click at ({ratio_x:.2f}, {ratio_y:.2f})"
                    except Exception as e:
                        ok, msg = False, str(e)

                logger.info(f"Action {act_type} at ({x}, {y}) -> ratio ({ratio_x:.4f}, {ratio_y:.4f}): {ok}, {msg}")
                return ok
            return False
            
        elif act_type == "type":
            if value:
                # 先点击目标位置 (如果提供了坐标)
                if isinstance(target, list) and len(target) >= 2:
                    x, y = target[0], target[1]
                    ratio_x, ratio_y = x, y
                    if x > 1.0 or y > 1.0:
                        if sw > 0 and sh > 0:
                            ratio_x = x / sw
                            ratio_y = y / sh
                        else:
                            ratio_x, ratio_y = 0.5, 0.5
                    self.action_executor.click_position(ratio_x, ratio_y)
                    time.sleep(0.2)
                self.action_executor.input_sim.write_text(str(value))
                time.sleep(0.1)
                return True
            return False
            
        elif act_type == "scroll":
            try:
                clicks = int(value) if value else 3
                self.action_executor.input_sim.scroll(clicks)
                return True
            except:
                return False
            
        elif act_type == "wait":
            try:
                wait_time = float(value) if value else 1.0
                time.sleep(min(wait_time, 10))
                return True
            except:
                return False
            
        elif act_type == "press":
            if value:
                keys = value.split("+") if "+" in value else [value]
                self.action_executor.input_sim.press_sequence(keys)
                return True
            return False
        
        return False
