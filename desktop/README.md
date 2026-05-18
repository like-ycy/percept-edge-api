# Desktop 运行控制台

基于 PySide6 (Qt 6) 的桌面运行控制台，用于管理 Percept Edge 多进程运行时的启动、监控、停止。

按 **Profile + Flow + Adapter** 三层架构组织，新机型与新启动顺序通过组合现有原子能力实现，不再为每个型号复制整套 UI / 服务代码。

## 启动

```bash
# 默认机型来自环境变量 PERCEPT_ROBOT / APP_ROBOT
uv run python -m desktop.main

# 显式指定机型与环境
uv run python -m desktop.main --robot robot-cr4c --env test

# 通过启动脚本（生产/桌面快捷方式）
bash desktop/launch.sh --robot robot-cr4c
```

## 已支持机型

| Profile         | display | 启动链路                                                  |
|-----------------|---------|-----------------------------------------------------------|
| `robot-cr4c`    | CR4C    | 同构臂：roscore → CAN → ros_slave → ros_master → robot_os → api → nginx；VR：roscore → CAN → VR ROS → VR 准备确认 → robot_os → api → nginx |
| `robot-cr4a`    | CR4A    | 同 CR4C（VR ROS 路径按机器人配置区分）    |
| `robot-cr1`     | CR1     | roscore → CAN(0..4) → master1 → pos_follow1 → master2 → pos_follow2 → robot_os → api → nginx（仅 bilateral） |
| `robot-w1`      | W1      | VR：robot_os → api → nginx（无 ROS / CAN 链路）           |

## 目录结构

```
desktop/
├── main.py                 # CLI 入口，--robot / --env
├── app.py                  # 装配 RuntimeFacade + MainWindow
├── launch.sh               # 桌面/自启用启动脚本
├── install_desktop.sh      # 安装 .desktop 快捷方式
├── theme/styles.qss        # 暗色主题
├── assets/icons/icon.png
├── profiles/               # 机型 = adapter 组合 + flow_factory
│   ├── base.py             # RobotProfile dataclass
│   ├── registry.py         # @register / load_profile
│   └── cr1.py / cr4a.py / cr4c.py / w1.py
├── flows/                  # 启动编排
│   ├── base.py             # Step / StepKind / FlowEvent
│   ├── sequential.py       # SequentialFlowRunner
│   ├── cr_flow.py          # CR 系列共用，profile.extra 控制差异
│   └── w1_flow.py
├── adapters/               # 单进程的 ProcessSpec / HealthProbe 产出器
│   ├── base.py             # Adapter Protocol + ProcessSpec / HealthProbe / BuildContext
│   ├── ros_adapter.py      # core / slave / master 三角色
│   ├── can_adapter.py      # slcand 一次性脚本
│   ├── robot_os_adapter.py # robot_os pyz + zmq_monitor probe（可选 source ROS setup）
│   └── api_adapter.py      # main.py + http probe
├── services/               # 通用基础服务
│   ├── config_loader.py    # RuntimeConfig.load(repo_root, env)
│   ├── process_manager.py  # ProcessManager(QObject)，spawn/stop/kill + 代次隔离
│   ├── health_checker.py   # HealthChecker (http / zmq_monitor) + RuntimeHealthCollector
│   ├── runtime_state.py    # RuntimeStateMachine + RuntimeStateStore
│   ├── runtime_facade.py   # 顶层门面，桥接 FlowEvent ↔ snapshot/log
│   ├── status_poll_service.py
│   └── process_bridge.py   # ProcessLineBuffer
├── models/                 # 纯数据
│   ├── log_entry.py / runtime_health.py / runtime_state.py
│   ├── stage_state.py / runtime_snapshot.py
└── widgets/                # PySide6 UI
    ├── main_window.py      # 顶部 Control + 左 Stage + 右 Log + 底 Footer
    ├── control_panel.py    # 启停 / 环境 / 全局徽章
    ├── log_panel.py        # 日志表格 + 过滤
    └── status_panel.py     # StatusChip / StagePanel / FooterStatusBar
```

## 三层契约速览

| 层      | 职责                                                | 不做                       |
|---------|-----------------------------------------------------|----------------------------|
| Adapter | 给定 `BuildContext` 产出 `ProcessSpec` / `HealthProbe` | 不持有进程，不做生命周期 |
| Flow    | 声明一组 `Step`，串行驱动 spawn / probe / sleep        | 不直接调 QProcess         |
| Profile | 选定 adapter 实例 + flow_factory + extra 开关         | 不知道 UI / config        |

完整契约见 `desktop/adapters/base.py`、`desktop/flows/base.py`、`desktop/profiles/base.py`。

## 配置来源

- 顶层配置：`config/base.toml` + `config/robots/<robot>/base.toml` + `config/robots/<robot>/<env>.toml`（见仓库根 README）
- 桌面运行时固定配置优先写入 TOML 的 `[desktop.runtime]`，推荐放在 `config/robots/<robot>/base.toml`，因为 ROS、Robot OS、API 工作目录通常是单台机器 test/prod 共用。
- 桌面端环境变量（覆盖 TOML / 默认值）：
  - `PERCEPT_ROBOT` / `APP_ROBOT` — 机型
  - `PERCEPT_ENV` / `APP_ENV` — 环境
  - `PERCEPT_LAUNCH_MODE` — 启动方式：`bilateral`（同构臂）/ `vr`（VR）
  - `ROS_ENV_CWD` / `ROS_SETUP_SCRIPT` / `ROSCORE_CMD` / `ROS_SLAVE_CMD` / `ROS_MASTER_CMD`
  - `VR_ROS_ENABLED` / `VR_ROS_PREPARE_COUNTDOWN_SECONDS`
  - `VR_ROS_ARM_CWD` / `VR_ROS_ARM_SETUP_SCRIPT` / `VR_ROS_ARM_CMD`
  - `VR_ROS_SERIAL_CWD` / `VR_ROS_SERIAL_SETUP_SCRIPT` / `VR_ROS_SERIAL_CMD`
  - `ROBOT_OS_CWD` / `ROBOT_OS_CMD`
  - `API_CWD` / `API_CMD` / `SERVER__PORT`
  - 各类超时：`API_STARTUP_TIMEOUT` / `ROBOT_OS_READY_TIMEOUT` / `*_GRACE` 系列
