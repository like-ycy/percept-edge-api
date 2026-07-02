const olderThanDaysInput = document.getElementById('olderThanDays');
const limitInput = document.getElementById('limit');
const previewBtn = document.getElementById('previewBtn');
const clearBtn = document.getElementById('clearBtn');
const selectAllBtn = document.getElementById('selectAllBtn');
const executeBtn = document.getElementById('executeBtn');
const cutoffText = document.getElementById('cutoffText');
const eligibleCount = document.getElementById('eligibleCount');
const reclaimableSize = document.getElementById('reclaimableSize');
const missingCount = document.getElementById('missingCount');
const unsafeCount = document.getElementById('unsafeCount');
const itemsDiv = document.getElementById('items');
const logDiv = document.getElementById('log');

const CONFIRM_TEXT = 'DELETE UPLOADED COLLECTIONS';

let isPageReady = false;
let hasInitialized = false;
let isBusy = false;
let latestEligible = [];

function readPositiveInt(input, fallback) {
  const value = Number.parseInt(input.value, 10);
  if (Number.isNaN(value) || value <= 0) return fallback;
  return value;
}

async function readJsonSafely(response) {
  try { return await response.json(); }
  catch (error) { return { code: response.status, msg: '\u54cd\u5e94 JSON \u89e3\u6790\u5931\u8d25: ' + error.message, data: null }; }
}

function log(message, type) {
  const entry = document.createElement('div');
  entry.className = 'log-entry ' + (type || 'info');
  entry.textContent = '[' + new Date().toLocaleString('zh-CN', { hour12: false }) + '] ' + message;
  logDiv.appendChild(entry);
  logDiv.scrollTop = logDiv.scrollHeight;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return (value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)) + ' ' + units[unit];
}

function selectedRecordIds() {
  const ids = [];
  const inputs = itemsDiv.querySelectorAll('input[data-record-id]:checked');
  for (let i = 0; i < inputs.length; i++) ids.push(Number(inputs[i].dataset.recordId));
  return ids;
}

function updateActionState() {
  previewBtn.disabled = !isPageReady || isBusy;
  executeBtn.disabled = !isPageReady || isBusy || selectedRecordIds().length === 0;
  selectAllBtn.disabled = !isPageReady || isBusy || latestEligible.length === 0;
}

function renderItems(eligible) {
  itemsDiv.textContent = '';
  if (!eligible.length) { updateActionState(); return; }
  for (let i = 0; i < eligible.length; i++) {
    const item = eligible[i];
    const row = document.createElement('label');
    row.className = 'log-entry info';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.dataset.recordId = String(item.record_id);
    checkbox.addEventListener('change', updateActionState);
    row.appendChild(checkbox);
    row.appendChild(document.createTextNode(
      ' #' + item.record_id + ' \u00b7 ' + formatBytes(item.size_bytes)
      + ' \u00b7 cloud_id=' + item.cloud_id
      + ' \u00b7 ' + (item.path || item.output_dir || '\u65e0\u8def\u5f84')
    ));
    itemsDiv.appendChild(row);
  }
  updateActionState();
}

function renderPreview(data) {
  const summary = data.summary || {};
  latestEligible = Array.isArray(data.eligible) ? data.eligible : [];
  cutoffText.textContent = data.cutoff || '\u672a\u77e5\u622a\u6b62\u65f6\u95f4';
  eligibleCount.textContent = String(summary.eligible_count || 0);
  reclaimableSize.textContent = formatBytes(summary.reclaimable_bytes || 0);
  missingCount.textContent = String(summary.missing_count || 0);
  unsafeCount.textContent = String(summary.unsafe_count || 0);
  renderItems(latestEligible);
}

function clearPage() {
  latestEligible = [];
  cutoffText.textContent = '\u672a\u626b\u63cf';
  eligibleCount.textContent = '0';
  reclaimableSize.textContent = '0 B';
  missingCount.textContent = '0';
  unsafeCount.textContent = '0';
  itemsDiv.textContent = '';
  logDiv.textContent = '';
  updateActionState();
}

function buildErrorMessage(response, result) {
  if (result && result.msg) return result.msg;
  if (result && result.detail) return result.detail;
  return 'HTTP ' + response.status;
}

