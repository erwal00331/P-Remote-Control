import tkinter as tk
from tkinter import ttk
import threading
import time
import ctypes
import queue
import logging

logger = logging.getLogger(__name__)

class App:
    def __init__(self, root, event_callback):
        self.root = root
        self.root.title("P-Remote UI")
        self.root.geometry("320x550")
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.root.attributes("-topmost", True)
        
        self.event_callback = event_callback
        self.is_visible = True
        self.msg_queue = queue.Queue()
        
        self.running = True
        
        self.setup_ui()
        self.start_hotkey_listener()
        
        # Start queue processing
        self.process_queue()
        
    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')
        
        # Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=2, pady=2)
        
        self.tab_main = ttk.Frame(self.notebook)
        self.tab_ai = ttk.Frame(self.notebook)
        self.tab_auto = ttk.Frame(self.notebook)
        
        self.notebook.add(self.tab_main, text='控制')
        self.notebook.add(self.tab_ai, text='AI')
        self.notebook.add(self.tab_auto, text='自动化')
        
        self.setup_main_tab()
        self.setup_ai_tab()
        self.setup_auto_tab()
        
        # Status Bar
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var, relief='sunken', anchor='w').pack(side='bottom', fill='x')

    def setup_main_tab(self):
        pad = 5
        
        # Connection
        lf_conn = ttk.LabelFrame(self.tab_main, text="连接与权限")
        lf_conn.pack(fill='x', padx=pad, pady=pad)
        
        f_row1 = ttk.Frame(lf_conn)
        f_row1.pack(fill='x', padx=5, pady=2)
        ttk.Label(f_row1, text="控制模式:").pack(side='left')
        self.mode_var = tk.StringVar(value="p2p")
        ttk.Radiobutton(f_row1, text="P2P", variable=self.mode_var, value="p2p", command=lambda: self.event_callback("set_mode", "p2p")).pack(side='left', padx=5)
        ttk.Radiobutton(f_row1, text="TCP", variable=self.mode_var, value="tcp", command=lambda: self.event_callback("set_mode", "tcp")).pack(side='left')
        
        f_row2 = ttk.Frame(lf_conn)
        f_row2.pack(fill='x', padx=5, pady=2)
        ttk.Label(f_row2, text="允许控制:").pack(side='left')
        self.allow_control_var = tk.StringVar(value="admin")
        cb = ttk.Combobox(f_row2, textvariable=self.allow_control_var, values=["admin", "any", "none"], width=10, state="readonly")
        cb.pack(side='left', padx=5)
        cb.bind("<<ComboboxSelected>>", self.on_allow_change)
        
        # Devices
        lf_dev = ttk.LabelFrame(self.tab_main, text="在线设备 (Ctrl+R 刷新)")
        lf_dev.pack(fill='both', expand=True, padx=pad, pady=pad)
        
        self.device_list = tk.Listbox(lf_dev, height=6)
        self.device_list.pack(fill='both', expand=True, padx=pad, pady=pad)
        self.device_list.bind('<<ListboxSelect>>', self.on_device_select)
        
        f_btns = ttk.Frame(lf_dev)
        f_btns.pack(fill='x', padx=pad, pady=2)
        ttk.Button(f_btns, text="连接选中", command=self.on_connect_p2p).pack(side='left', padx=2)
        ttk.Button(f_btns, text="刷新", command=lambda: self.event_callback("list_devices", {})).pack(side='right', padx=2)

        # Video
        lf_vid = ttk.LabelFrame(self.tab_main, text="视频控制")
        lf_vid.pack(fill='x', padx=pad, pady=pad)
        
        f_vid = ttk.Frame(lf_vid)
        f_vid.pack(fill='x', padx=pad, pady=pad)
        ttk.Button(f_vid, text="▶ 开启视频", command=lambda: self.event_callback("start_video", {})).pack(side='left', fill='x', expand=True)
        ttk.Button(f_vid, text="⏹ 停止视频", command=lambda: self.event_callback("stop_video", {})).pack(side='left', fill='x', expand=True, padx=5)
        
        f_win = ttk.Frame(lf_vid)
        f_win.pack(fill='x', padx=pad, pady=2)
        ttk.Label(f_win, text="屏幕ID:").pack(side='left')
        self.win_entry = ttk.Entry(f_win, width=5)
        self.win_entry.insert(0, "0")
        self.win_entry.pack(side='left', padx=5)
        ttk.Button(f_win, text="切换", command=self.change_monitor, width=6).pack(side='left')

    def setup_ai_tab(self):
        pad = 5
        self.chat_history = tk.Text(self.tab_ai, height=15, state='disabled', wrap='word', font=("Segoe UI", 9))
        self.chat_history.pack(fill='both', expand=True, padx=pad, pady=pad)
        
        f_inp = ttk.Frame(self.tab_ai)
        f_inp.pack(fill='x', padx=pad, pady=pad)
        self.chat_entry = ttk.Entry(f_inp)
        self.chat_entry.pack(side='left', fill='x', expand=True)
        self.chat_entry.bind("<Return>", self.send_chat)
        ttk.Button(f_inp, text="发送", command=self.send_chat, width=8).pack(side='right', padx=(5,0))
        
        f_set = ttk.Frame(self.tab_ai)
        f_set.pack(fill='x', padx=pad, pady=2)
        ttk.Label(f_set, text="推理:").pack(side='left')
        self.reasoning_var = tk.StringVar(value="medium")
        ttk.OptionMenu(f_set, self.reasoning_var, "medium", "low", "medium", "high").pack(side='left')
        
        self.ocr_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_set, text="OCR", variable=self.ocr_var).pack(side='left', padx=5)
        
        ttk.Button(f_set, text="清除记忆", command=lambda: self.event_callback("clear_memory", {})).pack(side='right')

    def setup_auto_tab(self):
        pad = 5
        ttk.Label(self.tab_auto, text="可用脚本:").pack(anchor='w', padx=pad, pady=pad)
        self.script_list = tk.Listbox(self.tab_auto)
        self.script_list.pack(fill='both', expand=True, padx=pad, pady=pad)
        self.script_list.bind('<Double-Button-1>', self.run_script)
        
        f_btn = ttk.Frame(self.tab_auto)
        f_btn.pack(fill='x', padx=pad, pady=pad)
        ttk.Button(f_btn, text="刷新", command=lambda: self.event_callback("load_scripts", {})).pack(side='left', fill='x', expand=True)
        ttk.Button(f_btn, text="运行选中", command=lambda: self.run_script(None)).pack(side='left', fill='x', expand=True, padx=5)
        ttk.Button(f_btn, text="停止", command=lambda: self.event_callback("stop_scripts", {})).pack(side='right', fill='x', expand=True)

    def process_queue(self):
        try:
            while True:
                task = self.msg_queue.get_nowait()
                action = task.get("action")
                data = task.get("data")
                
                if action == "log":
                    self.status_var.set(str(data))
                elif action == "chat_msg":
                    self.append_chat(data.get("sender"), data.get("text"))
                elif action == "update_devices":
                    self.update_devices(data)
                elif action == "update_scripts":
                    self.update_scripts(data)
                elif action == "device_selection":
                    self.show_device_selection_dialog(data)
                
                self.msg_queue.task_done()
        except queue.Empty:
            pass
        finally:
            if self.running:
                self.root.after(100, self.process_queue)

    def queue_update(self, action, data):
        self.msg_queue.put({"action": action, "data": data})

    # --- Interaction Handlers ---
    def on_allow_change(self, event):
        val = self.allow_control_var.get()
        self.event_callback("set_allow_control", {"value": val})

    def on_device_select(self, event):
        pass # Just selection
        
    def on_connect_p2p(self):
        sel = self.device_list.curselection()
        if not sel: return
        raw = self.device_list.get(sel[0])
        name = raw.split(" ", 1)[1]
        self.event_callback("connect_p2p", {"target": name})

    def change_monitor(self):
        try:
            w = int(self.win_entry.get())
            self.event_callback("change_window", {"window": w})
        except: pass

    def send_chat(self, event=None):
        txt = self.chat_entry.get().strip()
        if not txt: return
        self.chat_entry.delete(0, 'end')
        self.append_chat("User", txt)
        self.event_callback("chat", {"message": txt, "reasoning": self.reasoning_var.get(), "enable_ocr": self.ocr_var.get()})

    def run_script(self, event):
        sel = self.script_list.curselection()
        if not sel: return
        name = self.script_list.get(sel[0])
        self.event_callback("run_script", {"name": name})

    def update_devices(self, dev_dict):
        self.cached_devices = list(dev_dict.keys()) # Store for fallback
        self.device_list.delete(0, 'end')
        for k, v in dev_dict.items():
            st = "●" if v.get("client") == "在线" else "○"
            self.device_list.insert('end', f"{st} {k}")

    def update_scripts(self, script_dict):
        self.script_list.delete(0, 'end')
        for k in script_dict.keys():
            self.script_list.insert('end', k)

    def append_chat(self, sender, text):
        self.chat_history.configure(state='normal')
        tag = "user" if sender.lower() == "user" else "ai"
        self.chat_history.insert("end", f"{sender}: ", "system")
        self.chat_history.insert("end", f"{text}\n", tag)
        self.chat_history.see("end")
        self.chat_history.configure(state='disabled')

    # Hotkey Logic
    def start_hotkey_listener(self):
        t = threading.Thread(target=self._hotkey_loop, daemon=True)
        t.start()

    def _hotkey_loop(self):
        user32 = ctypes.windll.user32
        while self.running:
            # Ctrl=0x11, U=0x55. high bit set = pressed
            if (user32.GetAsyncKeyState(0x11) & 0x8000) and (user32.GetAsyncKeyState(0x55) & 0x8000):
                self.root.after(0, self.toggle_window)
                time.sleep(0.3)
            time.sleep(0.05)
            
    def toggle_window(self):
        if self.is_visible: 
            self.hide_window()
        else: 
            self.show_window()

    def hide_window(self):
        self.root.withdraw()
        self.is_visible = False
        
    def show_window(self):
        self.root.deiconify()
        self.is_visible = True

    def show_device_selection_dialog(self, data):
        # 强制显示主窗口
        self.show_window()
        self.notebook.select(self.tab_ai)
        
        dlg = tk.Toplevel(self.root)
        dlg.title("选择设备")
        dlg.transient(self.root)
        dlg.attributes("-topmost", True)
        dlg.resizable(True, True)
        
        ttk.Label(dlg, text="任务: " + str(data.get("task_description", "")), wraplength=430).pack(padx=8, pady=(8, 4))
        
        # 列标题行
        hdr = ttk.Frame(dlg)
        hdr.pack(fill='x', padx=8, pady=(0, 2))
        ttk.Label(hdr, text="设备名称", width=18, anchor='w').pack(side='left')
        ttk.Label(hdr, text="最大步数", width=8, anchor='center').pack(side='left', padx=(4, 0))
        ttk.Label(hdr, text="推理强度", width=10, anchor='center').pack(side='left', padx=(4, 0))
        ttk.Separator(dlg, orient='horizontal').pack(fill='x', padx=8, pady=(0, 2))
        
        # 可滚动区域
        outer = ttk.Frame(dlg)
        outer.pack(fill='both', expand=True, padx=8, pady=2)
        
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        frame_list = ttk.Frame(canvas)
        
        frame_list.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=frame_list, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # 鼠标滚轮支持
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all('<MouseWheel>', _on_mousewheel)
        
        # 现有的设备列表 (从 run.py 传入，或者使用本地缓存)
        available_devices = data.get("devices_list", [])
        if not available_devices and hasattr(self, 'cached_devices'):
            available_devices = self.cached_devices
            
        pre_selection = set(data.get("pre_selection", []))
        
        self.selected_vars = {}       # dev -> BooleanVar
        self.device_max_steps = {}    # dev -> IntVar
        self.device_reasoning = {}    # dev -> StringVar
        
        REASONING_OPTIONS = ["low", "medium", "high"]
        
        for dev in available_devices:
            row = ttk.Frame(frame_list)
            row.pack(fill='x', pady=1)
            
            var = tk.BooleanVar(value=(dev in pre_selection))
            cb = ttk.Checkbutton(row, text=dev, variable=var, width=18)
            cb.pack(side='left')
            self.selected_vars[dev] = var
            
            steps_var = tk.IntVar(value=30)
            steps_entry = ttk.Spinbox(row, from_=1, to=200, textvariable=steps_var, width=6)
            steps_entry.pack(side='left', padx=(4, 0))
            self.device_max_steps[dev] = steps_var
            
            reason_var = tk.StringVar(value="medium")
            reason_menu = ttk.OptionMenu(row, reason_var, "medium", *REASONING_OPTIONS)
            reason_menu.config(width=7)
            reason_menu.pack(side='left', padx=(4, 0))
            self.device_reasoning[dev] = reason_var
        
        # 动态调整窗口高度，最多 6 行设备
        n = max(len(available_devices), 1)
        row_h = 30
        canvas_h = min(n * row_h, 6 * row_h) + 10
        canvas.configure(height=canvas_h)
        dlg.update_idletasks()
        dlg.geometry(f"460x{200 + canvas_h}")
        
        ttk.Separator(dlg, orient='horizontal').pack(fill='x', padx=8, pady=(2, 4))
        
        def on_confirm():
            canvas.unbind_all('<MouseWheel>')
            selected = [d for d, v in self.selected_vars.items() if v.get()]
            device_configs = {
                d: {
                    "max_steps": self.device_max_steps[d].get(),
                    "reasoning_effort": self.device_reasoning[d].get()
                }
                for d in selected
            }
            self.event_callback("confirm_device_selection", {
                "devices": selected,
                "device_configs": device_configs,
                "task_description": data.get("task_description")
            })
            dlg.destroy()
            self.append_chat("System", f"已确认设备: {selected}")
        
        def on_cancel():
            canvas.unbind_all('<MouseWheel>')
            dlg.destroy()
        
        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=(0, 8))
        ttk.Button(btn_frame, text="取消", command=on_cancel, width=10).pack(side='left', padx=4)
        ttk.Button(btn_frame, text="确认执行", command=on_confirm, width=10).pack(side='left', padx=4)