- 也可以使用 Pydantic 嵌套环境变量覆盖，例如 `DESKTOP__RUNTIME__API_CWD=/tmp/percept-edge-api`。
- 进程注入的标准环境：`PYTHONUNBUFFERED=1` / `LANG=C.UTF-8` / `PERCEPT_ROBOT` / `APP_ROBOT` / `PERCEPT_ENV` / `APP_ENV` / `PERCEPT_LAUNCH_MODE`

`[desktop.runtime]` 示例：

```toml
[desktop.runtime]
uv_bin = "uv"
ros_env_cwd = "/home/ai/workspaces/ros_env/X5_ws"
ros_setup_script = "/home/ai/workspaces/ros_env/X5_ws/devel/setup.bash"
roscore_cmd = "roscore"
ros_slave_cmd = "roslaunch arx_x5_controller open_remote_slave.launch"
ros_master_cmd = "roslaunch arx_x5_controller open_remote_master.launch"
robot_os_cwd = "/home/ai/workspaces/percept-edge/ontology-core"
robot_os_cmd = "/usr/local/bin/python3.10 /home/ai/workspaces/percept-edge/ontology-core/robot_os.pyz core --run-mode mode1 --log-level INFO"
api_cwd = "/home/ai/workspaces/percept-edge/percept-edge-api"
api_cmd = "uv run main.py"
runtime_health_interval = 300.0
```

## 新增机型

1. `desktop/profiles/<name>.py`：用 `@register("robot-<name>")` 装饰工厂函数
2. 组装 adapters：复用 `RosAdapter / CanAdapter / RobotOsAdapter / ApiAdapter`，必要时新建
3. 选择 flow_factory：复用 `build_cr_flow`（用 `extra={"skip_xxx": True}` 调整）或新写 `flows/<name>_flow.py`
4. 在 `desktop/profiles/__init__.py` 的 `from desktop.profiles import ...` 中追加模块名

### Adapter 常用参数

| Adapter | 关键参数 | 说明 |
|---------|---------|------|
| `RosAdapter` | `role: "core"\|"slave"\|"master"`, `name?` | name 缺省派生自 role（roscore / ros_slave / ros_master） |
| `CanAdapter` | `name="can_init"` | 一次性 slcand 初始化 |
| `RobotOsAdapter` | `name="robot_os"`, `source_ros=True`, `ros_setup_override=None` | `source_ros=False` 跳过 ROS setup（W1 等无 ROS 机型）；`ros_setup_override` 显式指定 setup 路径，覆盖 `RuntimeConfig.ros_setup_script` |
| `ApiAdapter` | `name="api"` | HTTP gate 探测 `data.message=="Percept Edge API"` |

`RobotOsAdapter` 之所以默认要 source ROS setup：`bash -lc` 是 login shell，**不读 `~/.bashrc`**；如果你的 ROS 环境靠 `.bashrc` 注入 `PYTHONPATH`，子进程会找不到 `rospy`，即便用绝对路径的 python 解释器也不行。CR 系列 profile 的 `RobotOsAdapter()` 默认走这条路径；W1 必须传 `source_ros=False`。

## 新增启动顺序

在 `flows/` 新建文件，返回 `Sequence[Step]`。可用的 `StepKind`：

| Kind          | 行为                                            |
|---------------|-------------------------------------------------|
| `SPAWN`       | 启动长驻进程，等 `started` 信号后推进           |
| `GRACE_CHECK` | 休眠 `duration` 秒，校验指定进程仍存活          |
| `RUN_ONCE`    | 启动一次性进程，等 `exit_code == 0` 推进        |
| `WAIT_HEALTH` | 按 `HealthProbe.interval` 轮询直到 ready / 超时 |
| `SLEEP`       | 纯休眠                                          |
| `SHELL_ONCE`  | inline shell，`extra={"name","cmd","cwd"}`      |
| `GATE`        | 自定义回调 `extra["fn"](ctx) -> bool`           |

## 安装桌面快捷方式（Linux）

```bash
# 会先执行 uv sync --locked --extra desktop，首次安装 PySide6 约 200MB，请在终端中等待完成
bash desktop/install_desktop.sh --robot robot-cr4c
# 询问是否设置开机自启
```

桌面图标启动时使用 `uv run --no-sync --extra desktop`，不会再静默下载依赖；如果漏装依赖，会弹出提示要求先运行 `uv sync --locked --extra desktop`。

## 技术栈

- PySide6 (Qt 6)：QProcess / QThreadPool / QSS
- 状态机：`RuntimeStateMachine`，初始 stage 列表由 flow 推导
- 健康检查：HTTP（matchers）+ ZeroMQ monitor（`scripts/debug/wait_robot_os_ready.probe_monitor`）

## 代码检查

```bash
uv run ruff check desktop
uv run ty check desktop
uv run ruff format desktop
```
