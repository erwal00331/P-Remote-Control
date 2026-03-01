// ================= 全局状态 =================
let ws;
let targetDev = null;
let controlMode = "p2p";
let isSelfControl = false;  // 自控制模式标志
let localDeviceName = null; // 本地设备名称
let requestCallbacks = {};
let platformData = null; // 平台检测数据

const img = document.getElementById("remote-screen");
const statBar = document.getElementById("status-bar");
const p2pStat = document.getElementById("p2p-stat");

// 设备数据
let cachedDevices = {};
let selectedGroups = new Set();

// 自动化数据
let buttonsData = { positions: {}, groups: {} };
let ocrData = {};
let sequencesData = {};
let currentScript = []; // 当前编辑中的脚本

// 标记模式
let markMode = null; // 'button' | 'ocr' | null
let markStart = null;

// ================= 工具函数 =================
function showToast(msg, ms = 2000) {
  statBar.innerText = msg;
  statBar.style.opacity = 1;
  setTimeout(() => (statBar.style.opacity = 0), ms);
}

function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

function switchTab(tabName) {
  // Update Tab Styling
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.remove("active");
  });
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));

  const activeBtn = document.querySelector(`.tab-btn[onclick="switchTab('${tabName}')"]`);
  if (activeBtn) activeBtn.classList.add("active");
  document.getElementById(`tab-${tabName}`).classList.add("active");

  // Handle AI Tab and Remote Screen Visibility
  const mainChatOverlay = document.getElementById("ai-chat-overlay");
  const remoteScreen = document.getElementById("remote-screen");
  const aiMainFrame = document.getElementById("ai-main-frame");

  if (tabName === "ai") {
    if (mainChatOverlay) mainChatOverlay.classList.remove("hidden");
    if (remoteScreen) remoteScreen.classList.add("hidden");
    if (aiMainFrame) aiMainFrame.classList.add("active"); // Keep legacy container logic if needed

    refreshAiSidebarDevices();
  } else {
    if (mainChatOverlay) mainChatOverlay.classList.add("hidden");
    // Only show remote screen if NOT in AI tab (and defaulting to show)
    if (remoteScreen) remoteScreen.classList.remove("hidden");
    if (aiMainFrame) aiMainFrame.classList.remove("active");
  }

  // Load Data
  if (tabName === "buttons") loadButtons();
  else if (tabName === "ocr") loadOcrRegions();
  else if (tabName === "scripts" || tabName === "dispatch" || tabName === "ai") loadSequences();
}

function refreshAiSidebarDevices() {
  const list = document.getElementById("ai-sidebar-dev-list");
  if (!list) return;
  list.innerHTML = "";

  // Sort online first
  const devices = Object.entries(cachedDevices).sort((a, b) => {
    if (a[1].client === "在线" && b[1].client !== "在线") return -1;
    if (a[1].client !== "在线" && b[1].client === "在线") return 1;
    return 0;
  });

  devices.forEach(([name, info]) => {
    const div = document.createElement("div");
    div.style.padding = "4px 8px";
    div.style.fontSize = "12px";
    div.style.color = info.client === "在线" ? "#eee" : "#666";
    div.style.display = "flex";
    div.style.justifyContent = "space-between";

    const statusDot = info.client === "在线" ? `<span style="color:#10b981">●</span>` : "○";

    div.innerHTML = `<span>${statusDot} ${name}</span> <span style="font-family:monospace;opacity:0.7">${info.ip || ""}</span>`;
    list.appendChild(div);
  });
}

function clearAiMemory() {
  // Ideally user confirms
  if (confirm("确定清除所有AI对话历史和任务缓存吗？")) {
    // Send reset to server if supported, or just refresh frontend state
    // Currently just reloading page effectively clears non-persistent frontend state, 
    // but backend might persist. Sending a clear command would be better.
    // For now, let's just clear chat UI
    document.getElementById("chat-history").innerHTML = `<div class="chat-message ai">记忆已清除。<br>你好！我是中央控制 AI。</div>`;
    document.getElementById("task-monitor").innerHTML = ""; // Clear dispatch monitor too
    activeMonitorId = null; // Clear inline monitor ref
    showToast("前端记忆已清除");
  }
}

// ================= WebSocket 通信 =================
function connect() {
  const url = document.getElementById("ws-url").value;
  ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    document.getElementById("overlay").classList.add("hidden");
    showToast("✅ 已连接");
    // 获取本地设备名称
    sendRaw({ action: "get_device_name" });
    // 获取平台信息
    sendRaw({ action: "get_platform_info" });
    listDev();
    loadAllowControl(); // 加载权限设置
    loadSequences(); // 连接时预加载脚本
  };

  ws.onclose = () => {
    document.getElementById("overlay").classList.remove("hidden");
    showToast("❌ 连接已断开", 5000);
  };

  ws.onmessage = (e) => {
    const dv = new DataView(e.data);
    const headLen = dv.getUint32(0);
    const headStr = new TextDecoder().decode(new Uint8Array(e.data, 4, headLen));
    let head;
    try { head = JSON.parse(headStr); } catch (err) { console.error(err); return; }
    const bodyData = e.data.slice(4 + headLen);

    // 视频帧
    if (head.action === "video") {
      const oldSrc = img.src;
      if (oldSrc.startsWith("blob:")) URL.revokeObjectURL(oldSrc);
      const blob = new Blob([bodyData], { type: "image/jpeg" });
      img.src = URL.createObjectURL(blob);
    }
    // P2P 状态
    else if (head.action === "p2p_status") {
      const s = head.params.status;
      const authStat = document.getElementById("p2p-auth-stat");

      if (s === "self_connected") {
        // 自控制模式
        isSelfControl = true;
        controlMode = "local";
        p2pStat.innerText = "🏠 自控制模式";
        p2pStat.style.color = "var(--success)";
        document.getElementById("mode-p2p").className = "";
        document.getElementById("mode-tcp").className = "";
        authStat.style.display = "block";
        authStat.innerText = "✅ 本地设备 - 无需权限验证 | 浏览器输入控制已禁用";
        authStat.style.color = "var(--success)";
        // 自控模式：保留视频流（用户制作脚本时需要看到屏幕画面）
        // 浏览器端的鼠标/键盘输入控制已禁用，脚本端控制正常
        showToast("🏠 已进入自控制模式（浏览器输入控制已禁用，脚本控制正常）", 4000);
      } else {
        isSelfControl = false;
        p2pStat.innerText = `P2P: ${s}`;
        p2pStat.style.color = s === "connected" ? "var(--success)" : "var(--danger)";
        if (s === "connected") {
          setMode("p2p");
          // 显示权限验证等待状态
          authStat.style.display = "block";
          authStat.innerText = "🔐 等待权限验证...";
          authStat.style.color = "var(--warning)";
        }
      }
    }
    // 本地设备名称
    else if (head.action === "device_name_info") {
      localDeviceName = head.params.name;
      console.log(`本地设备名称: ${localDeviceName}`);
    }
    // 平台检测信息
    else if (head.action === "platform_info") {
      platformData = head.params;
      updatePlatformBadge(platformData);
    }
    // P2P 权限验证状态
    else if (head.action === "p2p_auth_status") {
      const authStat = document.getElementById("p2p-auth-stat");
      authStat.style.display = "block";
      if (head.params.allowed) {
        authStat.innerText = `✅ 权限验证通过 (${head.params.authority})`;
        authStat.style.color = "var(--success)";
        showToast(`权限验证通过: ${head.params.message}`, 3000);
      } else {
        authStat.innerText = `❌ 权限不足 (需要: ${head.params.require})`;
        authStat.style.color = "var(--danger)";
        showToast(`权限验证失败: ${head.params.message}`, 5000);
      }
    }
    // 权限控制设置响应
    else if (head.action === "allow_control_value") {
      document.getElementById("allow-control-select").value = head.params.value || "admin";
    }
    else if (head.action === "allow_control_updated") {
      if (head.params.success) {
        showToast(`权限设置已更新: ${head.params.value}`, 2000);
      } else {
        showToast(`设置失败: ${head.params.msg}`, 3000);
      }
    }
    // 按钮数据
    else if (head.action === "buttons_data") {
      buttonsData = head.data || { positions: {}, groups: {} };
      renderButtons();
    }
    // OCR数据
    else if (head.action === "ocr_data") {
      ocrData = head.data || {};
      renderOcrRegions();
      updateOcrDatalist(); // 更新OCR列表
    }
    // OCR识别结果
    else if (head.action === "ocr_text") {
      if (head.success) {
        showToast(`OCR[${head.name}]: ${head.text}`, 4000);
      } else {
        showToast(`OCR失败: ${head.msg}`, 3000);
      }
    }
    // 序列数据
    else if (head.action === "sequences_data") {
      sequencesData = head.data || {};
      renderSequences();
      renderDispatchScripts(); // 更新分发列表
    }
    // 操作结果
    else if (head.action === "button_result" || head.action === "ocr_result" || head.action === "sequence_result") {
      showToast(head.msg, 2000);
      if (head.success) {
        if (head.action === "button_result") loadButtons();
        else if (head.action === "ocr_result") loadOcrRegions();
        else if (head.action === "sequence_result") loadSequences();
      }
    }
    // 请求回调
    else if (head.request_id && requestCallbacks[head.request_id]) {
      requestCallbacks[head.request_id](head);
      delete requestCallbacks[head.request_id];
    }
    else if (head.status && head.msg) {
      showToast(`系统: ${head.msg}`);
    }
  };
}

function sendRaw(header, binary = null) {
  if (!ws || ws.readyState !== 1) return;
  header.date_len = binary ? binary.byteLength : 0;
  const headBytes = new TextEncoder().encode(JSON.stringify(header));
  const buf = new ArrayBuffer(4 + headBytes.length + header.date_len);
  const v = new DataView(buf);
  v.setUint32(0, headBytes.length);
  const u = new Uint8Array(buf);
  u.set(headBytes, 4);
  if (binary) u.set(new Uint8Array(binary), 4 + headBytes.length);
  ws.send(buf);
}

// 浏览器屏幕控制动作（鼠标/键盘），自控模式下应禁止这些动作
const BROWSER_SCREEN_CONTROL_ACTIONS = new Set([
  "click_mouse", "double_click", "mouse_down", "mouse_up", "move_mouse",
  "scroll_mouse", "write_keyboard", "keyPress_keyboard"
]);

