const $id      = document.getElementById("record_id");
        const $btn     = document.getElementById("btn-query");
        const $msg     = document.getElementById("msg");
        const $result  = document.getElementById("result");
        const $summary = document.getElementById("summary");
        const $cams    = document.getElementById("cams");
        const $camCnt  = document.getElementById("cam-count");
        const $segs    = document.getElementById("segs");
        const $segCnt  = document.getElementById("seg-count");
        let isPageReady = false;

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

        function setLoggedOutState() {
            isPageReady = false;
            $btn.disabled = true;
            $result.style.display = 'none';
            setMsg('请先登录后查询原始采集数据', 'loading');
        }

        function setLoggedInState() {
            isPageReady = true;
            $btn.disabled = false;
            setMsg('', '');
        }

        function row(k, v) {
            const rk = document.createElement("div");
            rk.className = "k";
            rk.textContent = k;
            const rv = document.createElement("div");
            rv.className = "v";
            rv.textContent = v ?? "";
            return [rk, rv];
        }

        function render(info) {
            $summary.innerHTML = "";
            const kvs = [
                ["record_id",      info.record_id],
                ["output_dir",     info.output_dir],
                ["capture_dir",    info.capture_dir],
                ["sealed",         info.sealed],
                ["frame_count",    info.frame_count],
                ["raw_bytes",      info.raw_bytes],
                ["start_time",     info.start_time],
                ["end_time",       info.end_time],
                ["sampled_frames", info.sampled_frames],
            ];
            for (const [k, v] of kvs) {
                const [a, b] = row(k, v);
                $summary.appendChild(a);
                $summary.appendChild(b);
            }

            $cams.innerHTML = "";
            $camCnt.textContent = info.camera_count;
            for (const cam of info.cameras) {
                const s = document.createElement("span");
                s.textContent = cam;
                $cams.appendChild(s);
            }

            $segs.innerHTML = "";
            $segCnt.textContent = info.segment_count;
            for (let i = 0; i < info.segments.length; i++) {
                const [a, b] = row(`#${i + 1}`, info.segments[i]);
                $segs.appendChild(a);
                $segs.appendChild(b);
            }

            $result.style.display = "block";
        }

        async function query() {
            if (!isPageReady) {
                try {
                    await StaticAuth.requireAuth({ message: '请先登录后查询原始采集数据' });
                    setLoggedInState();
                } catch (error) {
                    if (!StaticAuth.isAuthError(error)) {
                        setMsg(`登录检查失败: ${error.message}`, 'error');
                    }
                    return;
                }
            }
            const id = $id.value.trim();
            if (!id) {
                setMsg("请填写 record_id", "error");
                $id.focus();
                return;
            }
            setMsg("查询中…", "loading");
            $btn.disabled = true;
            $result.style.display = "none";
            try {
                const r = await StaticAuth.authFetch(`/api/storage/raw-info?record_id=${encodeURIComponent(id)}`);
                const j = await r.json();
                if (!r.ok) {
                    setMsg(`查询失败: ${j.message || r.status}`, "error");
                    return;
                }
                render(j.data);
                setMsg("查询成功", "success");
            } catch (e) {
                if (StaticAuth.isAuthError(e)) {
                    return;
                }
                setMsg(`网络错误: ${e.message}`, "error");
            } finally {
                $btn.disabled = !isPageReady;
            }
        }

        $btn.addEventListener("click", query);
        $id.addEventListener("keydown", (e) => { if (e.key === "Enter") query(); });
        StaticAuth.init({
            pageName: '原始采集数据查看',
            onLogin: setLoggedInState,
            onLogout: setLoggedOutState,
        });

        setLoggedOutState();
        StaticAuth.requireAuth({ message: '请先登录后进入原始采集数据查看' })
            .then(setLoggedInState)
            .catch((error) => {
                setLoggedOutState();
                if (!StaticAuth.isAuthError(error)) {
                    setMsg(`登录检查失败: ${error.message}`, 'error');
                }
            });
