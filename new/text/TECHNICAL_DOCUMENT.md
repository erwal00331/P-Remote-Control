# Technical Documentation | 技术文档

[English](#english) | [中文](#中文)

---

## <a id="english"></a>English

### 1. Architecture Overview
The application follows a modular architecture combining high-performance media transmission with AI-driven visual decision-making. 

### 2. File and Class Structure Details

#### 2.1 `code/api_manager.py` (AI & OCR Core)
Interfaces with external LLM and Vision API models to perceive the screen and decide actions.
- **`VolcengineOCR`**: Encapsulates Volcengine's generic text recognition API. Processes screenshots and handles HMAC authorization signing.
- **`ChatManager`**: Manages conversational context in memory. Acts as a rolling context window for multi-turn AI interactions.
- **`AIManager`**: The central intelligent router. Coordinates the Chain-of-Thought processing logic using the Multi-Modal Doubao model, utilizing a strict JSON Schema output structure to sanitize actions.
- **`AITaskExecutor`**: Manages the multi-threaded execution loop of AI tasks. Deals with pausing/resuming tasks, sending system status, frame updates, conflict detection (`_input_sim_hook`), and user prompts (`ask_user`).

#### 2.2 `code/p2p.py` (WebRTC Connection Engine)
Handles low-latency video streaming to clients through established Peer-to-Peer networks.
- **`ScreenShareTrack`**: Inherits from `aiortc.VideoStreamTrack`. Iteratively captures frames up to native configurations, resizing when limits are exceeded. Includes timeout caching and failsafe screens (synthesized "NO SIGNAL" output on capture disruption).
- **`P2PManager`**: Facilitates the complete lifecycle of WebRTC connections. Manages RTCPeerConnections, resolves ICE gathering, negotiates SDP offers/answers, and coordinates DataChannels.

#### 2.3 `code/executor.py` (Low-Level Execution)
Connects the application to the Operating System's input/output functions.
- **`CameraManager`**: Oversees real-time screen capture operations with safety checks. Contains methods to draw targeting grids over frames and dynamically tracks the mouse cursor to assist the LLM's visual navigation.
- **`InputSim`**: Simulates local keyboard strokes and mouse movements/clicks specifically leveraging Windows `user32.dll`.
- **`CommandExecutor`**: The primary wrapper instance encompassing `CameraManager` and `InputSim` acting as the main interface for external modules.

#### 2.4 `code/platform_compat.py` (Platform Compatibility Wrapper)
Resolves dependencies conditionally based on OS.
- **`PlatformInfo`**: Probes the underlying OS details (Windows vs Linux vs macOS) at startup.
- **`CrossPlatformInputSim` / `CrossPlatformCameraManager`**: Safe abstractions that intelligently downgrade from `user32`/`dxcam` on Windows to `mss`/`xdotool`/`pynput` on POSIX environments.

#### 2.5 `code/ws.py` & `code/video_service.py` (Networking & Telemetry)
- **`NetworkManager` (`ws.py`)**: An asynchronous supervisor spanning both raw TCP and WebSocket handling. Responsible for heartbeat lifecycles, connection persistency, and byte-exact messaging chunks.
- **`VideoService` (`video_service.py`)**: Dedicated threaded service managing high-frequency screen polls, implementing intelligent FPS degradation dynamically if the screen is idle.

#### 2.6 `code/run.py` & `code/ui_app.py` (Orchestration & UI)
- **`run.py`**: The main entry point script routing commands, initiating subsystems, loading configurations, and listening to events (`process_msg`).
- **`App` (`ui_app.py`)**: Builds the Control Panel UI through `tkinter`. Implements tabs for logs, device configuration, AI workflows, and standard local macros.

#### 2.7 `code/automation/` (Automation Engine)
A dedicated module subsystem focusing on scripted coordinates, macros, and positional data storage.
- **`ActionExecutor` (`action_executor.py`)**: Iterates through compiled UI macros recursively, integrating loop statements, jump commands, and coordinate resolutions to execute scripts automatically.
- **`CoordinateConverter` (`coordinate_utils.py`)**: Centralizes the conversion between raw pixel values and normalized ratio coordinates (0.0 - 1.0) allowing resolution-agnostic macros.
- **`DataManager` (`data_manager.py`)**: Provides safe I/O locking for reading/saving automated sequences, saved OCR zones, and customized button macros to the local filesystem (`.json`).
- **`OcrManager` (`ocr_manager.py`)**: Targets specific, pre-defined normalized boundary boxes on screen. Identifies strings selectively and casts them to user-defined data structures (e.g., Dates, Integers).

---

## <a id="中文"></a>中文

### 1. 架构概览
该应用采用了将高性能媒体传输与基于 AI 的视觉决策相结合的模块化架构，拆分了底层输入、网络流、平台兼容、自动化和智能化组件以保证高健壮性。

### 2. 文件与类结构详解

#### 2.1 `code/api_manager.py` (AI 与 OCR 核心)
提供外部大语言模型与机器视觉 API 的交互封装及逻辑组织。
- **`VolcengineOCR`**：火山引擎通用文字识别 API 封装。截取底层流数据后构建并进行 HMAC-SHA256 签名鉴权，获取文本坐标。
- **`ChatManager`**：在内存中简单但严格控制大小的对话上下文管理器，为 AI 提供历史会话信息。
- **`AIManager`**：核心的智能中枢。统筹与豆包(Doubao)等大模型的交互，负责多模态理解与思维链（Chain-of-Thought, CoT）下发。通过使用严格的 JSON Schema 强制规范化输出模型。
- **`AITaskExecutor`**：建立在多线程上的 AI 任务状态机层。集成了任务开始/停止、暂停/继续、冲突干预检测功能。同时向上能将当前运行的“动作节点”、“思考内容”、“调试网格图像”回传给UI以及用户交互(`ask_user`)。

#### 2.2 `code/p2p.py` (WebRTC P2P连接引擎)
管理核心的极低延迟的端到端屏幕流与控制通道。
- **`ScreenShareTrack`**：继承自 `aiortc.VideoStreamTrack`，用于按需提取和缩放当前的视频帧。配置了自动缓存控制，当图像获取崩溃抛出异常或长时无信号时可以降级发送虚拟“NO SIGNAL”蓝屏帧保护网络层。
- **`P2PManager`**：P2P 生命周期控制核心节点。涵盖建立 RTCPeerConnection 实例，采集 ICE 候选项，协商 SDP (Offer/Answer)，挂载视频轨道并管理用于双向指令推送的数据通道(DataChannel)。

#### 2.3 `code/executor.py` (底层执行处理)
提供与操作系统层对接的一系列具体动作输入及屏幕捕获能力。
- **`CameraManager`**：主要用于维护截屏逻辑、保障内存状态与并发截屏。内建在图像上绘制透明坐标系与辅助光标层 (`draw_grid_overlay`, `annotate_jpeg_with_cursor_label`) 用于改善语言模型的空间感知能力。
- **`InputSim`**：基础环境模拟发送器，深度封装 Windows `user32.dll` 进行原生的键盘映射按压、以及包含绝对坐标参数的高精度鼠标活动轨迹再现。
- **`CommandExecutor`**：最高层的外呼类，把摄像头图像、键鼠移动收口到一个实例对象供其它的上游控制路由调用。

#### 2.4 `code/platform_compat.py` (跨平台兼容封装)
消除平台差异的依赖层适配器。
- **`PlatformInfo`**：应用启动时收集并判定当前宿主环境架构(Windows, Linux, Darwin)。
- **`CrossPlatformInputSim` & `CrossPlatformCameraManager`**：提供与原生API相同的智能安全壳。如果在非 Windows 环境，会自动降回使用原生可运行的 `pynput`, `xdotool` 或是 `mss` 等第三方低阶库。

#### 2.5 `code/ws.py` & `code/video_service.py` (网络与遥测服务)
- **`NetworkManager` (`ws.py`)**：网络主管对象，兼容纯 TCP 长连接或 WebSocket 场景。具有稳健的断线重连、粘包拆包（按大小分配），心跳维活机制，并确保大字节安全派发给回调。
- **`VideoService` (`video_service.py`)**：运行在独立线程采集画面的视频源服务。采用了帧校验比对策略，长期静止画面会自动显著降低系统轮询采样率以节省带宽和发热量。

#### 2.6 `code/run.py` & `code/ui_app.py` (中央编排层与用户接口)
- **`run.py`**：应用的主管入口模块；组织并实例化内部功能类，管理权限提权(`_elevate_admin`)，以及消息分发引擎(`process_msg`)响应命令集。
- **`App` (`ui_app.py`)**：由 `tkinter` 构建的图形化控制面板界面。将所有状态输出展示为不同功能标签页（诸如聊天/日志日志面本、功能下发、多设备挑选组件）。

#### 2.7 `code/automation/` (自动化引擎模块)
专注于静态的坐标映射、多重循环、文件存取与逻辑驱动库。
- **`ActionExecutor` (`action_executor.py`)**：脚本的解析与运行机器。可通过建立跳转哈希表和递归层来处理序列内的 `If...Else...`，或多层 `For Loop` 和基于像素相对偏移的定位任务。
- **`CoordinateConverter` (`coordinate_utils.py`)**：集中式坐标数学运算基类。保证所有的自动坐标数据能够转换为与目标显示器实际分辨率无严格强制挂钩的 `0.0-1.0` 浮点空间比例。
- **`DataManager` (`data_manager.py`)**：使用包含重入锁和容灾备份功能的持久化管理器。记录诸如录制的固定流程脚本，已保存的特殊按钮数据或特征边界 JSON 文件。
- **`OcrManager` (`ocr_manager.py`)**：框选识别业务处理器。针对配置指定的区域裁剪图像并过滤出所期盼的正确数据结构(整型、纯文本等)给其他执行模块使用。
