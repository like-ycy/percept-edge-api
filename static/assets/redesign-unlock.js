const STATUS_URL  = "/api/collection/lock/status";
        const RELEASE_URL = "/api/collection/lock/release";

        const $status   = document.getElementById("status");
        const $badge    = document.getElementById("badge");
        const $msg      = document.getElementById("msg");
        const $operator = document.getElementById("operator");
        const $note     = document.getElementById("note");
        const $release  = document.getElementById("btn-release");
        const $refresh  = document.getElementById("btn-refresh");
        let isPageReady = false;
        let hasInitialized = false;

        const ICONS = {
            error:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
            success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
            loading: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
        };

        function setMsg(text, cls) {
            const icon = cls && ICONS[cls] ? ICONS[cls] : '';
            $msg.innerHTML = text ? `${icon}<span></span>` : '';
            const textEl = $msg.querySelector('span');
            if (textEl) {
                textEl.textContent = text;
            }
            $msg.className = `msg${cls ? ` ${cls}` : ''}`;
        }

        function setControlsEnabled(enabled) {
            $refresh.disabled = !enabled;
            $release.disabled = !enabled;
        }

        function setLoggedOutState() {
            hasInitialized = false;
            isPageReady = false;
            setControlsEnabled(false);
            $badge.textContent = 'LOGIN';
            $badge.className = 'badge neutral';
            setMsg('请先登录后查看采集锁状态', 'loading');
        }

        async function initPage() {
            isPageReady = true;
            setControlsEnabled(true);
            await loadStatus();
        }

        async function initPageOnce() {
            if (hasInitialized) {
                return;
            }
            hasInitialized = true;
            await initPage();
        }

        function renderState(state) {
            const lines = [
                `locked              = ${state.locked}`,
                `reason              = ${state.reason ?? ""}`,
                `triggered_record_id = ${state.triggered_record_id ?? ""}`,
                `triggered_at        = ${state.triggered_at ?? ""}`,
                `released_at         = ${state.released_at ?? ""}`,
                `released_by         = ${state.released_by ?? ""}`,
                `release_note        = ${state.release_note ?? ""}`,
            ];
            $status.textContent = lines.join("\n");
            if (state.locked) {
                $badge.textContent = "LOCKED";
                $badge.className = "badge locked";
                $release.disabled = false;
            } else {
                $badge.textContent = "UNLOCKED";
                $badge.className = "badge unlocked";
                $release.disabled = true;
            }
        }

        async function loadStatus() {
            setMsg("");
            try {
                const r = await StaticAuth.authFetch(STATUS_URL);
                const j = await r.json();
                if (!r.ok) {
                    setMsg(`加载失败: ${j.message || r.status}`, "error");
                    return;
                }
                renderState(j.data);
            } catch (e) {
                if (StaticAuth.isAuthError(e)) {
                    return;
                }
                setMsg(`网络错误: ${e.message}`, "error");
            }
        }

        async function release() {
            if (!isPageReady) {
                setMsg('请先登录后解锁', 'error');
                return;
            }
            const operator = $operator.value.trim();
            const note     = $note.value.trim();
            if (!operator) {
                setMsg("请填写操作者", "error");
                $operator.focus();
                return;
            }
            if (!confirm("确认解锁采集全局锁？")) return;

            $release.disabled = true;
            setMsg("解锁中…", "loading");
            try {
                const r = await StaticAuth.authFetch(RELEASE_URL, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ operator, note: note || null }),
                });
                const j = await r.json();
                if (!r.ok) {
                    setMsg(`解锁失败: ${j.message || r.status}`, "error");
                    $release.disabled = false;
                    return;
                }
                renderState(j.data);
                setMsg("解锁成功", "success");
            } catch (e) {
                if (StaticAuth.isAuthError(e)) {
                    return;
                }
                setMsg(`网络错误: ${e.message}`, "error");
                $release.disabled = false;
            }
        }

        $release.addEventListener("click", release);
        $refresh.addEventListener("click", loadStatus);
        StaticAuth.init({
            pageName: '采集锁解除',
            onLogin: initPageOnce,
            onLogout: setLoggedOutState,
        });

        setLoggedOutState();
        StaticAuth.requireAuth({ message: '请先登录后进入采集锁解除' })
            .then(initPageOnce)
            .catch((error) => {
                setLoggedOutState();
                if (!StaticAuth.isAuthError(error)) {
                    setMsg(`登录检查失败: ${error.message}`, 'error');
                }
            });