function sendCtrl(actObj) {
  if (!targetDev) return showToast("请先选择目标设备！");

  // 自控制模式：禁止浏览器端的屏幕控制（鼠标/键盘事件），
  // 以确保用户的物理输入不被拦截和重复模拟
  if (isSelfControl) {
    if (BROWSER_SCREEN_CONTROL_ACTIONS.has(actObj.action)) {
      // 浏览器屏幕控制动作在自控模式下被忽略
      return;
    }
    // 非屏幕控制动作（如 start_video, change_window 等）仍然正常发送
    sendRaw(actObj);
    return;
  }

  if (controlMode === "p2p" && actObj.action !== "p2p_connect") {
    sendRaw({ action: "p2p_proxy", params: actObj });
  } else {
    sendRaw({
      action: "server_request",
      params: {
        real_action: "forward",
        params: { device_name: targetDev, params: actObj },
      },
    });
  }
}

// ================= 设备管理 =================
function listDev() {
  const reqId = "ls_" + Date.now();
  sendRaw({ action: "server_request", params: { real_action: "list", request_id: reqId } });
  requestCallbacks[reqId] = (resp) => {
    cachedDevices = resp.result || {};
    refreshGroupFilters();
    renderDevList();
  };
}

function refreshGroupFilters() {
  const container = document.getElementById("group-filter-container");
  container.innerHTML = "";
  const groups = new Set();
  for (const info of Object.values(cachedDevices)) {
    if (info.group && info.client === "在线") groups.add(info.group);
  }

  if (groups.size === 0) {
    container.innerHTML = "<span style='color:#666; font-size:11px'>无在线组</span>";
    return;
  }

  const newSelected = new Set();
  selectedGroups.forEach((g) => { if (groups.has(g)) newSelected.add(g); });
  selectedGroups = newSelected;
  if (selectedGroups.size === 0) groups.forEach((g) => selectedGroups.add(g));

  Array.from(groups).sort().forEach((g) => {
    const div = document.createElement("div");
    div.className = "group-tag" + (selectedGroups.has(g) ? " active" : "");
    div.innerText = g;
    div.onclick = () => {
      if (selectedGroups.has(g)) {
        if (selectedGroups.size > 1) selectedGroups.delete(g);
      } else {
        selectedGroups.add(g);
      }
      div.className = "group-tag" + (selectedGroups.has(g) ? " active" : "");
      renderDevList();
    };
    container.appendChild(div);
  });
}

function toggleAllGroups(select) {
  document.querySelectorAll("#group-filter-container .group-tag").forEach((d) => {
    selectedGroups.add(d.innerText);
    d.className = "group-tag active";
  });
  renderDevList();
}

function renderDevList() {
  const container = document.getElementById("dev-container");
  container.innerHTML = "";
  let count = 0;

  for (const [name, info] of Object.entries(cachedDevices)) {
    const isOnline = info.client === "在线";
    const isInGroup = selectedGroups.has(info.group);
    if (isInGroup && isOnline) {
      const div = document.createElement("div");
      div.className = "list-item" + (targetDev === name ? " active" : "");
      div.innerHTML = `
              <span class="name">${name} <small style="color:#888">${info.group}</small></span>
              <span class="dot online"></span>
            `;
      div.onclick = () => selectDev(name, div);
      container.appendChild(div);
      count++;
    }
  }
  if (count === 0) {
    container.innerHTML = `<div class="empty-tip">无在线设备</div>`;
  }
}

function selectDev(name, el) {
  targetDev = name;
  isSelfControl = false; // 重置自控制标志
  document.querySelectorAll("#dev-container .list-item").forEach((d) => d.classList.remove("active"));
  el.classList.add("active");

  // 检测是否连接自己
  if (localDeviceName && name === localDeviceName) {
    // 自连接：跳过P2P，直接进入自控制模式
    showToast(`🏠 正在进入自控制模式...`);
  } else {
    setMode("p2p");
    showToast(`正在连接: ${name}`);
  }

  sendRaw({ action: "p2p_connect", params: { target_device: targetDev } });
}

function setMode(mode) {
  controlMode = mode;
  document.getElementById("mode-p2p").className = mode === "p2p" ? "active" : "";
  document.getElementById("mode-tcp").className = mode === "tcp" ? "active" : "";
}

function startVideo() { sendCtrl({ action: "start_video" }); }
function stopVideo() { sendCtrl({ action: "stop_video" }); img.src = ""; }
function changeWindow() {
  const wId = parseInt(document.getElementById("win").value) || 0;
  sendCtrl({ action: "change_window", params: { window: wId } });
}

// ================= 权限控制设置 =================
function setAllowControl() {
  const value = document.getElementById("allow-control-select").value;
  sendRaw({ action: "set_allow_control", params: { value } });
}

function loadAllowControl() {
  sendRaw({ action: "get_allow_control" });
}

// ================= 按钮管理 =================
function loadButtons() {
  sendRaw({ action: "get_buttons" });
}

function renderButtons() {
  const container = document.getElementById("buttons-container");
  container.innerHTML = "";

  for (const [groupName, buttonNames] of Object.entries(buttonsData.groups || {})) {
    if (buttonNames.length === 0) continue;

    const groupHeader = document.createElement("div");
    groupHeader.className = "group-header";
    groupHeader.innerHTML = `<span>📁 ${groupName}</span><small>${buttonNames.length}</small>`;
    container.appendChild(groupHeader);

    for (const btnName of buttonNames) {
      const pos = buttonsData.positions[btnName];
      const div = document.createElement("div");
      div.className = "list-item";
      div.innerHTML = `
              <span class="name">🔘 ${btnName}</span>
              <span class="actions">
                <button onclick="event.stopPropagation(); highlightPosition('${btnName}', 'button')" style="flex:0;padding:2px 5px">👁</button>
                <button onclick="event.stopPropagation(); clickButton('${btnName}')" class="success">点</button>
                <button onclick="event.stopPropagation(); deleteButton('${btnName}')" class="danger">✕</button>
              </span>
            `;
      div.onclick = () => highlightPosition(btnName, 'button');
      container.appendChild(div);
    }
  }

  if (container.children.length === 0) {
    container.innerHTML = `<div class="empty-tip">暂无按钮<br/>点击"添加按钮"在画面上框选</div>`;
  }
}

function startMarkButton() {
  markMode = "button";
  document.getElementById("mark-hint").classList.remove("hidden");
  showToast("在画面上拖拽框选按钮区域");
}

// 高亮显示按钮/OCR区域位置
let highlightTimeout = null;
function highlightPosition(name, type) {
  let position = null;

  if (type === 'button') {
    position = buttonsData.positions[name];
  } else if (type === 'ocr') {
    // 递归查找OCR区域
    function findOcr(data) {
      for (const [n, item] of Object.entries(data)) {
        if (n === name && item.position) return item.position;
        if (item.type === 'group' && item.children) {
          const found = findOcr(item.children);
          if (found) return found;
        }
      }
      return null;
    }
    position = findOcr(ocrData);
  }

  if (!position || !img.naturalWidth) {
    showToast(`${name}: 位置未定义或画面未加载`);
    return;
  }

  let [x, y, w, h] = position;

  // 检测是否为比例坐标 (简单假设如果 x<=1 且 w<=1 则是比例)
  // 为了更安全，可以检查是否全 float
  const isRatio = (x <= 1.0 && w <= 1.0 && h <= 1.0);

  if (isRatio) {
    // 转换为像素用于显示
    x = x * img.naturalWidth;
    y = y * img.naturalHeight;
    w = w * img.naturalWidth;
    h = h * img.naturalHeight;
  }

  const imgRect = img.getBoundingClientRect();
  const scale = Math.min(imgRect.width / img.naturalWidth, imgRect.height / img.naturalHeight);
  const actW = img.naturalWidth * scale;
  const actH = img.naturalHeight * scale;
  const mainRect = document.getElementById("main").getBoundingClientRect();
  const offsetX = (imgRect.left - mainRect.left) + (imgRect.width - actW) / 2;
  const offsetY = (imgRect.top - mainRect.top) + (imgRect.height - actH) / 2;

  const highlightRect = document.getElementById("highlight-rect");
  highlightRect.style.left = (offsetX + x * scale) + "px";
  highlightRect.style.top = (offsetY + y * scale) + "px";
  highlightRect.style.width = (w * scale) + "px";
  highlightRect.style.height = (h * scale) + "px";
  highlightRect.classList.remove("hidden");

  // 3秒后自动隐藏
  if (highlightTimeout) clearTimeout(highlightTimeout);
  highlightTimeout = setTimeout(() => {
    highlightRect.classList.add("hidden");
  }, 3000);

  // 显示原始值
  if (isRatio) {
    showToast(`${name}: [${position[0].toFixed(3)}, ${position[1].toFixed(3)}...]`);
  } else {
    showToast(`${name}: [${x}, ${y}, ${w}, ${h}]`);
  }
}

async function clickButton(name) {
  if (isSelfControl) {
    // 自控制模式：直接使用服务端自动化点击（更精确）
    sendRaw({ action: "click_button", params: { name } });
    showToast(`自控制点击: ${name}`);
  } else if (targetDev) {
    // 远程控制模式：计算坐标并发送鼠标点击指令
    const pos = buttonsData.positions[name];
    if (!pos) { showToast(`未找到按钮位置: ${name}`); return; }

    if (!img.naturalWidth) { showToast("画面未加载，无法定位"); return; }

    const [x, y, w, h] = pos;
    let nx, ny;

    // 检测比例坐标
    if (x <= 1.0 && w <= 1.0 && h <= 1.0) {
      nx = x + w / 2;
      ny = y + h / 2;
    } else {
      // 像素坐标，除以 naturalWidth 进行归一化
      nx = (x + w / 2) / img.naturalWidth;
      ny = (y + h / 2) / img.naturalHeight;
    }

    sendCtrl({
      action: "click_mouse",
      params: { x: nx, y: ny, button: "left", clicks: 1 }
    });
    showToast(`远程点击: ${name}`);
  } else {
    // 本地模式：发送给服务器执行
    sendRaw({ action: "click_button", params: { name } });
    showToast(`本地点击: ${name}`);
  }
}

function deleteButton(name) {
  if (confirm(`确定删除按钮 "${name}"?`)) {
    sendRaw({ action: "delete_button", params: { name } });
  }
}

function saveNewButton() {
  const name = document.getElementById("new-btn-name").value.trim();
  const group = document.getElementById("new-btn-group").value.trim() || "默认分组";
  const posStr = document.getElementById("new-btn-pos").value;

  if (!name) { showToast("请输入按钮名称"); return; }
  if (!posStr) { showToast("请先在画面上框选区域"); return; }

  const pos = posStr.split(",").map(Number);
  sendRaw({ action: "add_button", params: { name, position: pos, group } });
  closeModal("add-button-modal");
}

