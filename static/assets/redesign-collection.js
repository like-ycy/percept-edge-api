const videoGrid = document.getElementById('videoGrid');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const statusValue = document.getElementById('statusValue');
const taskIdInput = document.getElementById('taskId');
const logDiv = document.getElementById('log');

const cameraConnections = new Map();
let isCollecting = false;
let isStopping = false;
let isPageReady = false;
let hasInitialized = false;
let authGeneration = 0;

function log(msg, type = 'info') {
  const entry = document.createElement('div');
  entry.className = `log-entry ${type}`;
  entry.textContent = `[${new Date().toLocaleTimeString('zh-CN', { hour12: false })}] ${msg}`;
  logDiv.appendChild(entry);
  logDiv.scrollTop = logDiv.scrollHeight;
}

function updateStatus(status, className = '') {
  statusValue.textContent = status;
  statusValue.className = `status-value ${className}`;
}

function setCollectionControls({ collecting = false, stopping = false } = {}) {
  isCollecting = collecting;
  isStopping = stopping;

  if (!isPageReady) {
    startBtn.disabled = true;
    stopBtn.disabled = true;
    stopBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg> 停止采集';
    return;
  }

  startBtn.disabled = collecting || stopping;
  stopBtn.disabled = !collecting || stopping;
  stopBtn.innerHTML = stopping
    ? '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg> 正在保存…'
    : '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg> 停止采集';
}

async function pollCollectionStatusUntilSettled() {
  const maxAttempts = 30;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      const resp = await StaticAuth.authFetch('/api/collection/status');
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const result = await resp.json();
      const session = result?.data;
      const status = session?.status;

      if (!status) {
        setCollectionControls({ collecting: false, stopping: false });
        updateStatus('预览中');
        return true;
      }

      if (status === 'stopping') {
        setCollectionControls({ collecting: true, stopping: true });
        updateStatus('正在保存…');
      } else if (status === 'collecting') {
        setCollectionControls({ collecting: true, stopping: false });
        updateStatus('采集中', 'collecting');
      } else {
        setCollectionControls({ collecting: false, stopping: false });
        updateStatus('预览中');
        return true;
      }
    } catch (e) {
      if (StaticAuth.isAuthError(e)) {
        return false;
      }
      log(`轮询采集状态失败: ${e.message}`, 'error');
      break;
    }

    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  return false;
}

function updateCameraStatus(cameraId, status, className = '') {
  const statusEl = document.getElementById(`status-${getCameraDomId(cameraId)}`);
  if (statusEl) {
    statusEl.textContent = status;
    statusEl.className = `camera-status ${className}`;
  }
}

function isCurrentGeneration(generation) {
  return isPageReady && generation === authGeneration;
}

function getCameraDomId(cameraId) {
  const value = String(cameraId);
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return `camera-${hash.toString(36)}`;
}

async function fetchCameras() {
  try {
    const resp = await StaticAuth.authFetch('/api/collection/cameras');
    if (resp.ok) {
      const result = await resp.json();
      return result.data.cameras || [];
    }

    log('获取摄像头列表失败', 'error');
    return [];
  } catch (e) {
    if (StaticAuth.isAuthError(e)) {
      throw e;
    }
    log(`获取摄像头列表失败: ${e.message}`, 'error');
    return [];
  }
}

function createCameraContainer(cameraId) {
  const cameraDomId = getCameraDomId(cameraId);
  const container = document.createElement('div');
  container.className = 'video-container';
  container.id = `container-${cameraDomId}`;

  const header = document.createElement('div');
  header.className = 'video-header';

  const cameraName = document.createElement('span');
  cameraName.className = 'camera-name';
  cameraName.textContent = cameraId;

  const cameraStatus = document.createElement('span');
  cameraStatus.className = 'camera-status';
  cameraStatus.id = `status-${cameraDomId}`;
  cameraStatus.textContent = '连接中…';

  const wrapper = document.createElement('div');
  wrapper.className = 'video-wrapper';

  const video = document.createElement('video');
  video.id = `video-${cameraDomId}`;
  video.autoplay = true;
  video.playsInline = true;
  video.muted = true;

  header.append(cameraName, cameraStatus);
  wrapper.appendChild(video);
  container.append(header, wrapper);
  return container;
}

