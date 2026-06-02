const confirmCheck = document.getElementById('confirmCheck');
const reconcileBtn = document.getElementById('reconcileBtn');
const clearResultBtn = document.getElementById('clearResultBtn');
const resultCard = document.getElementById('resultCard');
const successCount = document.getElementById('successCount');
const failedCount = document.getElementById('failedCount');
const successIds = document.getElementById('successIds');
const failedIds = document.getElementById('failedIds');
const logDiv = document.getElementById('log');

let isPageReady = false;
let hasInitialized = false;
let isReconciling = false;

function updateActionState() {
  reconcileBtn.disabled = !isPageReady || !confirmCheck.checked || isReconciling;
}

async function readJsonSafely(response) {
  try {
    return await response.json();
  } catch (error) {
    return {
      code: response.status,
      msg: `响应 JSON 解析失败: ${error.message}`,
      data: null,
    };
  }
}

function log(message, type = 'info') {
  const entry = document.createElement('div');
  entry.className = `log-entry ${type}`;
  entry.textContent = `[${new Date().toLocaleString('zh-CN', { hour12: false })}] ${message}`;
  logDiv.appendChild(entry);
  logDiv.scrollTop = logDiv.scrollHeight;
}

function formatIds(ids) {
  if (!Array.isArray(ids) || ids.length === 0) {
    return '无';
  }
  return ids.join(', ');
}

function renderResult(data) {
  const success = Array.isArray(data?.success) ? data.success : [];
  const failed = Array.isArray(data?.failed) ? data.failed : [];

  successCount.textContent = String(success.length);
  failedCount.textContent = String(failed.length);
  successIds.textContent = formatIds(success);
  failedIds.textContent = formatIds(failed);
  resultCard.classList.add('show');
}

function clearResult() {
  successCount.textContent = '0';
  failedCount.textContent = '0';
  successIds.textContent = '无';
  failedIds.textContent = '无';
  resultCard.classList.remove('show');
  logDiv.textContent = '';
}

function buildErrorMessage(response, result) {
  if (result?.msg) {
    return result.msg;
  }
  if (result?.detail) {
    return result.detail;
  }
  return `HTTP ${response.status}`;
}

function setLoggedOutState() {
  hasInitialized = false;
  isPageReady = false;
  updateActionState();
}

function setLoggedInState() {
  isPageReady = true;
  updateActionState();
}

function restoreLoggedInState() {
  if (hasInitialized) {
    setLoggedInState();
    return;
  }
  hasInitialized = true;
  setLoggedInState();
  log('登录态已确认，可以执行补偿操作', 'success');
}

async function reconcileCloudNotify() {
  try {
    await StaticAuth.requireAuth({ message: '请先登录后再执行补偿' });
  } catch (error) {
    if (!StaticAuth.isAuthError(error)) {
      log(`登录检查失败: ${error.message}`, 'error');
    }
    return;
  }

  if (!confirmCheck.checked) {
    log('请先勾选确认项', 'warn');
    return;
  }

  const confirmed = window.confirm('确认批量补偿所有符合条件的上传通知吗？');
  if (!confirmed) {
    log('已取消补偿操作', 'info');
    return;
  }

  isReconciling = true;
  reconcileBtn.textContent = '补偿调度中…';
  updateActionState();
  log('开始请求 /api/upload/reconcile-cloud-notify');

  try {
    const response = await StaticAuth.authFetch('/api/upload/reconcile-cloud-notify', {
      method: 'POST',
    });
    const result = await readJsonSafely(response);

    if (!response.ok || result.code !== 200) {
      throw new Error(buildErrorMessage(response, result));
    }

    renderResult(result.data || { success: [], failed: [] });
    const success = Array.isArray(result.data?.success) ? result.data.success : [];
    const failed = Array.isArray(result.data?.failed) ? result.data.failed : [];
    log(
      `补偿调度完成：成功 ${success.length} 条，失败 ${failed.length} 条`,
      failed.length ? 'warn' : 'success',
    );
  } catch (error) {
    if (StaticAuth.isAuthError(error)) {
      return;
    }
    log(`补偿请求失败: ${error.message}`, 'error');
  } finally {
    isReconciling = false;
    reconcileBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>开始批量补偿</title><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg> 开始批量补偿';
    updateActionState();
  }
}

StaticAuth.init({
  pageName: '批量补偿上传',
  onLogin: restoreLoggedInState,
  onLogout: setLoggedOutState,
});

confirmCheck.addEventListener('change', updateActionState);
reconcileBtn.addEventListener('click', reconcileCloudNotify);
clearResultBtn.addEventListener('click', clearResult);

StaticAuth.requireAuth({ message: '请先登录后进入批量补偿上传' })
  .then(restoreLoggedInState)
  .catch((error) => {
    setLoggedOutState();
    if (!StaticAuth.isAuthError(error)) {
      log(`登录检查失败: ${error.message}`, 'error');
    }
  });

updateActionState();