// ================= OCR管理 =================
function loadOcrRegions() {
  sendRaw({ action: "get_ocr_regions" });
}

function renderOcrRegions() {
  const container = document.getElementById("ocr-container");
  container.innerHTML = "";

  function renderRecursive(data, depth = 0) {
    for (const [name, item] of Object.entries(data)) {
      if (item.type === "group") {
        const groupHeader = document.createElement("div");
        groupHeader.className = "group-header";
        groupHeader.style.paddingLeft = (12 + depth * 15) + "px";
        groupHeader.innerHTML = `<span>📁 ${name}</span>`;
        container.appendChild(groupHeader);
        renderRecursive(item.children || {}, depth + 1);
      } else {
        const div = document.createElement("div");
        div.className = "list-item";
        div.style.paddingLeft = (12 + depth * 15) + "px";
        div.innerHTML = `
                <span class="name">📋 ${name} <small style="color:#888">${item.data_type || ""}</small></span>
                <span class="actions">
                  <button onclick="event.stopPropagation(); highlightPosition('${name}', 'ocr')" style="flex:0;padding:2px 5px">👁</button>
                  <button onclick="event.stopPropagation(); recognizeOcr('${name}')" class="success">识</button>
                  <button onclick="event.stopPropagation(); deleteOcr('${name}')" class="danger">✕</button>
                </span>
              `;
        div.onclick = () => highlightPosition(name, 'ocr');
        container.appendChild(div);
      }
    }
  }

  renderRecursive(ocrData);

  if (container.children.length === 0) {
    container.innerHTML = `<div class="empty-tip">暂无OCR区域<br/>点击"添加区域"在画面上框选</div>`;
  }
}

function startMarkOcr() {
  markMode = "ocr";
  document.getElementById("mark-hint").classList.remove("hidden");
  showToast("在画面上拖拽框选OCR区域");
}

function recognizeOcr(name) {
  sendRaw({ action: "recognize_ocr", params: { name } });
  showToast(`正在识别 ${name}...`);
}

function deleteOcr(name) {
  if (confirm(`确定删除OCR区域 "${name}"?`)) {
    sendRaw({ action: "delete_ocr_region", params: { name } });
  }
}

function saveNewOcr() {
  const name = document.getElementById("new-ocr-name").value.trim();
  const dataType = document.getElementById("new-ocr-type").value;
  const posStr = document.getElementById("new-ocr-pos").value;

  if (!name) { showToast("请输入区域名称"); return; }
  if (!posStr) { showToast("请先在画面上框选区域"); return; }

  const pos = posStr.split(",").map(Number);
  sendRaw({ action: "add_ocr_region", params: { name, position: pos, data_type: dataType } });
  closeModal("add-ocr-modal");
}

// ================= 脚本管理 =================
function loadSequences() {
  sendRaw({ action: "get_sequences" });
}

function renderSequences() {
  const container = document.getElementById("saved-scripts");
  container.innerHTML = "";

  function renderRecursive(data) {
    for (const [name, item] of Object.entries(data)) {
      if (item.type === "group") {
        renderRecursive(item.children || {});
      } else if (item.type === "sequence") {
        const div = document.createElement("div");
        div.className = "list-item";
        div.innerHTML = `
                <span class="name">📜 ${name}</span>
                <span class="actions">
                  <button onclick="event.stopPropagation(); runSequence('${name}')" class="success">▶</button>
                  <button onclick="event.stopPropagation(); loadScriptToEditor('${name}')" class="primary">编</button>
                  <button onclick="event.stopPropagation(); deleteSequence('${name}')" class="danger">✕</button>
                </span>
              `;
        container.appendChild(div);
      }
    }
  }

  renderRecursive(sequencesData);

  if (container.children.length === 0) {
    container.innerHTML = `<div class="empty-tip">暂无脚本</div>`;
  }
}

// 动作类型配置
const ACTION_CONFIG = {
  click: { label: '点击', needParam: true, placeholder: '按钮名称' },
  double_click: { label: '双击', needParam: true, placeholder: '按钮名称' },
  wait: { label: '等待', needParam: true, placeholder: '秒数 (如: 1.5)' },
  type: { label: '输入', needParam: true, placeholder: '要输入的文字' },
  press: { label: '按键', needParam: true, placeholder: '按键 (如: enter 或 ctrl+c)' },
  activate_window: { label: '激活窗口', needParam: true, placeholder: '窗口标题 (部分匹配)' },
  call: { label: '调用', needParam: true, placeholder: '脚本名称' },

  // 条件与循环
  if: { label: '如果(条件)', needParam: true, control: true, indent: 1, mode: 'condition', backendType: 'if' },
  else: { label: '否则', needParam: false, control: true, indent: 0, backendType: 'else' },
  end_if: { label: '结束判断', needParam: false, control: true, indent: -1, backendType: 'end_if' },

  start_loop_count: { label: '循环(次数)', needParam: true, placeholder: '次数', control: true, indent: 1, mode: 'count', backendType: 'start_loop' },
  start_loop_cond: { label: '循环(条件)', needParam: true, control: true, indent: 1, mode: 'condition', backendType: 'start_loop' },
  end_loop: { label: '结束循环', needParam: false, control: true, indent: -1, backendType: 'end_loop' },

  start_jump: { label: '跳转起点', needParam: true, placeholder: '标签名', control: true, indent: 1, backendType: 'start_jump' },
  end_jump: { label: '跳转终点', needParam: true, placeholder: '标签名', control: true, indent: -1, backendType: 'end_jump' },
};

function updateOcrDatalist() {
  const datalist = document.getElementById("ocr-list-datalist");
  if (!datalist) return;
  datalist.innerHTML = "";

  function addItems(data) {
    for (const [name, item] of Object.entries(data)) {
      if (item.type === "group") {
        addItems(item.children || {});
      } else {
        const opt = document.createElement("option");
        opt.value = name;
        datalist.appendChild(opt);
      }
    }
  }
  addItems(ocrData);
}

function updateParamPlaceholder() {
  const type = document.getElementById("action-type").value;
  const paramInput = document.getElementById("action-param");
  const condContainer = document.getElementById("condition-params");

  const config = ACTION_CONFIG[type] || {};

  // 根据模式切换输入框
  if (config.mode === 'condition') {
    paramInput.style.display = "none";
    condContainer.style.display = "flex";
    // 确保OCR列表已加载
    if (document.getElementById("ocr-list-datalist").children.length === 0) {
      updateOcrDatalist();
    }
  } else {
    paramInput.style.display = "block";
    condContainer.style.display = "none";

    paramInput.placeholder = config.needParam ? `参数: ${config.placeholder || ''}` : '(无需参数)';
    paramInput.disabled = !config.needParam;
    if (!config.needParam) paramInput.value = '';
  }
}

// ========== 拖拽排序相关变量 ==========
let draggedActionIndex = null;
let dragOverActionIndex = null;

function renderScriptActions() {
  const container = document.getElementById("script-actions");
  container.innerHTML = "";

  let indentLevel = 0;

  currentScript.forEach((action, idx) => {
    // 查找对应的 UI config
    let config = ACTION_CONFIG[action.type] || {};

    // 尝试反向匹配 UI config (针对 start_loop 等聚合类型)
    if (action.type === 'start_loop') {
      if (action.param && (action.param.includes("condition:") || action.param.includes('max:'))) {
        config = ACTION_CONFIG['start_loop_cond'] || config;
      } else {
        config = ACTION_CONFIG['start_loop_count'] || config;
      }
    }

    // 处理缩进：结束类标签先减少缩进
    if (config.indent < 0) indentLevel = Math.max(0, indentLevel + config.indent);

    const div = document.createElement("div");
    div.className = "script-action" + (config.control ? " control-flow" : "");
    div.style.paddingLeft = (10 + indentLevel * 20) + "px";
    div.draggable = true;
    div.dataset.index = idx;

    const label = config.label || action.type;
    const paramDisplay = action.param || (config.needParam ? '' : '-');

    div.innerHTML = `
            <span class="drag-handle" title="拖动排序">⋮⋮</span>
            <span class="type">${label}</span>
            <span class="param">${paramDisplay}</span>
            <span class="remove" onclick="event.stopPropagation(); removeAction(${idx})">✕</span>
          `;

    // 添加拖拽事件监听
    div.addEventListener('dragstart', handleDragStart);
    div.addEventListener('dragend', handleDragEnd);
    div.addEventListener('dragover', handleDragOver);
    div.addEventListener('dragenter', handleDragEnter);
    div.addEventListener('dragleave', handleDragLeave);
    div.addEventListener('drop', handleDrop);

    container.appendChild(div);

    // 处理缩进：开始类标签后增加缩进
    if (config.indent > 0) indentLevel += config.indent;
  });

  if (currentScript.length === 0) {
    container.innerHTML = `<div class="empty-tip">添加动作构建脚本<br><small style="color:#666">点击下方 + 添加动作</small></div>`;
  }
}

// ========== 拖拽事件处理函数 ==========
function handleDragStart(e) {
  draggedActionIndex = parseInt(e.target.dataset.index);
  e.target.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', draggedActionIndex);

  // 添加一点延迟让CSS动画更流畅
  setTimeout(() => {
    e.target.style.opacity = '0.5';
  }, 0);
}



function handleDragEnd(e) {
  e.target.classList.remove('dragging');
  e.target.style.opacity = '';

  // 清除所有 drag-over 状态
  document.querySelectorAll('.script-action.drag-over').forEach(el => {
    el.classList.remove('drag-over');
  });

  draggedActionIndex = null;
  dragOverActionIndex = null;
}

function handleDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}

function handleDragEnter(e) {
  e.preventDefault();
  const target = e.target.closest('.script-action');
  if (target && parseInt(target.dataset.index) !== draggedActionIndex) {
    // 清除其他元素的 drag-over 状态
    document.querySelectorAll('.script-action.drag-over').forEach(el => {
      if (el !== target) el.classList.remove('drag-over');
    });
    target.classList.add('drag-over');
    dragOverActionIndex = parseInt(target.dataset.index);
  }
}

function handleDragLeave(e) {
  const target = e.target.closest('.script-action');
  if (target) {
    // 检查是否真的离开了元素（而不是进入子元素）
    const rect = target.getBoundingClientRect();
    if (e.clientX < rect.left || e.clientX >= rect.right ||
      e.clientY < rect.top || e.clientY >= rect.bottom) {
      target.classList.remove('drag-over');
    }
  }
}

