const API_BASE = '/api/monitor';
let refreshTimer = null;
let hasInitialized = false;
let monitorGeneration = 0;

        function pctClass(pct) {
            if (pct >= 85) return 'crit';
            if (pct >= 60) return 'warn';
            return '';
        }

        async function fetchSystemInfo() {
            const response = await StaticAuth.authFetch(`${API_BASE}/system`);
            const result = await response.json();
            if (result.code === 200 && result.data) {
                return result.data;
            }
            return null;
        }

        async function fetchRobotStatus() {
            const response = await StaticAuth.authFetch(`${API_BASE}/robot`);
            const result = await response.json();
            if (result.code === 200 && result.data) {
                return result.data;
            }
            return null;
        }

        function setProgress(barId, textId, pct) {
            const bar = document.getElementById(barId);
            const text = document.getElementById(textId);
            const safe = Math.max(0, Math.min(100, pct));
            bar.style.width = `${safe}%`;
            bar.className = `progress-fill ${pctClass(safe)}`;
            text.textContent = `${pct.toFixed(1)}%`;
        }

        function updateSystemInfo(data) {
            document.getElementById('cpuTotalCores').textContent = data.cpu.total_cores;
            document.getElementById('cpuPhysicalCores').textContent = data.cpu.physical_cores;
            document.getElementById('cpuPercent').textContent = `${data.cpu.cpu_percent.toFixed(1)}%`;
            setProgress('cpuProgress', 'cpuProgressText', data.cpu.cpu_percent);
            document.getElementById('cpuFreq').textContent =
                `${data.cpu.freq_min.toFixed(0)} – ${data.cpu.freq_max.toFixed(0)} MHz`;

            document.getElementById('memTotal').textContent = `${data.memory.total_gb.toFixed(2)} GB`;
            document.getElementById('memUsed').textContent = `${data.memory.used_gb.toFixed(2)} GB`;
            document.getElementById('memAvailable').textContent = `${data.memory.available_gb.toFixed(2)} GB`;
            document.getElementById('memPercent').textContent = `${data.memory.percent.toFixed(1)}%`;
            setProgress('memProgress', 'memProgressText', data.memory.percent);

            document.getElementById('diskTotal').textContent = `${data.disk.total_gb.toFixed(2)} GB`;
            document.getElementById('diskUsed').textContent = `${data.disk.used_gb.toFixed(2)} GB`;
            document.getElementById('diskFree').textContent = `${data.disk.free_gb.toFixed(2)} GB`;
            document.getElementById('diskPercent').textContent = `${data.disk.percent.toFixed(1)}%`;
            setProgress('diskProgress', 'diskProgressText', data.disk.percent);

            document.getElementById('platformSystem').textContent = data.platform.system;
            document.getElementById('platformPlatform').textContent = data.platform.platform;
            document.getElementById('platformRelease').textContent = data.platform.release;
            document.getElementById('platformMac').textContent = data.platform.mac_address || '-';
            document.getElementById('platformIp').textContent = data.platform.ip_address || '-';
        }

        function formatRegisterInfo(registerInfo) {
            if (!registerInfo || typeof registerInfo !== 'object') {
                return '-';
            }
            const items = Object.entries(registerInfo).filter(([, value]) => {
                if (value === null || value === undefined) return false;
                return String(value).trim() !== '';
            });
            if (!items.length) return '-';
            return items.map(([key, value]) => `${key}: ${value}`).join(' | ');
        }

        function appendInfoRow(container, label, value) {
            const row = document.createElement('div');
            row.className = 'info-row';

            const labelEl = document.createElement('span');
            labelEl.className = 'info-label';
            labelEl.textContent = label;

            const valueEl = document.createElement('span');
            valueEl.className = 'info-value';
            valueEl.textContent = value;

            row.append(labelEl, valueEl);
            container.appendChild(row);
        }

        function updateRobotStatus(data) {
            document.getElementById('robotModel').textContent = data.metadata.robot_model || '-';
            document.getElementById('robotType').textContent = data.metadata.robot_type || '-';
            document.getElementById('robotDesc').textContent =
                data.metadata.robot_desc.join(', ') || '-';
            document.getElementById('robotRegisterInfo').textContent =
                formatRegisterInfo(data.metadata.robot_register_info);

            const componentsDiv = document.getElementById('components');
            componentsDiv.innerHTML = '';

            data.components.forEach(comp => {
                const compCard = document.createElement('div');
                compCard.className = 'component-card';

                let statusClass = 'status-unknown';
                if (comp.connect_state === 'connected') statusClass = 'status-connected';
                else if (comp.connect_state === 'disconnected') statusClass = 'status-disconnected';

                const header = document.createElement('div');
                header.className = 'component-header';

                const componentId = document.createElement('span');
                componentId.textContent = comp.component_id;

                const status = document.createElement('span');
                status.className = `status-badge ${statusClass}`;
                status.textContent = comp.connect_state;

                header.append(componentId, status);
                compCard.appendChild(header);

                appendInfoRow(compCard, '频率', `${comp.hz} Hz`);

                if (comp.width && comp.height) {
                    appendInfoRow(compCard, '分辨率', `${comp.width} × ${comp.height}`);
                }
                if (comp.jpeg_quality) {
                    appendInfoRow(compCard, 'JPEG 质量', comp.jpeg_quality);
                }
                if (comp.depth_scale !== null && comp.depth_scale !== undefined) {
                    appendInfoRow(compCard, '深度缩放', comp.depth_scale);
                }
                if (comp.brand && comp.model) {
                    appendInfoRow(compCard, '型号', `${comp.brand} ${comp.model}`);
                }
                if (comp.detail) {
                    appendInfoRow(compCard, '详情', comp.detail);
                }
                if (comp.joint_data_dim) {
                    appendInfoRow(compCard, '关节数据维度', comp.joint_data_dim);
                }
                if (comp.eef_data_dim) {
                    appendInfoRow(compCard, '末端执行器维度', comp.eef_data_dim);
                }
                if (comp.gripper_data_dim) {
                    appendInfoRow(compCard, '夹爪数据维度', comp.gripper_data_dim);
                }
                if (comp.joint_speed_dim) {
                    appendInfoRow(compCard, '关节速度维度', comp.joint_speed_dim);
                }
                if (comp.current_dim) {
                    appendInfoRow(compCard, '电流维度', comp.current_dim);
                }
                if (comp.effort_dim) {
                    appendInfoRow(compCard, '力矩维度', comp.effort_dim);
                }

                componentsDiv.appendChild(compCard);
            });
        }

        function stopRefreshTimer() {
            if (!refreshTimer) {
                return;
            }
            clearInterval(refreshTimer);
            refreshTimer = null;
        }

        function startRefreshTimer() {
            stopRefreshTimer();
            refreshTimer = setInterval(() => {
                loadData();
            }, 30000);
        }

        function setLoggedOutState() {
            monitorGeneration += 1;
            hasInitialized = false;
            stopRefreshTimer();
            document.getElementById('refreshBtn').disabled = true;
            document.getElementById('loading').style.display = 'none';
            document.getElementById('content').style.display = 'none';
            document.getElementById('error').hidden = true;
            document.getElementById('lastUpdate').textContent = '请先登录';
        }

        async function initPage() {
            const generation = monitorGeneration;
            document.getElementById('refreshBtn').disabled = false;
            const loaded = await loadData(generation);
            if (!loaded || generation !== monitorGeneration || !hasInitialized) {
                return;
            }
            startRefreshTimer();
        }

        async function initPageOnce() {
            if (hasInitialized) {
                return;
            }
            hasInitialized = true;
            await initPage();
        }

        function isCurrentLoad(generation) {
            return generation === monitorGeneration && hasInitialized;
        }

        async function loadData(generation = monitorGeneration) {
            try {
                if (!isCurrentLoad(generation)) {
                    return false;
                }
                document.getElementById('loading').style.display = 'block';
                document.getElementById('content').style.display = 'none';
                document.getElementById('error').hidden = true;

                const [systemInfo, robotStatus] = await Promise.all([
                    fetchSystemInfo(),
                    fetchRobotStatus(),
                ]);

                if (!isCurrentLoad(generation)) {
                    return false;
                }

                if (systemInfo) {
                    updateSystemInfo(systemInfo);
                }
                if (robotStatus) {
                    updateRobotStatus(robotStatus);
                }

                document.getElementById('loading').style.display = 'none';
                document.getElementById('content').style.display = 'block';
                document.getElementById('lastUpdate').textContent =
                    `最后更新 · ${new Date().toLocaleString('zh-CN', { hour12: false })}`;
                return true;
            } catch (error) {
                if (StaticAuth.isAuthError(error)) {
                    setLoggedOutState();
                    return false;
                }
                if (!isCurrentLoad(generation)) {
                    return false;
                }
                document.getElementById('loading').style.display = 'none';
                document.getElementById('error').hidden = false;
                document.getElementById('errorText').textContent = `加载失败: ${error.message}`;
                return false;
            }
        }

        async function refreshData() {
            try {
                const response = await StaticAuth.authFetch(`${API_BASE}/refresh`, { method: 'POST' });
                const result = await response.json();
                if (result.code === 200) {
                    await loadData();
                } else {
                    alert(`刷新失败: ${result.msg}`);
                }
            } catch (error) {
                if (StaticAuth.isAuthError(error)) {
                    setLoggedOutState();
                    return;
                }
                alert(`刷新失败: ${error.message}`);
            }
        }

        StaticAuth.init({
            pageName: '系统监控面板',
            onLogin: initPageOnce,
            onLogout: setLoggedOutState,
        });

        document.getElementById('refreshBtn')?.addEventListener('click', refreshData);

        setLoggedOutState();
        StaticAuth.requireAuth({ message: '请先登录后进入系统监控面板' })
            .then(initPageOnce)
            .catch((error) => {
                setLoggedOutState();
                if (!StaticAuth.isAuthError(error)) {
                    document.getElementById('error').hidden = false;
                    document.getElementById('errorText').textContent = `登录检查失败: ${error.message}`;
                }
            });
