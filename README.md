# AI Remote Control Agent | AI 远程控制助手

[English](#english) | [中文](#中文)

---

## <a id="english"></a>English

### Overview
This project is an intelligent screen sharing and automation tool that leverages WebRTC for low-latency P2P screen streaming and integrates Multi-modal AI to analyze screen content and execute automated tasks. 

### Key Features
- **P2P Screen Sharing (WebRTC):** High performance, low-latency screen streaming with dynamic resolution and frame rate adjustments.
- **AI Task Automation:** Uses **Volcengine Doubao LLM** and OCR to visually comprehend the screen and dispatch a sequence of simulated inputs (clicks, keyboard strokes).
- **Coordinate Grid System:** Uses relative screen coordinates projected through a grid to pinpoint UI elements accurately.
- **Action Queueing & Notifications:** Supports batched task execution with dynamic task queues, pause/resume mechanisms, and real-time pop-up notifications.

### Project Structure
- `code/api_manager.py`: Integrates LLM and OCR, executing multi-modal prompts with action planning.
- `code/p2p.py`: Manages the WebRTC peer connection, including SDP negotiation, ICE candidate gathering, and video track stream management.
- `code/executor.py` & `automation/`: Simulates multi-platform UI interactions.

---

## <a id="中文"></a>中文

### 项目简介
本项目是一个智能的屏幕共享与自动化工具。它利用 WebRTC 提供低延迟的 P2P 屏幕流传输，并集成了多模态 AI 模型，实现对屏幕内容的视觉解析与自动化任务执行。

### 核心特性
- **P2P 屏幕共享 (WebRTC)：** 高性能、低延迟的屏幕流媒体传输，支持动态分辨率与黑屏/异常状态处理。
- **AI 任务自动化：** 基于**火山引擎豆包大模型**与 OCR 技术，AI 可以视觉理解屏幕内容，并逐步规划、执行输入指令（鼠标点击、拖拽、键盘输入）。
- **坐标网格系统：** 通过网格覆盖技术提取相对坐标，使得大模型能够精准点击界面元素。
- **动作队列与系统通知：** 支持将高置信度动作组合排列执行，具备完善的异常中断机制，并通过系统原生弹窗向用户提供实时进度通知。

### 目录结构
- `code/api_manager.py`：负责 LLM 及 OCR 的 API 调用、请求意图解析及任务执行器状态管理。
- `code/p2p.py`：负责处理 WebRTC 连接（SDP、ICE）与视频数据流的管理。
- `code/executor.py` 与 `automation/`：负责跨平台的底层键鼠接口模拟。