function handleDrop(e) {
  e.preventDefault();
  const target = e.target.closest('.script-action');
  if (!target) return;

  const fromIndex = draggedActionIndex;
  const toIndex = parseInt(target.dataset.index);

  if (fromIndex !== null && fromIndex !== toIndex) {
    // 执行排序
    moveAction(fromIndex, toIndex);
  }

  // 清除状态
  target.classList.remove('drag-over');
}

function moveAction(fromIndex, toIndex) {
  // 从数组中移除元素
  const [movedAction] = currentScript.splice(fromIndex, 1);

  // 插入到新位置
  currentScript.splice(toIndex, 0, movedAction);

  // 重新渲染
  renderScriptActions();

  showToast(`已移动动作到位置 ${toIndex + 1}`);
}

function addAction() {
  const uiType = document.getElementById("action-type").value;
  const config = ACTION_CONFIG[uiType] || {};

  let finalType = config.backendType || uiType;
  let finalParam = "";

  if (config.mode === 'condition') {
    const region = document.getElementById("cond-region").value.trim();
    const op = document.getElementById("cond-op").value;
    const val = document.getElementById("cond-value").value.trim();

    if (!region || !val) {
      showToast("请输入完整的条件信息");
      return;
    }

    // 构造条件字符串: Region op Value
    const conditionStr = `${region} ${op} ${val}`;

    if (uiType === 'start_loop_cond') {
      finalParam = `condition:${conditionStr}`;
    } else {
      finalParam = conditionStr;
    }

    // 清空输入
    document.getElementById("cond-region").value = "";
    document.getElementById("cond-value").value = "";

  } else if (config.mode === 'count') {
    const count = document.getElementById("action-param").value.trim();
    if (!count) { showToast("请输入循环次数"); return; }
    finalParam = count;
  } else {
    finalParam = document.getElementById("action-param").value.trim();
    if (config.needParam && !finalParam) {
      showToast("请输入参数");
      return;
    }
  }

  currentScript.push({ type: finalType, param: finalParam });
  renderScriptActions();

  if (config.mode !== 'condition') {
    document.getElementById("action-param").value = "";
  }
}

function removeAction(idx) {
  currentScript.splice(idx, 1);
  renderScriptActions();
}

function saveScript() {
  const name = document.getElementById("script-name").value.trim();
  if (!name) { showToast("请输入脚本名称"); return; }
  if (currentScript.length === 0) { showToast("脚本为空"); return; }

  sendRaw({ action: "save_sequence", params: { name, actions: currentScript } });
}

async function runScriptClientSide(actions) {
  showToast("开始客户端执行...");
  try {
    for (let i = 0; i < actions.length; i++) {
      const action = actions[i];
      const { type, param } = action;

      if (type === 'click') {
        await clickButton(param);
        await new Promise(r => setTimeout(r, 100));
      } else if (type === 'double_click') {
        await doubleClickButton(param);
        await new Promise(r => setTimeout(r, 100));
      } else if (type === 'activate_window') {
        sendCtrl({ action: "activate_window", params: { title: param } });
        await new Promise(r => setTimeout(r, 500));
      } else if (type === 'wait') {
        const secs = parseFloat(param) || 0;
        await new Promise(r => setTimeout(r, secs * 1000));
      } else if (type === 'type') {
        for (const char of param) {
          sendCtrl({ action: "write_keyboard", params: { key: char } });
          await new Promise(r => setTimeout(r, 20));
        }
      } else if (type === 'press') {
        const keys = param.split('+').map(k => k.trim().toLowerCase());
        if (keys.length > 1) {
          sendCtrl({ action: "keyPress_keyboard", params: { key_list: keys } });
        } else {
          sendCtrl({ action: "write_keyboard", params: { key: keys[0] } });
        }
      } else if (type === 'call') {
        // 简单的子脚本调用支持
        function findSequence(data) {
          for (const [n, item] of Object.entries(data)) {
            if (n === param && item.type === "sequence") return item.actions || [];
            if (item.type === "group") {
              const found = findSequence(item.children || {});
              if (found) return found;
            }
          }
          return null;
        }
        const subActions = findSequence(sequencesData);
        if (subActions) await runScriptClientSide(subActions);
      } else {
        // 暂不支持的控制流或高级指令
        console.warn(`跳过不支持的客户端动作: ${type}`);
      }
    }
    showToast("客户端执行完成");
  } catch (e) {
    console.error(e);
    showToast(`执行出错: ${e.message}`);
  }
}

function doubleClickButton(name) {
  return new Promise((resolve, reject) => {
    if (targetDev) {
      const pos = buttonsData.positions[name];
      if (!pos) { showToast(`未找到按钮位置: ${name}`); resolve(); return; }
      if (!img.naturalWidth) { showToast("画面未加载"); resolve(); return; }

      const [x, y, w, h] = pos;
      let nx, ny;
      if (x <= 1.0 && w <= 1.0 && h <= 1.0) {
        nx = x + w / 2;
        ny = y + h / 2;
      } else {
        nx = (x + w / 2) / img.naturalWidth;
        ny = (y + h / 2) / img.naturalHeight;
      }
      sendCtrl({
        action: "double_click",
        params: { x: nx, y: ny, button: "left" }
      });
      showToast(`双击: ${name}`);
      setTimeout(resolve, 100);
    } else {
      resolve();
    }
  });
}

function runCurrentScript() {
  if (currentScript.length === 0) { showToast("脚本为空"); return; }

  if (isSelfControl) {
    // 自控制模式：直接用服务端执行（支持完整的条件/循环等高级功能）
    sendRaw({ action: "run_actions", params: { actions: currentScript } });
    showToast("自控制执行中...");
  } else if (targetDev && (controlMode === 'p2p' || controlMode === 'tcp')) {
    runScriptClientSide(currentScript);
  } else {
    sendRaw({ action: "run_actions", params: { actions: currentScript } });
    showToast("执行中...");
  }
}

function stopScript() {
  sendRaw({ action: "stop_sequence" });
  showToast("已停止");
}

function runSequence(name) {
  if (isSelfControl) {
    // 自控制模式：直接用服务端执行
    sendRaw({ action: "run_sequence", params: { name } });
    showToast(`自控制运行: ${name}`);
  } else if (targetDev && (controlMode === 'p2p' || controlMode === 'tcp')) {
    function findSequence(data) {
      for (const [n, item] of Object.entries(data)) {
        if (n === name && item.type === "sequence") return item.actions || [];
        if (item.type === "group") {
          const found = findSequence(item.children || {});
          if (found) return found;
        }
      }
      return null;
    }
    const actions = findSequence(sequencesData);
    if (actions) {
      runScriptClientSide(actions);
    } else {
      showToast("未找到脚本内容");
    }
  } else {
    sendRaw({ action: "run_sequence", params: { name } });
    showToast(`运行: ${name}`);
  }
}

function deleteSequence(name) {
  if (confirm(`确定删除脚本 "${name}"?`)) {
    sendRaw({ action: "delete_sequence", params: { name } });
  }
}

function loadScriptToEditor(name) {
  function findSequence(data) {
    for (const [n, item] of Object.entries(data)) {
      if (n === name && item.type === "sequence") return item.actions || [];
      if (item.type === "group") {
        const found = findSequence(item.children || {});
        if (found) return found;
      }
    }
    return null;
  }

  const actions = findSequence(sequencesData);
  if (actions) {
    currentScript = [...actions];
    document.getElementById("script-name").value = name;
    renderScriptActions();
    showToast(`已加载: ${name}`);
  }
}

// ================= 画面区域框选 =================
function getImageCoords(e) {
  if (!img.naturalWidth) return null;
  const rect = img.getBoundingClientRect();
  const scale = Math.min(rect.width / img.naturalWidth, rect.height / img.naturalHeight);
  const actW = img.naturalWidth * scale;
  const actH = img.naturalHeight * scale;
  const offsetX = (rect.width - actW) / 2;
  const offsetY = (rect.height - actH) / 2;
  const x = e.clientX - rect.left - offsetX;
  const y = e.clientY - rect.top - offsetY;
  if (x < 0 || x > actW || y < 0 || y > actH) return null;
  // 返回原始图像坐标
  const mainRect = document.getElementById("main").getBoundingClientRect();

  // DEBUG 
  console.log(`[Coords] clientX=${e.clientX}, rect.left=${rect.left}, mainRect.left=${mainRect.left}`);
  console.log(`[Coords] offsetX(img)=${offsetX}, rect-main=${rect.left - mainRect.left}`);

  return {
    x: Math.round(x / scale),
    y: Math.round(y / scale),
    scale,
    offsetX: (rect.left - mainRect.left) + offsetX,
    offsetY: (rect.top - mainRect.top) + offsetY
  };
}

img.addEventListener("mousedown", (e) => {
  if (markMode) {
    e.preventDefault();
    const coords = getImageCoords(e);
    if (!coords) return;
    markStart = coords;

    const rect = document.getElementById("selection-rect");
    rect.style.left = (coords.offsetX + coords.x * coords.scale) + "px";
    rect.style.top = (coords.offsetY + coords.y * coords.scale) + "px";
    rect.style.width = "0px";
    rect.style.height = "0px";
    rect.classList.remove("hidden");
    return;
  }

  // 正常拖拽逻辑
  if (!targetDev) return;
  // 自控模式下不拦截鼠标事件
  if (isSelfControl) return;
  e.preventDefault();
  const pos = getRelPos(e);
  if (!pos) return;
  dragStart = { ...pos, rawX: e.clientX, rawY: e.clientY, btn: btnMap[e.button] };
  isDragging = false;
});

document.addEventListener("mousemove", (e) => {
  if (markMode && markStart) {
    const coords = getImageCoords(e);
    if (!coords) return;

    const rect = document.getElementById("selection-rect");
    const x1 = Math.min(markStart.x, coords.x);
    const y1 = Math.min(markStart.y, coords.y);
    const x2 = Math.max(markStart.x, coords.x);
    const y2 = Math.max(markStart.y, coords.y);

    rect.style.left = (coords.offsetX + x1 * coords.scale) + "px";
    rect.style.top = (coords.offsetY + y1 * coords.scale) + "px";
    rect.style.width = ((x2 - x1) * coords.scale) + "px";
    rect.style.height = ((y2 - y1) * coords.scale) + "px";
    return;
  }

  // 正常移动逻辑
  if (Date.now() - lastMove < 30) return;
  lastMove = Date.now();
  const pos = getRelPos(e);
  if (!pos) return;
  if (dragStart && !isDragging) {
    if (Math.hypot(e.clientX - dragStart.rawX, e.clientY - dragStart.rawY) > 5) {
      isDragging = true;
      sendCtrl({
        action: "mouse_down",
        params: { x: dragStart.x, y: dragStart.y, button: dragStart.btn },
      });
    }
  }
  sendCtrl({ action: "move_mouse", params: { x: pos.x, y: pos.y } });
});

