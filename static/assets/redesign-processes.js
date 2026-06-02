const API_BASE = '';
        const els = {
            sampledAt: document.getElementById('sampled-at'),
            sourceBadge: document.getElementById('source-badge'),
            sourceText: document.getElementById('source-text'),
            platformBadge: document.getElementById('platform-badge'),
            platformText: document.getElementById('platform-text'),
            overview: document.getElementById('overview-grid'),
            cpuGrid: document.getElementById('cpu-grid'),
            procBody: document.getElementById('proc-body'),
            procCount: document.getElementById('proc-count'),
            errorArea: document.getElementById('error-area'),
            btnSnapshot: document.getElementById('btn-snapshot'),
            btnRefresh: document.getElementById('btn-refresh'),
            autoToggle: document.getElementById('auto-toggle'),
        };

        let autoTimer = null;
        let isPageReady = false;
        let hasInitialized = false;

        function fmtNum(n, digits = 1) {
            if (n === null || n === undefined || Number.isNaN(n)) return '—';
            return Number(n).toFixed(digits);
        }

        function fmtMB(mb) {
            if (mb === null || mb === undefined) return '—';
            if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
            return mb.toFixed(1);
        }

        function fmtTime(iso) {
            if (!iso) return '—';
            const d = new Date(iso);
            if (Number.isNaN(d.getTime())) return iso;
            return d.toLocaleString('zh-CN', { hour12: false });
        }

        function pctClass(pct) {
            if (pct >= 85) return 'crit';
            if (pct >= 60) return 'warn';
            return '';
        }

        function statusClass(s) {
            const v = (s || '').toLowerCase();
            if (v.includes('run')) return 'running';
            if (v.includes('zombie') || v.includes('dead')) return 'zombie';
            return 'sleeping';
        }

        function showError(msg) {
            els.errorArea.innerHTML = '<div class="error-box"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><span></span></div>';
            els.errorArea.querySelector('span').textContent = msg;
        }
        function clearError() { els.errorArea.innerHTML = ''; }

        function setControlsEnabled(enabled) {
            els.btnSnapshot.disabled = !enabled;
            els.btnRefresh.disabled = !enabled;
            els.autoToggle.disabled = !enabled;
        }

        function stopAutoRefresh() {
            if (autoTimer) {
                clearInterval(autoTimer);
                autoTimer = null;
            }
            els.autoToggle.checked = false;
        }

        function setLoggedOutState() {
            hasInitialized = false;
            isPageReady = false;
            stopAutoRefresh();
            setControlsEnabled(false);
            els.sampledAt.textContent = '—';
            els.sourceText.textContent = '请先登录';
            els.platformText.textContent = '—';
            els.procBody.innerHTML = '<tr><td colspan="13" class="empty">请先登录</td></tr>';
        }

        async function initPage() {
            isPageReady = true;
            setControlsEnabled(true);
            await load(false);
        }

        async function initPageOnce() {
            if (hasInitialized) {
                return;
            }
            hasInitialized = true;
            await initPage();
        }

        async function guardedLoad(refresh) {
            try {
                await StaticAuth.requireAuth({ message: '请先登录后查看进程监控' });
                await load(refresh);
            } catch (error) {
                if (!StaticAuth.isAuthError(error)) {
                    showError(error.message || String(error));
                }
            }
        }

        function renderOverview(sys, mainPid) {
            const cards = [
                { label: '主进程 PID', value: mainPid ?? '—' },
                { label: '逻辑 / 物理核心', value: `${sys.cpu_count_logical} / ${sys.cpu_count_physical}` },
                {
                    label: '内存使用率',
                    value: `${fmtNum(sys.memory_percent)} %`,
                    sub: `${fmtMB(sys.memory_used_mb)} / ${fmtMB(sys.memory_total_mb)} MB`,
                    bar: sys.memory_percent,
                },
                {
                    label: '系统平均负载',
                    value: sys.load_avg_1_5_15.map(x => fmtNum(x, 2)).join(' / '),
                    sub: '1m / 5m / 15m',
                },
                { label: '平台', value: sys.platform },
            ];
            els.overview.innerHTML = cards.map(c => `
                <div class="card">
                    <div class="label">${c.label}</div>
                    <div class="value">${escapeHtml(c.value)}</div>
                    ${c.sub ? `<div class="sub">${escapeHtml(c.sub)}</div>` : ''}
                    ${c.bar !== undefined ? `<div class="bar-track"><div class="bar-fill ${pctClass(c.bar)}" style="width:${Math.min(100, c.bar)}%"></div></div>` : ''}
                </div>
            `).join('');
        }

        function renderCpu(perCpu) {
            if (!perCpu || perCpu.length === 0) {
                els.cpuGrid.innerHTML = '<div class="empty">无数据</div>';
                return;
            }
            els.cpuGrid.innerHTML = perCpu.map((p, i) => `
                <div class="cpu-cell">
                    <div class="num">CPU${i}</div>
                    <div class="pct">${fmtNum(p)}%</div>
                    <div class="bar-track"><div class="bar-fill ${pctClass(p)}" style="width:${Math.min(100, p)}%"></div></div>
                </div>
            `).join('');
        }

        function renderProcs(procs, mainPid) {
            els.procCount.textContent = `（共 ${procs.length} 个）`;
            if (procs.length === 0) {
                els.procBody.innerHTML = '<tr><td colspan="13" class="empty">无数据</td></tr>';
                return;
            }
            els.procBody.innerHTML = procs.map(p => {
                const isMain = p.pid === mainPid;
                const stCls = statusClass(p.status);
                const aff = p.cpu_affinity?.length
                    ? (p.cpu_affinity.length > 6 ? `${p.cpu_affinity.slice(0, 6).join(',')}…` : p.cpu_affinity.join(','))
                    : '—';
                return `
                    <tr class="${isMain ? 'main' : ''}">
                        <td><span class="pill ${isMain ? 'main' : ''}">${p.pid}</span></td>
                        <td>${p.parent_pid ?? '—'}</td>
                        <td>${escapeHtml(p.name)}</td>
                        <td><span class="pill ${stCls}">${escapeHtml(p.status)}</span></td>
                        <td>${fmtNum(p.cpu_percent)}</td>
                        <td>${p.cpu_num ?? '—'}</td>
                        <td title="${escapeHtml(p.cpu_affinity ? p.cpu_affinity.join(',') : '')}">${escapeHtml(aff)}</td>
                        <td>${p.num_threads}</td>
                        <td>${fmtMB(p.memory_rss_mb)}</td>
                        <td>${fmtNum(p.memory_percent, 2)}</td>
                        <td>${p.num_fds ?? '—'}</td>
                        <td>${fmtTime(p.create_time)}</td>
                        <td class="cmdline" title="${escapeHtml(p.cmdline)}">${escapeHtml(p.cmdline) || '—'}</td>
                    </tr>
                `;
            }).join('');
        }

        function escapeHtml(s) {
            if (s === null || s === undefined) return '';
            return String(s)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        }

        async function load(refresh) {
            const url = `${API_BASE}/debug/processes${refresh ? '?refresh=true' : ''}`;
            if (!isPageReady) {
                return;
            }
            setControlsEnabled(false);
            try {
                const resp = await StaticAuth.authFetch(url, { cache: 'no-store' });
                const body = await resp.json();
                if (!resp.ok || body.code !== 200) {
                    throw new Error(body.message || `HTTP ${resp.status}`);
                }
                clearError();
                const data = body.data;
                els.sampledAt.textContent = fmtTime(data.sampled_at);
                els.sourceBadge.className = `badge${data.from_cache ? ' cache' : ''}`;
                els.sourceText.textContent = data.from_cache ? '快照缓存' : '实时采样';
                els.platformBadge.className = 'badge';
                els.platformText.textContent = data.system.platform;

                renderOverview(data.system, data.main_pid);
                renderCpu(data.system.per_cpu_percent);
                renderProcs(data.processes, data.main_pid);
            } catch (e) {
                if (StaticAuth.isAuthError(e)) {
                    return;
                }
                els.sourceBadge.className = 'badge error';
                els.sourceText.textContent = '请求失败';
                showError(e.message || String(e));
            } finally {
                setControlsEnabled(isPageReady);
            }
        }

        StaticAuth.init({
            pageName: '进程监控面板',
            onLogin: initPageOnce,
            onLogout: setLoggedOutState,
        });

        els.btnSnapshot.addEventListener('click', () => guardedLoad(false));
        els.btnRefresh.addEventListener('click', () => guardedLoad(true));
        els.autoToggle.addEventListener('change', (event) => {
            if (autoTimer) {
                clearInterval(autoTimer);
                autoTimer = null;
            }
            if (event.target.checked && isPageReady) {
                guardedLoad(false);
                autoTimer = setInterval(() => guardedLoad(false), 5000);
            }
        });

        setLoggedOutState();
        StaticAuth.requireAuth({ message: '请先登录后进入进程监控面板' })
            .then(initPageOnce)
            .catch((error) => {
                setLoggedOutState();
                if (!StaticAuth.isAuthError(error)) {
                    showError(error.message || String(error));
                }
            });