async function connectCamera(cameraId, generation) {
  if (!isCurrentGeneration(generation)) {
    return;
  }

  log(`正在连接摄像头: ${cameraId}`);
  updateCameraStatus(cameraId, '连接中…', '');

  const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${wsProtocol}//${location.host}/api/collection/preview?camera_id=${encodeURIComponent(cameraId)}`);

  const connection = { ws, pc: null };
  cameraConnections.set(cameraId, connection);

  ws.onopen = () => log(`[${cameraId}] WebSocket 已连接`, 'success');

  ws.onmessage = async (event) => {
    if (!isCurrentGeneration(generation)) {
      return;
    }

    const msg = JSON.parse(event.data);

    if (msg.type === 'offer') {
      log(`[${cameraId}] 收到 WebRTC offer`);
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
      });
      connection.pc = pc;

      pc.ontrack = (event) => {
        if (!isCurrentGeneration(generation)) {
          return;
        }

        log(`[${cameraId}] 收到视频轨道`, 'success');
        const video = document.getElementById(`video-${getCameraDomId(cameraId)}`);
        if (video) {
          video.srcObject = event.streams[0];
        }
        updateCameraStatus(cameraId, '已连接', 'connected');
      };

      pc.oniceconnectionstatechange = () => {
        if (!isCurrentGeneration(generation)) {
          return;
        }

        log(`[${cameraId}] ICE 状态: ${pc.iceConnectionState}`);
        if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
          updateCameraStatus(cameraId, '已断开', 'error');
        }
      };

      await pc.setRemoteDescription(new RTCSessionDescription(msg.data));
      if (!isCurrentGeneration(generation)) {
        return;
      }

      const answer = await pc.createAnswer();
      if (!isCurrentGeneration(generation)) {
        return;
      }

      await pc.setLocalDescription(answer);
      if (!isCurrentGeneration(generation)) {
        return;
      }

      ws.send(JSON.stringify({
        type: 'answer',
        data: { sdp: answer.sdp, type: answer.type },
      }));
      log(`[${cameraId}] 已发送 answer`);
    }
  };

  ws.onerror = () => {
    if (!isCurrentGeneration(generation)) {
      return;
    }

    log(`[${cameraId}] WebSocket 错误`, 'error');
    updateCameraStatus(cameraId, '连接错误', 'error');
  };

  ws.onclose = () => {
    if (!isCurrentGeneration(generation)) {
      return;
    }

    log(`[${cameraId}] WebSocket 已关闭`);
    if (!isCollecting) {
      updateCameraStatus(cameraId, '已断开', 'error');
    }
  };
}

async function initCameraPreviews(generation) {
  if (!isCurrentGeneration(generation)) {
    return;
  }

  const cameras = await fetchCameras();

  if (!isCurrentGeneration(generation)) {
    return;
  }

  videoGrid.innerHTML = '';

  if (cameras.length === 0) {
    videoGrid.innerHTML = '<div class="no-cameras">未检测到可用摄像头</div>';
    updateStatus('无摄像头');
    return;
  }

  log(`检测到 ${cameras.length} 个摄像头: ${cameras.join(', ')}`, 'success');

  for (const cameraId of cameras) {
    if (!isCurrentGeneration(generation)) {
      return;
    }

    const container = createCameraContainer(cameraId);
    videoGrid.appendChild(container);
  }

  for (const cameraId of cameras) {
    if (!isCurrentGeneration(generation)) {
      return;
    }

    await connectCamera(cameraId, generation);
    if (!isCurrentGeneration(generation)) {
      return;
    }
  }

  updateStatus('预览中');
}

function closeAllConnections() {
  for (const [, connection] of cameraConnections) {
    if (
      connection.ws
      && connection.ws.readyState !== WebSocket.CLOSED
      && connection.ws.readyState !== WebSocket.CLOSING
    ) {
      connection.ws.close();
    }
    if (connection.pc) {
      connection.pc.close();
    }
  }
  cameraConnections.clear();
}

function setLoggedOutState() {
  authGeneration += 1;
  hasInitialized = false;
  isPageReady = false;
  closeAllConnections();
  videoGrid.innerHTML = '<div class="no-cameras">请先登录</div>';
  setCollectionControls({ collecting: false, stopping: false });
  updateStatus('未登录');
}

async function initPage() {
  isPageReady = true;
  const generation = authGeneration;
  videoGrid.innerHTML = '<div class="video-empty"><div class="screen" aria-hidden="true"></div><span>正在加载摄像头列表…</span></div>';
  setCollectionControls({ collecting: false, stopping: false });
  await initCameraPreviews(generation);
  if (!isCurrentGeneration(generation)) {
    return;
  }
  await checkStatus();
}