document.addEventListener("mouseup", (e) => {
  if (markMode && markStart) {
    const coords = getImageCoords(e);
    document.getElementById("selection-rect").classList.add("hidden");
    document.getElementById("mark-hint").classList.add("hidden");

    if (coords) {
      const x1 = Math.min(markStart.x, coords.x);
      const y1 = Math.min(markStart.y, coords.y);
      const w = Math.abs(coords.x - markStart.x);
      const h = Math.abs(coords.y - markStart.y);

      if (w > 5 && h > 5 && img.naturalWidth > 0) {
        // 计算比例坐标 (保留5位小数)
        const rx = (x1 / img.naturalWidth).toFixed(5);
        const ry = (y1 / img.naturalHeight).toFixed(5);
        const rw = (w / img.naturalWidth).toFixed(5);
        const rh = (h / img.naturalHeight).toFixed(5);

        const posStr = `${rx},${ry},${rw},${rh}`;

        if (markMode === "button") {
          document.getElementById("new-btn-pos").value = posStr;
          document.getElementById("add-button-modal").classList.remove("hidden");
        } else if (markMode === "ocr") {
          document.getElementById("new-ocr-pos").value = posStr;
          document.getElementById("add-ocr-modal").classList.remove("hidden");
        }
      }
    }

    markMode = null;
    markStart = null;
    return;
  }

  // 正常点击逻辑
  if (!dragStart) return;
  const pos = getRelPos(e) || { x: dragStart.x, y: dragStart.y };
  if (isDragging) {
    sendCtrl({ action: "mouse_up", params: { x: pos.x, y: pos.y, button: dragStart.btn } });
  } else {
    sendCtrl({
      action: "click_mouse",
      params: { x: pos.x, y: pos.y, button: dragStart.btn, clicks: 1 },
    });
  }
  dragStart = null;
  isDragging = false;
});

// ================= 输入事件处理 =================
const btnMap = { 0: "left", 1: "middle", 2: "right" };
let dragStart = null, isDragging = false, lastMove = 0;

function getRelPos(e) {
  if (!img.naturalWidth) return null;
  const rect = img.getBoundingClientRect();
  const scale = Math.min(rect.width / img.naturalWidth, rect.height / img.naturalHeight);
  const actW = img.naturalWidth * scale;
  const actH = img.naturalHeight * scale;
  const offsetX = (rect.width - actW) / 2;
  const offsetY = (rect.height - actH) / 2;
  const x = e.clientX - rect.left - offsetX;
  const y = e.clientY - rect.top - offsetY;
  if (x < 0 || x > actW || y < 0 || y > actH) return null;
  return { x: x / actW, y: y / actH };
}

img.addEventListener("dblclick", (e) => {
  if (isSelfControl) return; // 自控模式下不拦截
  const pos = getRelPos(e);
  if (pos)
    sendCtrl({
      action: "double_click",
      params: { x: pos.x, y: pos.y, button: btnMap[e.button] },
    });
});

img.addEventListener("wheel", (e) => {
  if (isSelfControl) return; // 自控模式下不拦截
  e.preventDefault();
  const pos = getRelPos(e);
  if (pos)
    sendCtrl({
      action: "scroll_mouse",
      params: { x: pos.x, y: pos.y, clicks: e.deltaY > 0 ? -120 : 120 },
    });
}, { passive: false });

document.getElementById("main").addEventListener("keydown", (e) => {
  if (!targetDev) return;
  // 自控模式下不拦截键盘事件，让用户正常使用键盘
  if (isSelfControl) return;
  if (e.key !== "F12" && !e.ctrlKey) e.preventDefault();
  const keyMap = {
    Control: "ctrl", Alt: "alt", Shift: "shift", Meta: "lwin",
    Backspace: "backspace", Enter: "enter", Escape: "esc",
    Tab: "tab", Delete: "del",
  };
  const key = keyMap[e.key] || e.key.toLowerCase();
  if (e.ctrlKey || e.altKey || e.metaKey || key.length > 1) {
    let list = [];
    if (e.ctrlKey && key !== "ctrl") list.push("ctrl");
    if (e.altKey && key !== "alt") list.push("alt");
    if (e.shiftKey && key !== "shift") list.push("shift");
    if (e.metaKey && key !== "lwin") list.push("lwin");
    if (!["ctrl", "alt", "shift", "lwin"].includes(key)) list.push(key);
    if (list.length > 0)
      sendCtrl({ action: "keyPress_keyboard", params: { key_list: list } });
  } else {
    sendCtrl({ action: "write_keyboard", params: { key: e.key } });
  }
});

img.addEventListener("click", () => document.getElementById("main").focus());

// ================= 分发脚本功能 =================
let dispatchTasks = {}; // { task_id: { device, script, status, message } }

function renderDispatchDevices() {
  const container = document.getElementById("dispatch-device-list");
  container.innerHTML = "";

  for (const [name, info] of Object.entries(cachedDevices)) {
    if (info.client === "在线") {
      const div = document.createElement("label");
      div.className = "device-checkbox-item";
      div.innerHTML = `
            <input type="checkbox" value="${name}" />
            <span>${name}</span>
            <small style="color: #666">${info.group || ""}</small>
          `;
      container.appendChild(div);
    }
  }

  if (container.children.length === 0) {
    container.innerHTML = `<div class="empty-tip">无在线设备</div>`;
  }
}

function selectAllDevices(select) {
  document.querySelectorAll("#dispatch-device-list input[type='checkbox']").forEach(cb => {
    cb.checked = select;
  });
}

function renderDispatchScripts() {
  const select = document.getElementById("dispatch-script-select");
  select.innerHTML = `<option value="">-- 选择脚本 --</option>`;

  function addOptions(data, prefix = "") {
    for (const [name, item] of Object.entries(data)) {
      if (item.type === "sequence") {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = prefix + name;
        select.appendChild(opt);
      } else if (item.type === "group") {
        addOptions(item.children || {}, prefix + name + "/");
      }
    }
  }

  addOptions(sequencesData);
}

function dispatchScript() {
  const scriptName = document.getElementById("dispatch-script-select").value;
  if (!scriptName) {
    showToast("请选择脚本");
    return;
  }

  const selectedDevices = [];
  document.querySelectorAll("#dispatch-device-list input[type='checkbox']:checked").forEach(cb => {
    selectedDevices.push(cb.value);
  });

  if (selectedDevices.length === 0) {
    showToast("请选择目标设备");
    return;
  }

  // 查找脚本内容
  function findSequence(data, targetName) {
    for (const [n, item] of Object.entries(data)) {
      if (n === targetName && item.type === "sequence") return item.actions || [];
      if (item.type === "group") {
        const found = findSequence(item.children || {}, targetName);
        if (found) return found;
      }
    }
    return null;
  }
  const actions = findSequence(sequencesData, scriptName);
  if (!actions) {
    showToast("无法获取脚本内容");
    return;
  }

  // 为每个设备创建任务
  const batchId = Date.now();
  selectedDevices.forEach((deviceName, idx) => {
    const taskId = `${batchId}_${idx}`;

    // 记录任务
    dispatchTasks[taskId] = {
      device: deviceName,
      script: scriptName,
      status: "pending",
      message: "等待发送..."
    };

    // 发送远程执行请求 (直接发送动作列表，无需对方预存脚本)
    sendRaw({
      action: "server_request",
      params: {
        real_action: "forward",
        params: {
          device_name: deviceName,
          params: {
            action: "remote_run_actions",
            params: {
              actions: actions,
              task_id: taskId
            }
          }
        }
      }
    });
  });

  renderTaskMonitor();
  showToast(`已向 ${selectedDevices.length} 个设备分发脚本: ${scriptName}`);
}

function stopAllRemote() {
  const runningDevices = new Set();
  for (const task of Object.values(dispatchTasks)) {
    if (task.status === "running" || task.status === "accepted") {
      runningDevices.add(task.device);
    }
  }

  if (runningDevices.size === 0) {
    showToast("没有正在运行的任务");
    return;
  }

  runningDevices.forEach(deviceName => {
    sendRaw({
      action: "server_request",
      params: {
        real_action: "forward",
        params: {
          device_name: deviceName,
          params: {
            action: "remote_stop_sequence",
            params: {}
          }
        }
      }
    });
  });

  showToast(`已向 ${runningDevices.size} 个设备发送停止命令`);
}

function handleRemoteScriptStatus(params) {
  const { device_name, task_id, status, message } = params;

  if (dispatchTasks[task_id]) {
    dispatchTasks[task_id].status = status;
    dispatchTasks[task_id].message = message;
    renderTaskMonitor();
  }

  // 显示状态变化
  const statusText = {
    accepted: "已接收",
    running: "执行中",
    completed: "已完成",
    failed: "失败",
    stopped: "已停止"
  };
  showToast(`[${device_name}] ${statusText[status] || status}: ${message}`, 3000);
}

function renderTaskMonitor() {
  const container = document.getElementById("task-monitor");
  container.innerHTML = "";

  const tasks = Object.entries(dispatchTasks).reverse(); // 最新的在前面

  tasks.forEach(([taskId, task]) => {
    const div = document.createElement("div");
    div.className = "task-item";
    div.innerHTML = `
          <span class="task-device" title="${task.device}">${task.device}</span>
          <span class="task-script">${task.script}</span>
          <span class="task-status status-${task.status}">${task.status}</span>
        `;
    div.title = task.message;
    container.appendChild(div);
  });

  document.getElementById("task-count").textContent = `${tasks.length} 个任务`;

  if (tasks.length === 0) {
    container.innerHTML = `<div class="empty-tip">暂无分发任务</div>`;
  }
}

// ================= AI 多设备任务管理 =================
let aiTasks = {}; // { task_id: { device, goal, status, message, frame } }
let aiViewMode = 'grid'; // 'grid' | 'switch'
let aiCurrentViewDevice = null; // 切换模式下当前显示的设备

// 渲染 AI 设备列表
function renderAiDevices() {
  const container = document.getElementById("ai-device-list");
  container.innerHTML = "";

  for (const [name, info] of Object.entries(cachedDevices)) {
    if (info.client === "在线") {
      const div = document.createElement("label");
      div.className = "device-checkbox-item";
      div.innerHTML = `
            <input type="checkbox" value="${name}" />
            <span>${name}</span>
            <small style="color: #666">${info.group || ""}</small>
          `;
      container.appendChild(div);
    }
  }

  if (container.children.length === 0) {
    container.innerHTML = `<div class="empty-tip">无在线设备</div>`;
  }
}

