(function () {
  const STORAGE_KEYS = {
    token: 'perceptStaticAuthToken',
    username: 'perceptStaticAuthUsername',
    env: 'perceptStaticAuthEnv',
  };

  const DEFAULT_ENV = 'prod';
  const DEFAULT_USERNAME = 'robot_wx';
  const AUTH_ERROR_NAME = 'StaticAuthError';

  const LOGIN_ENVIRONMENTS = {
    prod: {
      label: 'prod',
      host: '192.168.21.139',
      url: 'http://192.168.21.139:8989/embodied_api/auth/login',
    },
    test: {
      label: 'test',
      host: '192.168.21.138',
      url: 'http://192.168.21.138:8989/embodied_api/auth/login',
    },
  };

  let authOptions = {};
  let modalEl = null;
  let loginPromise = null;
  let resolveLogin = null;
  let rejectLogin = null;
  let loginInFlight = false;

  class StaticAuthError extends Error {
    constructor(message, status = 'auth_required') {
      super(message);
      this.name = AUTH_ERROR_NAME;
      this.status = status;
    }
  }

  function readStorage(key) {
    try {
      return localStorage.getItem(key) || '';
    } catch (error) {
      console.warn(`localStorage 读取失败: ${error.message}`);
      return '';
    }
  }

  function writeStorage(key, value) {
    try {
      localStorage.setItem(key, value);
      return true;
    } catch (error) {
      console.warn(`localStorage 写入失败: ${error.message}`);
      return false;
    }
  }

  function removeStorage(key) {
    try {
      localStorage.removeItem(key);
    } catch (error) {
      console.warn(`localStorage 删除失败: ${error.message}`);
    }
  }

  function isKnownEnv(env) {
    return Object.prototype.hasOwnProperty.call(LOGIN_ENVIRONMENTS, env);
  }

  function getStoredEnv() {
    const stored = readStorage(STORAGE_KEYS.env);
    return isKnownEnv(stored) ? stored : DEFAULT_ENV;
  }

  function getEnvironment(env) {
    return LOGIN_ENVIRONMENTS[isKnownEnv(env) ? env : DEFAULT_ENV];
  }

  function getSession() {
    const token = readStorage(STORAGE_KEYS.token);
    if (!token) {
      return null;
    }

    const env = getStoredEnv();
    return {
      token,
      username: readStorage(STORAGE_KEYS.username),
      env,
    };
  }

  function setSession(session) {
    const tokenSaved = writeStorage(STORAGE_KEYS.token, session.token);
    const usernameSaved = writeStorage(STORAGE_KEYS.username, session.username || 'unknown');
    const envSaved = writeStorage(
      STORAGE_KEYS.env,
      isKnownEnv(session.env) ? session.env : DEFAULT_ENV,
    );
    if (tokenSaved && usernameSaved && envSaved) {
      return true;
    }
    clearSession();
    return false;
  }

  function clearSession() {
    removeStorage(STORAGE_KEYS.token);
    removeStorage(STORAGE_KEYS.username);
    removeStorage(STORAGE_KEYS.env);
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

  function ensureModal() {
    if (modalEl) {
      return modalEl;
    }

    modalEl = document.createElement('div');
    modalEl.className = 'static-auth-backdrop';
    modalEl.innerHTML = `
      <section class="static-auth-dialog card" role="dialog" aria-modal="true" aria-labelledby="staticAuthTitle">
        <div class="card-title">
          <h2 id="staticAuthTitle">登录</h2>
          <span class="badge neutral">IAM</span>
        </div>
        <form id="staticAuthForm" class="stack">
          <div class="field">
            <label for="staticAuthEnv">登录环境</label>
            <select id="staticAuthEnv" autocomplete="off">
              <option value="prod">prod · 192.168.21.139</option>
              <option value="test">test · 192.168.21.138</option>
            </select>
            <div class="form-help" id="staticAuthEnvHint"></div>
          </div>
          <div class="field">
            <label for="staticAuthUsername">用户名</label>
            <input type="text" id="staticAuthUsername" required autocomplete="username">
          </div>
          <div class="field">
            <label for="staticAuthPassword">密码</label>
            <input type="password" id="staticAuthPassword" required autocomplete="current-password">
          </div>
          <div class="login-error" id="staticAuthError"></div>
          <div class="btn-row">
            <button type="submit" class="btn primary" id="staticAuthSubmitBtn">登录</button>
            <button type="button" class="btn" id="staticAuthCancelBtn">取消</button>
          </div>
        </form>
      </section>
    `;
    document.body.appendChild(modalEl);

    document.getElementById('staticAuthForm').addEventListener('submit', submitLogin);
    document.getElementById('staticAuthCancelBtn').addEventListener('click', cancelLogin);
    document.getElementById('staticAuthEnv').addEventListener('change', updateLoginEnvHint);
    updateLoginEnvHint();
    return modalEl;
  }

  function updateLoginEnvHint() {
    const envSelect = document.getElementById('staticAuthEnv');
    const hint = document.getElementById('staticAuthEnvHint');
    if (!envSelect || !hint) {
      return;
    }
    const env = getEnvironment(envSelect.value);
    hint.textContent = `当前登录 API: ${env.host}`;
  }

  function setLoginError(message) {
    const errorEl = document.getElementById('staticAuthError');
    if (errorEl) {
      errorEl.textContent = message || '';
    }
  }

  function setLoginLoading(loading) {
    loginInFlight = loading;
    const submitBtn = document.getElementById('staticAuthSubmitBtn');
    if (!submitBtn) {
      return;
    }
    submitBtn.disabled = loading;
    submitBtn.textContent = loading ? '登录中…' : '登录';
  }

  function showLoginModal(options = {}) {
    ensureModal();

    if (loginPromise) {
      if (options.message) {
        setLoginError(options.message);
      }
      modalEl.classList.add('show');
      return loginPromise;
    }

    const session = getSession();
    const envSelect = document.getElementById('staticAuthEnv');
    const usernameInput = document.getElementById('staticAuthUsername');
    const passwordInput = document.getElementById('staticAuthPassword');

    envSelect.value = session?.env || getStoredEnv();
    usernameInput.value = session?.username || readStorage(STORAGE_KEYS.username) || DEFAULT_USERNAME;
    passwordInput.value = '';
    setLoginError(options.message || '');
    setLoginLoading(false);
    updateLoginEnvHint();
    modalEl.classList.add('show');
    usernameInput.focus();

    loginPromise = new Promise((resolve, reject) => {
      resolveLogin = resolve;
      rejectLogin = reject;
    });
    return loginPromise;
  }

  function hideLoginModal() {
    if (modalEl) {
      modalEl.classList.remove('show');
    }
  }

  function settleLoginSuccess(session) {
    const resolve = resolveLogin;
    loginPromise = null;
    resolveLogin = null;
    rejectLogin = null;
    if (resolve) {
      resolve(session);
    }
  }

  function settleLoginCancel() {
    const reject = rejectLogin;
    loginPromise = null;
    resolveLogin = null;
    rejectLogin = null;
    if (reject) {
      reject(new StaticAuthError('登录已取消', 'cancelled'));
    }
  }

  function cancelLogin() {
    if (loginInFlight) {
      return;
    }
    hideLoginModal();
    settleLoginCancel();
  }

  async function submitLogin(event) {
    event.preventDefault();
    if (loginInFlight) {
      return;
    }

    const envSelect = document.getElementById('staticAuthEnv');
    const usernameInput = document.getElementById('staticAuthUsername');
    const passwordInput = document.getElementById('staticAuthPassword');
    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    if (!username || !password) {
      setLoginError('请输入用户名和密码');
      return;
    }

    const loginEnv = getEnvironment(envSelect.value);
    setLoginLoading(true);
    setLoginError('');

    try {
      const response = await fetch(loginEnv.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const result = await readJsonSafely(response);

      if (response.ok && result.code === 200 && result.data?.token) {
        const session = {
          token: result.data.token,
          username: result.data.username || username,
          env: loginEnv.label,
        };
        if (!setSession(session)) {
          setLoginError('登录成功，但浏览器无法保存登录态，请检查浏览器存储权限');
          return;
        }
        hideLoginModal();
        updateAuthUI();
        if (typeof authOptions.onLogin === 'function') {
          authOptions.onLogin(session);
        }
        settleLoginSuccess(session);
        return;
      }

      setLoginError(result.msg || `登录失败: HTTP ${response.status}`);
    } catch (error) {
      setLoginError(`登录失败: ${error.message}`);
    } finally {
      setLoginLoading(false);
    }
  }

  function renderStatusContainer(container) {
    const session = getSession();
    if (session) {
      const env = getEnvironment(session.env);
      container.innerHTML = `
        <span class="auth-status saved">已登录 · ${escapeHtml(session.username || 'unknown')} · ${env.label}</span>
        <button type="button" class="btn small" data-static-auth-login>切换环境</button>
        <button type="button" class="btn small" data-static-auth-logout>退出</button>
      `;
    } else {
      container.innerHTML = `
        <span class="auth-status empty">未登录</span>
        <button type="button" class="btn small" data-static-auth-login>登录</button>
      `;
    }

    container.querySelector('[data-static-auth-login]')?.addEventListener('click', () => {
      showLoginModal().catch((error) => {
        if (!isAuthError(error)) {
          console.error(error);
        }
      });
    });
    container.querySelector('[data-static-auth-logout]')?.addEventListener('click', logout);
  }

  function updateAuthUI() {
    document.querySelectorAll('[data-static-auth-status]').forEach(renderStatusContainer);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  async function requireAuth(options = {}) {
    const session = getSession();
    if (session) {
      return session;
    }
    return showLoginModal(options);
  }

  async function authFetch(url, options = {}) {
    const session = await requireAuth({ message: '请先登录后继续操作' });
    const headers = new Headers(options.headers || {});
    headers.set('Authorization', `Bearer ${session.token}`);

    const response = await fetch(url, { ...options, headers });
    if (response.status === 401 || response.status === 403) {
      const error = new StaticAuthError('登录态已失效或无权限，请重新登录', response.status);
      clearSession();
      updateAuthUI();
      if (typeof authOptions.onLogout === 'function') {
        authOptions.onLogout(error);
      }
      showLoginModal({ message: error.message }).catch((loginError) => {
        if (!isAuthError(loginError)) {
          console.error(loginError);
        }
      });
      throw error;
    }
    return response;
  }

  function logout() {
    clearSession();
    updateAuthUI();
    hideLoginModal();
    if (loginPromise) {
      settleLoginCancel();
    }
    if (typeof authOptions.onLogout === 'function') {
      authOptions.onLogout(new StaticAuthError('已退出登录', 'logout'));
    }
  }

  function init(options = {}) {
    authOptions = { ...authOptions, ...options };
    ensureModal();
    updateAuthUI();
  }

  function isAuthError(error) {
    return Boolean(error && error.name === AUTH_ERROR_NAME);
  }

  window.StaticAuth = {
    init,
    requireAuth,
    authFetch,
    getSession,
    logout,
    showLoginModal,
    isAuthError,
    AuthError: StaticAuthError,
    environments: LOGIN_ENVIRONMENTS,
    storageKeys: STORAGE_KEYS,
  };
}());