async function initPageOnce() {
  if (hasInitialized) {
    return;
  }
  hasInitialized = true;
  await initPage();
}

async function startCollection() {
  try {
    await StaticAuth.requireAuth({ message: '请先登录后开始采集' });
  } catch (error) {
    if (!StaticAuth.isAuthError(error)) {
      log(`登录检查失败: ${error.message}`, 'error');
    }
    return;
  }

  const taskId = taskIdInput.value;
  if (!taskId) {
    log('请输入任务 ID', 'error');
    return;
  }

  log(`开始采集，任务 ID: ${taskId}`);

  try {
    const resp = await StaticAuth.authFetch(`/api/collection/start?task_id=${taskId}`, {
      method: 'POST',
    });

    if (resp.ok) {
      const data = await resp.json();
      log(`采集已开始: ${JSON.stringify(data)}`, 'success');
      setCollectionControls({ collecting: true, stopping: false });
      updateStatus('采集中', 'collecting');
    } else {
      const text = await resp.text();
      let errMsg;
      try {
        const err = JSON.parse(text);
        errMsg = err.message || err.detail || JSON.stringify(err);
      } catch {
        errMsg = text || `HTTP ${resp.status}`;
      }
      log(`开始采集失败: ${errMsg}`, 'error');
      updateStatus('错误', 'error');
    }
  } catch (e) {
    if (StaticAuth.isAuthError(e)) {
      return;
    }
    log(`请求失败: ${e.message}`, 'error');
  }
}

async function stopCollection() {
  if (isStopping) {
    log('正在保存中，请勿重复点击', 'info');
    return;
  }

  try {
    await StaticAuth.requireAuth({ message: '请先登录后停止采集' });
  } catch (error) {
    if (!StaticAuth.isAuthError(error)) {
      log(`登录检查失败: ${error.message}`, 'error');
    }
    return;
  }

  log('停止采集…');
  setCollectionControls({ collecting: true, stopping: true });
  updateStatus('正在保存…');

  try {
    const stopPromise = StaticAuth.authFetch('/api/collection/stop', { method: 'POST' });
    const pollingPromise = pollCollectionStatusUntilSettled();
    const resp = await stopPromise;

    if (resp.ok) {
      const data = await resp.json();
      log(`采集已停止: ${JSON.stringify(data)}`, 'success');
      const settled = await pollingPromise;
      if (!settled) {
        setCollectionControls({ collecting: false, stopping: false });
        updateStatus('预览中');
      }
    } else {
      const text = await resp.text();
      let errMsg;
      try {
        const err = JSON.parse(text);
        errMsg = err.message || err.detail || JSON.stringify(err);
      } catch {
        errMsg = text || `HTTP ${resp.status}`;
      }
      log(`停止采集失败: ${errMsg}`, 'error');
      setCollectionControls({ collecting: true, stopping: false });
      updateStatus('采集中', 'collecting');
    }
  } catch (e) {
    if (StaticAuth.isAuthError(e)) {
      return;
    }
    log(`请求失败: ${e.message}`, 'error');
    setCollectionControls({ collecting: true, stopping: false });
    updateStatus('采集中', 'collecting');
  }
}

async function checkStatus() {
  try {
    const resp = await StaticAuth.authFetch('/api/collection/status');
    if (resp.ok) {
      const data = await resp.json();
      if (data?.data?.status === 'collecting') {
        setCollectionControls({ collecting: true, stopping: false });
        updateStatus('采集中', 'collecting');
        log(`检测到正在进行的采集: 任务 ${data.data.task_id}`);
      } else if (data?.data?.status === 'stopping') {
        setCollectionControls({ collecting: true, stopping: true });
        updateStatus('正在保存…');
        log(`检测到采集正在保存: 任务 ${data.data.task_id}`);
      }
    }
  } catch (e) {
    if (StaticAuth.isAuthError(e)) {
      return;
    }
    log(`查询采集状态失败: ${e.message}`, 'error');
  }
}

StaticAuth.init({
  pageName: '数据采集测试',
  onLogin: initPageOnce,
  onLogout: setLoggedOutState,
});

startBtn.addEventListener('click', startCollection);
stopBtn.addEventListener('click', stopCollection);

setLoggedOutState();
StaticAuth.requireAuth({ message: '请先登录后进入数据采集测试' })
  .then(initPageOnce)
  .catch((error) => {
    setLoggedOutState();
    if (!StaticAuth.isAuthError(error)) {
      log(`登录检查失败: ${error.message}`, 'error');
    }
  });