function selectAllAiDevices(select) {
  document.querySelectorAll("#ai-device-list input[type='checkbox']").forEach(cb => {
    cb.checked = select;
  });
}

// 获取选中的 AI 目标设备
function getSelectedAiDevices() {
  const devices = [];
  document.querySelectorAll("#ai-device-list input[type='checkbox']:checked").forEach(cb => {
    devices.push(cb.value);
  });
  return devices;
}

// 启动多设备 AI 任务
function startMultiAiTask() {
  const goal = document.getElementById("ai-goal-input").value.trim();
  if (!goal) {
    showToast("请输入 AI 任务目标");
    return;
  }

  const selectedDevices = getSelectedAiDevices();
  if (selectedDevices.length === 0) {
    showToast("请选择目标设备");
    return;
  }

  const maxSteps = parseInt(document.getElementById("ai-max-steps").value) || 30;
  const reasoningEffort = document.getElementById("ai-reasoning-effort")?.value || "medium";
  const screenIndex = parseInt(document.getElementById("ai-screen-select")?.value) || 0;
  const enableOcr = document.getElementById("ai-enable-ocr")?.value === "true";
  const batchId = Date.now();

  // 为每个设备创建任务
  selectedDevices.forEach((deviceName, idx) => {
    const taskId = `ai_${batchId}_${idx}`;

    // 记录任务
    aiTasks[taskId] = {
      device: deviceName,
      goal: goal,
      status: "pending",
      message: "等待发送...",
      frame: null,
      timestamp: Date.now()
    };

    // 发送远程执行请求 (通过 TCP/服务器转发，不需要 P2P 连接)
    sendRaw({
      action: "server_request",
      params: {
        real_action: "forward",
        params: {
          device_name: deviceName,
          params: {
            action: "start_ai_task",
            params: {
              task_id: taskId,
              goal: goal,
              max_steps: maxSteps,
              reasoning_effort: reasoningEffort,
              screen_index: screenIndex,
              enable_ocr: enableOcr
            }
          }
        }
      }
    });
  });

  // 更新 UI
  renderAiTaskMonitor();
  renderAiFrames();
  showToast(`已向 ${selectedDevices.length} 个设备分发 AI 任务 (推理强度: ${reasoningEffort}, 屏幕: ${screenIndex}, OCR: ${enableOcr ? '开' : '关'})`);
}


// 停止所有 AI 任务
function stopAllAiTasks() {
  const runningTasks = [];
  for (const [taskId, task] of Object.entries(aiTasks)) {
    if (task.status === "running" || task.status === "started" || task.status === "pending" || task.status === "asking") {
      runningTasks.push({ device: task.device, taskId });
    }
  }

  if (runningTasks.length === 0) {
    showToast("没有正在运行的任务");
    return;
  }

  runningTasks.forEach(({ device, taskId }) => {
    sendRaw({
      action: "server_request",
      params: {
        real_action: "forward",
        params: {
          device_name: device,
          params: {
            action: "stop_ai_task",
            params: { task_id: taskId }
          }
        }
      }
    });
  });

  showToast(`已向 ${runningTasks.length} 个设备发送停止命令`);
}

// 设置视图模式
function setAiViewMode(mode) {
  aiViewMode = mode;

  // 更新按钮状态
  document.getElementById("ai-view-grid").className = mode === 'grid' ? 'active' : '';
  document.getElementById("ai-view-switch").className = mode === 'switch' ? 'active' : '';

  renderAiFrames();
}

// 切换模式下选择设备
function selectAiViewDevice(deviceName) {
  aiCurrentViewDevice = deviceName;
  renderAiFrames();
}

// 获取设备的最佳显示任务（优先运行中，其次最新）
function getBestTaskForDevice(deviceName) {
  const deviceTasks = Object.values(aiTasks).filter(t => t.device === deviceName);
  if (deviceTasks.length === 0) return null;

  // 优先找正在运行的
  const runningPromise = deviceTasks.find(t => ["running", "started", "pending"].includes(t.status));
  if (runningPromise) return runningPromise;

  // 否则找最新的
  return deviceTasks.sort((a, b) => b.timestamp - a.timestamp)[0];
}

// 渲染 AI 帧显示
function renderAiFrames() {
  const grid = document.getElementById("ai-frames-grid");
  const single = document.getElementById("ai-frame-single");
  const tabs = document.getElementById("ai-device-tabs");

  // 获取关联的所有设备（去重）
  const devices = [...new Set(Object.values(aiTasks).map(t => t.device))];

  if (devices.length === 0) {
    grid.innerHTML = '<div class="ai-frame-placeholder">选择设备并开始 AI 任务后，执行画面将显示在这里</div>';
    grid.className = "ai-frames-grid";
    single.classList.add("hidden");
    tabs.classList.remove("active");
    return;
  }

  if (aiViewMode === 'grid') {
    // 分屏模式
    grid.classList.remove("hidden");
    single.classList.add("hidden");
    tabs.classList.remove("active");

    // 确定网格大小
    const count = devices.length;
    grid.className = "ai-frames-grid";
    if (count === 1) grid.classList.add("grid-1");
    else if (count === 2) grid.classList.add("grid-2");
    else if (count <= 4) grid.classList.add("grid-4");
    else if (count <= 6) grid.classList.add("grid-6");
    else grid.classList.add("grid-more");

    grid.innerHTML = "";
    devices.forEach(deviceName => {
      const task = getBestTaskForDevice(deviceName);
      if (!task) return;

      const slot = document.createElement("div");
      slot.className = "ai-frame-slot";

      const statusClass = task.status || "pending";
      const statusLabels = {
        pending: "等待", started: "启动中", running: "执行中",
        completed: "完成", failed: "失败", stopped: "已停止", error: "错误"
      };

      slot.innerHTML = `
            <div class="device-label">
              ${deviceName}
              <span class="status-badge ${statusClass}">${statusLabels[task.status] || task.status}</span>
            </div>
            ${task.thought ? `<div class="ai-thought-small" title="${task.thought.replace(/"/g, '&quot;')}">🤔 ${task.thought}</div>` : ''}
            ${task.frame
          ? `<img src="data:image/jpeg;base64,${task.frame}" alt="${deviceName}" />`
          : `<div class="no-frame">⏳ 等待画面...</div>`
        }
          `;
      grid.appendChild(slot);
    });
  } else {
    // 切换模式
    grid.classList.add("hidden");
    single.classList.remove("hidden");
    tabs.classList.add("active");

    // 渲染设备标签页
    tabs.innerHTML = "";
    devices.forEach(deviceName => {
      const task = getBestTaskForDevice(deviceName);
      const isActive = deviceName === aiCurrentViewDevice || (!aiCurrentViewDevice && deviceName === devices[0]);
      const statusClass = task?.status || "pending";

      const tab = document.createElement("button");
      tab.className = "ai-device-tab" + (isActive ? " active" : "");
      tab.innerHTML = `<span class="status-dot ${statusClass}"></span>${deviceName}`;
      tab.onclick = () => selectAiViewDevice(deviceName);
      tabs.appendChild(tab);

      if (isActive) aiCurrentViewDevice = deviceName;
    });

    // 显示当前设备的帧
    const currentTask = aiCurrentViewDevice ? getBestTaskForDevice(aiCurrentViewDevice) : null;
    const singleImg = document.getElementById("ai-frame-single-img");
    const overlay = document.getElementById("ai-frame-overlay");

    if (currentTask?.frame) {
      singleImg.src = `data:image/jpeg;base64,${currentTask.frame}`;
      overlay.textContent = `${aiCurrentViewDevice} - ${currentTask.message || "执行中"}`;

      const thoughtEl = document.getElementById("ai-frame-thought");
      if (thoughtEl) thoughtEl.textContent = currentTask.thought || "";

      singleImg.style.display = "block";
    } else {
      singleImg.style.display = "none";
      overlay.textContent = `${aiCurrentViewDevice || "无设备"} - 暂无画面`;

      const thoughtEl = document.getElementById("ai-frame-thought");
      if (thoughtEl) thoughtEl.textContent = "";
    }
  }
}

// 渲染 AI 任务监控
function renderAiTaskMonitor() {
  const container = document.getElementById("ai-task-monitor");
  container.innerHTML = "";

  const tasks = Object.entries(aiTasks).reverse();

  tasks.forEach(([taskId, task]) => {
    const div = document.createElement("div");
    div.className = "task-item";
    div.innerHTML = `
          <span class="task-device" title="${task.device}">${task.device}</span>
          <span class="task-script" title="${task.goal}">${task.goal.substring(0, 20)}${task.goal.length > 20 ? '...' : ''}</span>
          <span class="task-status status-${task.status}">${task.status}</span>
        `;
    div.title = task.message;
    container.appendChild(div);
  });

  document.getElementById("ai-task-count").textContent = `(${tasks.length} 个任务)`;

  if (tasks.length === 0) {
    container.innerHTML = `<div class="empty-tip">暂无 AI 任务</div>`;
  }
}

// 处理 AI 任务状态更新
function handleAiTaskStatus(params) {
  const { device_name, task_id, status, message, timestamp } = params;

  // 查找或创建任务
  if (!aiTasks[task_id]) {
    aiTasks[task_id] = {
      device: device_name,
      goal: "",
      status: status,
      message: message,
      thought: params.thought || "",
      frame: null
    };
  } else {
    aiTasks[task_id].status = status;
    aiTasks[task_id].message = message;
    if (params.thought) aiTasks[task_id].thought = params.thought;
  }

  // Update AI Status Bar in Chat Overlay
  const statusContainer = document.getElementById("ai-task-status-bar");
  const statusText = document.getElementById("ai-thinking-text");
  if (statusContainer && statusText) {
    if (status === "running" || status === "asking") {
      statusContainer.style.display = "block";
      // Show thought primarily, fallback to message
      const text = params.thought || message || "Thinking...";
      statusText.textContent = `[${device_name}] ${text.substring(0, 100)}${text.length > 100 ? "..." : ""}`;
      statusText.title = text; // Tooltip for full text
    } else if (status === "completed" || status === "failed" || status === "stopped" || status === "error") {
      statusText.textContent = `[${device_name}] ${statusLabels[status] || status}: ${message}`;
      // Hide after 5 seconds
      setTimeout(() => {
        // Only hide if the text hasn't changed (meaning no new task started)
        if (statusText.textContent.includes(message)) {
          statusContainer.style.display = "none";
        }
      }, 5000);
    }
  }

  renderAiTaskMonitor();
  renderAiFrames();

  // Feedback on status change
  const statusLabels = {
    pending: "等待", started: "启动中", running: "执行中",
    completed: "完成", failed: "失败", stopped: "已停止", error: "错误", asking: "提问"
  };

  const overlay = document.getElementById("ai-chat-overlay");
  const isChatVisible = overlay && !overlay.classList.contains("hidden");

  // Always show toast for completed/failed/asking or if chat is hidden
  if (status === "completed" || status === "failed" || status === "error" || (!isChatVisible && status !== "running")) {
    showToast(`[${device_name}] AI ${statusLabels[status] || status}: ${message?.substring(0, 30) || ""}`, 4000);
  }

  // Feedback on completion
  if (status === "completed") {
    renderChatMessage("ai", `✅ Task on **${device_name}** completed: ${message}`);
  } else if (status === "failed" || status === "error") {
    renderChatMessage("ai", `❌ Task on **${device_name}** failed: ${message}`);
  }

  if (activeMonitorId) {
    const slot = document.getElementById(`slot-${activeMonitorId}-${device_name}`);
    if (slot) {
      const dot = slot.querySelector(".status-dot");
      if (dot) dot.className = `status-dot ${status}`;
      // Add message tooltip
      slot.title = message || status;
    }
  }

  // Handle 'asking' status - Inject into chat
  if (status === "asking") {
    const chatContainer = document.getElementById("chat-history");
    if (chatContainer) {
      const cardId = `question-${task_id}-${Date.now()}`;
      const qDiv = document.createElement("div"); // Renamed from div
      qDiv.className = "chat-message ai";
      qDiv.style.border = "1px solid #eab308"; // Yellow border for attention
      qDiv.innerHTML = `
             <div style="font-weight:bold;margin-bottom:5px;display:flex;align-items:center">
                <span class="status-dot running"></span> <span style="margin-right:5px;font-size:12px;display:inline-block;padding:2px 5px;background:#333;border-radius:3px">${aiTasks[task_id]?.device || device_name}</span> needs help:
             </div>
             <div style="margin-bottom:10px">${message}</div>
             <div id="${cardId}-area">
                <input type="text" id="${cardId}-input" placeholder="Type answer..." style="width:100%;padding:8px;border-radius:4px;border:1px solid #555;background:#222;color:white;margin-bottom:5px">
                <button onclick="answerAiQuestion('${task_id}', '${cardId}-input')" style="width:100%;padding:6px;background:#eab308;color:black;border:none;border-radius:4px;cursor:pointer;font-weight:bold;font-size:12px">Send Answer</button>
             </div>
          `;
      chatContainer.appendChild(qDiv);
      chatContainer.scrollTop = chatContainer.scrollHeight;
    }
  }
}

function answerAiQuestion(taskId, inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const answer = input.value.trim();
  if (!answer) return;

  // Find device name from task ID local cache
  const deviceName = aiTasks[taskId]?.device;
  if (!deviceName) {
    showToast("Error: Unknown task device");
    return;
  }

  sendRaw({
    action: "server_request",
    params: {
      real_action: "forward",
      params: {
        device_name: deviceName,
        params: {
          action: "answer_ai_question",
          params: {
            task_id: taskId,
            answer: answer
          }
        }
      }
    }
  });

  // UI Feedback
  if (input.parentElement)
    input.parentElement.innerHTML = `<div style="color:#eab308;font-size:12px"><i>Answer sent: ${answer}</i></div>`;
}


// 处理 AI 任务帧更新
function handleAiTaskFrame(params) {
  const { device_name, task_id, frame, timestamp } = params;

  if (activeMonitorId) {
    const slot = document.getElementById(`slot-${activeMonitorId}-${device_name}`);
    if (slot) {
      const img = slot.querySelector("img");
      if (img) img.src = `data:image/jpeg;base64,${frame}`;
    }
  }
}

// 保留旧函数兼容性
function startAiTask() {
  // 兼容性：如果没有选择设备，使用当前选中的设备
  if (getSelectedAiDevices().length === 0 && targetDev) {
    // 自动勾选当前设备
    const checkbox = document.querySelector(`#ai-device-list input[value="${targetDev}"]`);
    if (checkbox) checkbox.checked = true;
  }
  startMultiAiTask();
}