async function previewCleanup() {
  try { await StaticAuth.requireAuth({ message: '\u8bf7\u5148\u767b\u5f55\u540e\u518d\u626b\u63cf\u6e05\u7406\u6570\u636e' }); }
  catch (error) { if (!StaticAuth.isAuthError(error)) log(error.message, 'error'); return; }

  const olderThanDays = readPositiveInt(olderThanDaysInput, 3);
  const limit = readPositiveInt(limitInput, null);
  const params = new URLSearchParams({ older_than_days: String(olderThanDays) });
  if (limit !== null) params.set('limit', String(limit));

  isBusy = true;
  updateActionState();
  log('\u5f00\u59cb\u626b\u63cf\uff1a\u4fdd\u7559 ' + olderThanDays + ' \u5929' + (limit ? '\uff0c\u4e0a\u9650 ' + limit + ' \u6761' : ''));

  try {
    const response = await StaticAuth.authFetch('/api/storage/cleanup/preview?' + params.toString());
    const result = await readJsonSafely(response);
    if (!response.ok || result.code !== 200) throw new Error(buildErrorMessage(response, result));
    renderPreview(result.data || {});
    log('\u626b\u63cf\u5b8c\u6210\uff1a\u53ef\u6e05\u7406 ' + (result.data && result.data.summary ? result.data.summary.eligible_count || 0 : 0) + ' \u6761', 'success');
  } catch (error) {
    if (!StaticAuth.isAuthError(error)) log('\u626b\u63cf\u5931\u8d25: ' + error.message, 'error');
  } finally {
    isBusy = false;
    updateActionState();
  }
}

async function executeCleanup() {
  const recordIds = selectedRecordIds();
  if (!recordIds.length) { log('请先选择要删除本地目录的记录', 'warn'); return; }
  const confirmText = prompt('\u786e\u8ba4\u5220\u9664 ' + recordIds.length + ' \u6761\u672c\u5730\u76ee\u5f55\u3002\u8bf7\u8f93\u5165\uff1a' + CONFIRM_TEXT);
  if (confirmText !== CONFIRM_TEXT) { log('\u786e\u8ba4\u6587\u672c\u4e0d\u5339\u914d\uff0c\u5df2\u53d6\u6d88\u5220\u9664', 'warn'); return; }

  isBusy = true;
  updateActionState();
  log('开始删除 ' + recordIds.length + ' 条本地目录（数据库记录保留）');

  try {
    const response = await StaticAuth.authFetch('/api/storage/cleanup/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        older_than_days: readPositiveInt(olderThanDaysInput, 3),
        record_ids: recordIds,
        confirm_text: confirmText,
      }),
    });
    const result = await readJsonSafely(response);
    if (!response.ok || result.code !== 200) throw new Error(buildErrorMessage(response, result));
    const s = (result.data && result.data.summary) || {};
    log('本地目录删除完成：成功 ' + (s.deleted_count || 0) + ' 条，失败 ' + (s.failed_count || 0) + ' 条，跳过 ' + (s.skipped_count || 0) + ' 条；数据库记录保留', s.failed_count || s.skipped_count ? 'warn' : 'success');
    await previewCleanup();
  } catch (error) {
    if (!StaticAuth.isAuthError(error)) log('删除本地目录失败: ' + error.message, 'error');
  } finally {
    isBusy = false;
    updateActionState();
  }
}

function setLoggedOutState() { hasInitialized = false; isPageReady = false; updateActionState(); }

function restoreLoggedInState() {
  if (!hasInitialized) { hasInitialized = true; log('\u767b\u5f55\u6001\u5df2\u786e\u8ba4\uff0c\u53ef\u4ee5\u626b\u63cf\u6e05\u7406\u6570\u636e', 'success'); }
  isPageReady = true;
  updateActionState();
}

StaticAuth.init({ pageName: '\u6e05\u7406\u5df2\u4e0a\u4f20\u6570\u636e', onLogin: restoreLoggedInState, onLogout: setLoggedOutState });
previewBtn.addEventListener('click', previewCleanup);
clearBtn.addEventListener('click', clearPage);
selectAllBtn.addEventListener('click', function () {
  const inputs = itemsDiv.querySelectorAll('input[data-record-id]');
  for (let i = 0; i < inputs.length; i++) inputs[i].checked = true;
  updateActionState();
});
executeBtn.addEventListener('click', executeCleanup);

StaticAuth.requireAuth({ message: '\u8bf7\u5148\u767b\u5f55\u540e\u8fdb\u5165\u6e05\u7406\u5df2\u4e0a\u4f20\u6570\u636e\u9875\u9762' })
  .then(restoreLoggedInState)
  .catch(function (error) {
    setLoggedOutState();
    if (!StaticAuth.isAuthError(error)) log(error.message, 'error');
  });

updateActionState();