function stopAiTask() {
  stopAllAiTasks();
}

function updateAiStatus(status, message) {
  // 兼容性保留
}

function updateAiFrame(base64Data) {
  // 兼容性保留
}

// 扩展 switchTab 函数以支持分发和AI标签页
const originalSwitchTab = switchTab;
switchTab = function (name) {
  originalSwitchTab(name);
  if (name === "dispatch") {
    renderDispatchDevices();
    renderDispatchScripts();
    renderTaskMonitor();
  } else if (name === "ai") {
    renderAiDevices();
    renderAiTaskMonitor();
    renderAiFrames();
  }
};

// 扩展 ws.onmessage 处理远程脚本状态和 AI 任务状态
const originalOnMessage = ws ? ws.onmessage : null;

// 在 connect 函数中添加状态处理
const originalConnect = connect;
connect = function () {
  originalConnect();

  // 重新获取 ws 引用并扩展 onmessage
  setTimeout(() => {
    if (ws) {
      const baseOnMessage = ws.onmessage;
      ws.onmessage = function (e) {
        baseOnMessage.call(ws, e);

        // 额外处理远程脚本状态和 AI 任务状态
        try {
          const dv = new DataView(e.data);
          const headLen = dv.getUint32(0);
          const headStr = new TextDecoder().decode(new Uint8Array(e.data, 4, headLen));
          const head = JSON.parse(headStr);

          if (head.action === "remote_script_status") {
            handleRemoteScriptStatus(head.params);
          } else if (head.action === "ai_task_status") {
            handleAiTaskStatus(head.params);
          } else if (head.action === "ai_task_frame") {
            handleAiTaskFrame(head.params);
          } else if (head.action === "ai_debug_frame") {
            handleAiDebugFrame(head.params);
          } else if (head.action === "chat_response") {
            handleChatResponse(head.params);
          } else if (head.action === "device_selection_request") {
            handleDeviceSelectionRequest(head.params);
          } else if (head.action === "batch_task_started") {
            showToast(`Batch task started on ${head.params.count} devices`);
          }
        } catch (err) {
          // 忽略解析错误
        }
      };
    }
  }, 100);
};

// 初始化
renderScriptActions();

// ================= 平台检测 UI =================
function updatePlatformBadge(data) {
  const badge = document.getElementById("platform-badge");
  if (!badge || !data) return;

  badge.style.display = "block";

  if (data.is_degraded) {
    const degradedCount = (data.degraded_features || []).length;
    const missingCount = (data.missing_features || []).length;

    if (missingCount > 0) {
      badge.innerHTML = `⚠️ ${data.platform} 降级模式 | ${missingCount} 项不可用`;
      badge.style.background = "rgba(255, 100, 100, 0.15)";
      badge.style.color = "#ff6b6b";
    } else if (degradedCount > 0) {
      badge.innerHTML = `ℹ️ ${data.platform} 模式 | ${degradedCount} 项降级`;
      badge.style.background = "rgba(255, 180, 50, 0.15)";
      badge.style.color = "#ffb432";
    } else {
      badge.innerHTML = `ℹ️ ${data.platform} 兼容模式`;
      badge.style.background = "rgba(100, 180, 255, 0.15)";
      badge.style.color = "#64b4ff";
    }
  } else {
    badge.innerHTML = `✅ Windows 完整模式`;
    badge.style.background = "rgba(80, 200, 120, 0.1)";
    badge.style.color = "#50c878";
    // 完整模式下 3 秒后自动隐藏
    setTimeout(() => { badge.style.display = "none"; }, 3000);
  }
}

function showPlatformDetails() {
  if (!platformData) return;

  let msg = `🖥️ 平台信息\n`;
  msg += `━━━━━━━━━━━━━━━━━━━\n`;
  msg += `操作系统: ${platformData.platform}\n`;
  msg += `架构: ${platformData.arch}\n`;
  msg += `Python: ${platformData.python_version}\n`;
  msg += `图形界面: ${platformData.has_display ? '✓ 可用' : '✗ 不可用'}\n`;

  if (platformData.degraded_features && platformData.degraded_features.length > 0) {
    msg += `\n⚠️ 降级的功能:\n`;
    platformData.degraded_features.forEach(f => {
      msg += `  • ${f}\n`;
    });
  }

  if (platformData.missing_features && platformData.missing_features.length > 0) {
    msg += `\n❌ 不可用的功能:\n`;
    platformData.missing_features.forEach(f => {
      msg += `  • ${f}\n`;
    });
  }

  if (!platformData.is_degraded) {
    msg += `\n✅ 所有功能正常运行`;
  }

  alert(msg);
}

// ================= Chat Logic =================
let currentConvId = null;

function sendChat() {
  const input = document.getElementById("chat-input");
  const msg = input.value.trim();
  if (!msg) return;

  // Show user message
  renderChatMessage("user", msg);
  input.value = "";

  // Send to backend
  sendRaw({
    action: "chat",
    params: {
      message: msg,
      conv_id: currentConvId
    }
  });
}

// Bind Enter key and auto-resize
const chatInput = document.getElementById("chat-input");
if (chatInput) {
  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
      // Reset height after sending
      this.style.height = "auto";
      this.style.height = this.scrollHeight + "px";
    }
  });

  // Auto-resize textarea on input
  chatInput.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 150) + "px";
  });
}

function handleChatResponse(params) {
  if (params.conv_id) currentConvId = params.conv_id;
  if (params.response_text) {
    renderChatMessage("ai", params.response_text);
  }
}

function renderChatMessage(role, text) {
  const container = document.getElementById("chat-history");
  if (!container) return;

  const div = document.createElement("div");
  div.className = `chat-message ${role}`;
  // Convert newlines to <br> for display
  div.innerHTML = text.replace(/\n/g, "<br>");
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function handleDeviceSelectionRequest(params) {
  const { conv_id, task_description, pre_selection, script_content } = params;
  currentConvId = conv_id;

  const container = document.getElementById("chat-history");

  // Device Selection Card
  const cardId = `dev-select-${Date.now()}`;
  const card = document.createElement("div");
  card.className = "device-selection-card";
  card.innerHTML = `
    <div class="device-selection-header">
       <span>Select Devices</span>
       <small style="cursor:pointer;color:#3b82f6" onclick="toggleAllDevSelect('${cardId}')">All/None</small>
    </div>
    <div style="display:grid;grid-template-columns:20px 1fr 60px 70px;align-items:center;gap:4px;padding:2px 6px;font-size:10px;color:#666;border-bottom:1px solid #333;margin-bottom:2px">
      <span></span><span>设备名称</span><span style="text-align:center">最大步数</span><span style="text-align:center">推理强度</span>
    </div>
    <div class="device-selection-list" id="${cardId}-list">
       <!-- Checkboxes -->
    </div>
    <div class="device-selection-actions">
       <button class="btn-cancel" onclick="this.closest('.device-selection-card').remove()">Cancel</button>
       <button class="btn-confirm" onclick="confirmDeviceSelection('${cardId}', '${conv_id}', '${task_description.replace(/'/g, "\\'")}', '${script_content ? script_content.replace(/'/g, "\\'") : ""}')">Confirm Execution</button>
    </div>
  `;

  container.appendChild(card);

  // Populate List
  const listEl = document.getElementById(`${cardId}-list`);
  const onlineDevices = Object.entries(cachedDevices).filter(([n, i]) => i.client === "在线").map(([n]) => n);

  if (onlineDevices.length === 0) {
    listEl.innerHTML = "<div style='padding:5px;color:#888'>No devices online</div>";
  } else {
    onlineDevices.forEach(dev => {
      const isPreSelected = (pre_selection.includes("all") || pre_selection.includes(dev));
      const safeId = dev.replace(/[^a-zA-Z0-9_-]/g, '_');
      const row = document.createElement("div");
      // 4 列: checkbox | 设备名 | 步数输入 | 强度下拉
      row.style.cssText = "display:grid;grid-template-columns:20px 1fr 60px 70px;align-items:center;gap:4px;padding:3px 6px;border-bottom:1px solid #2a2a2a;";
      row.innerHTML = `
        <input type="checkbox" value="${dev}" data-dev="${dev}" ${isPreSelected ? "checked" : ""} style="width:14px;height:14px;margin:0;cursor:pointer;">
        <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px;cursor:pointer" onclick="this.previousElementSibling.click()">${dev}</span>
        <input type="number" id="${cardId}-steps-${safeId}" value="30" min="1" max="200"
          style="width:100%;background:#111;border:1px solid #444;color:#fff;border-radius:3px;padding:2px 4px;font-size:11px;text-align:center;box-sizing:border-box">
        <select id="${cardId}-reason-${safeId}"
          style="width:100%;background:#111;border:1px solid #444;color:#fff;border-radius:3px;padding:2px 3px;font-size:11px;box-sizing:border-box">
          <option value="low">低</option>
          <option value="medium" selected>中</option>
          <option value="high">高</option>
        </select>
      `;
      listEl.appendChild(row);
    });
  }

  container.scrollTop = container.scrollHeight;
}

function toggleAllDevSelect(cardId) {
  const inputs = document.querySelectorAll(`#${cardId}-list input[type='checkbox']`);
  const allChecked = Array.from(inputs).every(i => i.checked);
  inputs.forEach(i => i.checked = !allChecked);
}

function confirmDeviceSelection(cardId, convId, taskDesc, scriptContent) {
  const inputs = document.querySelectorAll(`#${cardId}-list input[type='checkbox']:checked`);
  const devices = Array.from(inputs).map(i => i.getAttribute('data-dev') || i.value);

  if (devices.length === 0) {
    showToast("Please select at least one device");
    return;
  }

  // Build per-device configs
  const deviceConfigs = {};
  devices.forEach(dev => {
    const safeId = dev.replace(/[^a-zA-Z0-9_-]/g, '_');
    const stepsEl = document.getElementById(`${cardId}-steps-${safeId}`);
    const reasonEl = document.getElementById(`${cardId}-reason-${safeId}`);
    deviceConfigs[dev] = {
      max_steps: stepsEl ? (parseInt(stepsEl.value) || 30) : 30,
      reasoning_effort: reasonEl ? (reasonEl.value || 'medium') : 'medium'
    };
  });

  // Disable buttons
  const card = document.getElementById(cardId + "-list").parentElement;
  card.querySelectorAll("button").forEach(b => b.disabled = true);
  card.innerHTML += `<div style="margin-top:5px;color:#10b981">Confirmed. Starting task on ${devices.length} devices...</div>`;

  sendRaw({
    action: "confirm_batch_task",
    params: {
      devices: devices,
      task_description: taskDesc,
      script_content: scriptContent,
      device_configs: deviceConfigs
    }
  });

  // Create Inline Monitor
  createInlineTaskMonitor(devices, taskDesc);
}

let activeMonitorId = null;

function createInlineTaskMonitor(devices, title) {
  const chatContainer = document.getElementById("chat-history");
  const monitorId = `monitor-${Date.now()}`;
  activeMonitorId = monitorId;

  const div = document.createElement("div");
  div.className = "chat-task-monitor";
  div.id = monitorId;

  let gridHtml = "<div></div>";
  // Wait, grid needs to be proper
  gridHtml = "";
  devices.forEach(dev => {
    gridHtml += `
            <div class="device-slot" id="slot-${monitorId}-${dev}" data-device="${dev}">
                <div class="label"><span class="status-dot pending"></span>${dev}</div>
                <img src="" alt="Waiting..." onclick="expandMonitorImage(this)" title="点击查看大图" style="cursor:zoom-in"/>
            </div>
        `;
  });

  div.innerHTML = `
        <div class="monitor-header" style="display:flex;justify-content:space-between;align-items:center">
            <span>🚀 ${title || "Batch Task"}</span>
            <button onclick="stopInlineMonitor('${monitorId}')" class="danger" style="padding:2px 8px;font-size:12px">🛑 EMERGENCY STOP</button>
        </div>
        <div class="monitor-grid">
            ${gridHtml}
        </div>
    `;

  chatContainer.appendChild(div);
  chatContainer.scrollTop = chatContainer.scrollHeight;

  // Store task IDs mapping if available, or just broadcast stop
}

function stopInlineMonitor(monitorId) {
  if (!confirm("EMERGENCY STOP: Terminate all AI tasks immediately?")) return;

  // 1. Use existing stopAllAiTasks which correctly sends stop_ai_task to each device
  stopAllAiTasks();

  // 2. Visual update
  const monitor = document.getElementById(monitorId);
  if (monitor) {
    monitor.querySelectorAll(".status-dot").forEach(dot => {
      if (!dot.classList.contains("completed") && !dot.classList.contains("failed")) {
        dot.className = "status-dot stopped";
      }
    });
    showToast("Emergency Stop Signal Sent!");
  }
}

function expandMonitorImage(img) {
  if (!img.src || img.src === window.location.href) return;
  const modal = document.getElementById("full-image-modal");
  const view = document.getElementById("full-image-view");
  if (modal && view) {
    view.src = img.src;
    modal.style.display = "flex";
  }
}

// ================= AI 调试模式 =================
let aiDebugMode = false;

// 调试模式切换
const debugToggle = document.getElementById("ai-debug-mode");
if (debugToggle) {
  debugToggle.addEventListener("change", function () {
    aiDebugMode = this.checked;
    const panel = document.getElementById("ai-debug-panel");
    const label = document.getElementById("debug-mode-label");
    if (panel) {
      panel.classList.toggle("hidden", !aiDebugMode);
    }
    if (label) {
      label.textContent = aiDebugMode ? "开启" : "关闭";
      label.style.color = aiDebugMode ? "#3b82f6" : "#666";
    }
  });
}

// 处理 AI 调试帧 (AI 视角截图)
function handleAiDebugFrame(params) {
  if (!aiDebugMode) return;
  const { frame } = params;
  const img = document.getElementById("debug-ai-frame");
  if (img && frame) {
    img.src = `data:image/jpeg;base64,${frame}`;
  }
}

// 添加调试日志条目
function addDebugLog(type, text, detail) {
  if (!aiDebugMode) return;
  const log = document.getElementById("debug-log");
  if (!log) return;

  const now = new Date();
  const timeStr = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;

  const entry = document.createElement("div");
  entry.className = `debug-log-entry ${type}`;

  let html = `<span class="log-time">${timeStr}</span> <span class="log-text">${text}</span>`;
  if (detail) {
    html += `<div class="log-thought">${detail}</div>`;
  }
  entry.innerHTML = html;

  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;

  // 限制最大日志条数
  while (log.children.length > 200) {
    log.removeChild(log.firstChild);
  }
}

// 清除调试日志
function clearDebugLog() {
  const log = document.getElementById("debug-log");
  if (log) log.innerHTML = "";
}

// 扩展 handleAiTaskStatus 以注入调试日志
const _originalHandleAiTaskStatus = handleAiTaskStatus;
handleAiTaskStatus = function (params) {
  _originalHandleAiTaskStatus(params);

  if (!aiDebugMode) return;

  const { device_name, status, message, thought, action_data, step_result, debug_info } = params;
  const dev = device_name || "?";

  if (debug_info) {
    addDebugLog("step-start", `🔍 [${dev}] AI 接收到的输入信息:`, `<pre style="margin:0;font-size:10px;white-space:pre-wrap;color:#a3a3a3">${debug_info}</pre>`);
  }

  // 状态变化日志
  if (status === "started") {
    addDebugLog("step-start", `🚀 [${dev}] 任务开始: ${message}`);
  } else if (status === "completed") {
    addDebugLog("step-done", `✅ [${dev}] 任务完成`, thought || message);
  } else if (status === "failed" || status === "error") {
    addDebugLog("step-fail", `❌ [${dev}] ${message}`, thought);
  } else if (status === "stopped") {
    addDebugLog("step-done", `⏹ [${dev}] ${message}`);
  } else if (status === "asking") {
    addDebugLog("step-action", `❓ [${dev}] AI 提问: ${message}`);
  } else if (status === "running" && !action_data && !step_result) {
    addDebugLog("step-start", `🔄 [${dev}] ${message}`);
  }

  // 动作执行日志 (带 action_data)
  if (action_data) {
    const actType = action_data.type || "?";
    const target = action_data.target ? JSON.stringify(action_data.target) : "";
    const value = action_data.value || "";
    addDebugLog("step-action",
      `⚡ [${dev}] <span class="log-action">${actType}</span> target=${target} value="${value}"`,
      thought ? `💭 ${thought}` : null
    );
  }

  // 步骤结果日志
  if (step_result) {
    const result = step_result.result || "";
    const isSuccess = result === "success";
    addDebugLog(
      isSuccess ? "step-success" : "step-fail",
      `${isSuccess ? "✅" : "❌"} [${dev}] 结果: ${result}`
    );
  }
};
