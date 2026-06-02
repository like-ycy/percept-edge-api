# Percept Edge API

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/like-ycy/percept-edge-api)

边缘数据采集平台后端服务，基于 `FastAPI + SQLAlchemy + ZeroMQ + WebRTC`。

提供任务同步、采集控制、存储查询、上传调度、健康检查与调试能力。

## 核心能力

- 任务管理：任务列表读取本地缓存，首次访问后由后台周期刷新，也支持手动强制同步。
- 采集控制：启动/停止/丢弃采集任务，实时查询采集状态。
- 后台整理：采集阶段先落 raw spool，停止后在后台 materialize 为最终 `mp4/json`。
- 数据通道：通过 ZeroMQ 接收传感器数据流，并提供 WebRTC 预览。
- 存储查询：按上传状态、任务、模板等维度查询采集记录。
- 上传服务：支持单条与批量上传、进度查询、上传完成通知回调。
- 健康检查：同时检查云端 API 与 Robot OS（ZeroMQ）连通性。

## 技术栈

| 技术 | 版本 |
| --- | --- |
| Python | >= 3.10 |
| FastAPI | >= 0.128.0 |
| Uvicorn | >= 0.40.0 |
| SQLAlchemy + aiosqlite | >= 2.0.46 / >= 0.22.1 |
| Pydantic + pydantic-settings | >= 2.12.5 / >= 2.12.0 |
| pyzmq | >= 27.1.0 |
| aiortc | >= 1.14.0 |
| httpx | >= 0.28.1 |
| opencv-python | >= 4.13.0 |

## 前置条件

运行本项目前，请确保以下依赖已正确安装。

### uv（Python 包管理）

本项目使用 [uv](https://docs.astral.sh/uv/) 管理 Python 环境和依赖，不使用 pip。

```bash
# 安装 uv（macOS / Linux）
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### ffmpeg / ffprobe

采集数据处理依赖 ffmpeg 和 ffprobe。

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# 验证
ffmpeg -version
ffprobe -version
```

### nginx 权限配置

如果服务由普通用户部署或维护，该用户需要加入 `sudoers`，并具备执行以下命令的权限：

- `start nginx`
- `stop nginx`
- `restart nginx`

示例配置：

```sudoers
username ALL=(ALL) NOPASSWD: /usr/bin/slcand, /usr/sbin/ifconfig, /usr/sbin/nginx, /usr/bin/systemctl start nginx, /usr/bin/systemctl stop nginx, /usr/bin/systemctl restart nginx
```

### rsync 3.4.1

上传服务要求 rsync >= 3.4.1，系统自带版本通常较旧，需手动编译安装。

另外，执行上传的用户所使用的 SSH 公钥需要预先添加到远程上传服务器的 `authorized_keys` 中，以便 `rsync` 通过 SSH 免密传输数据。

```bash
# 下载源码
wget https://download.samba.org/pub/rsync/src/rsync-3.4.1.tar.gz
tar xf rsync-3.4.1.tar.gz
cd rsync-3.4.1

# 编译安装
./configure.sh \
  --disable-debug \
  --disable-xxhash \
  --disable-zstd \
  --disable-lz4   \
  --disable-openssl \
  --disable-iconv \
  --disable-ipv6   \
  --disable-acl-support \
  --disable-xattr-support \
  --prefix=/usr/local
make -j$(nproc)
sudo make install
```

验证安装：

```bash
which rsync
```

应输出：

```
/usr/local/bin/rsync
```

**请注意：保持 /usr/local/bin/rsync 路径不变，代码中使用的也是这个，且与系统内置的 /usr/bin/rsync 区分开来**

查看版本：

```bash
rsync --version
```

### Git Submodules

项目依赖 `libs/contracts` 子模块（协议定义），首次克隆后需初始化：

```bash
git submodule update --init --recursive
```

后续拉取代码时同步子模块：

```bash
git pull --recurse-submodules
```

### libxcb-cursor0（Desktop 桌面依赖）

Desktop 桌面程序基于 PySide6，在 Linux 系统上运行时需要 `libxcb-cursor0` 库支持。

```bash
# Ubuntu / Debian
sudo apt install libxcb-cursor0
```

### 机器人上的 sqlite 数据库快速查看

可以使用 sqlitebrowser 工具

```bash
sudo add-apt-repository -y ppa:linuxgndu/sqlitebrowser
sudo apt-get update
sudo apt-get install sqlitebrowser
```

## 快速开始

### 1. 创建 Python 环境

```bash
# 安装 Python 3.14
uv python install 3.14

# 在项目根目录创建 .venv（指定 Python 3.14）
uv venv --python 3.14
```

### 2. 安装依赖

```bash
# 仅运行时依赖
uv sync

# 需要安装桌面运行控制台的机器人（会预装 PySide6，避免首次点图标时后台下载）
uv sync --locked --extra desktop

# 如果需要测试/检查代码
uv sync --group dev
```

### 3. 配置文件

日常开发和部署需显式选择机器人和运行环境：`robot + env`。

边端配置由以下文件组成：

- `config/base.toml`：公共配置
- `config/robots/<robot>/base.toml`：单台机器人 test/prod 共用配置（可选）
- `config/robots/<robot>/test.toml`：机器人测试环境差异配置
- `config/robots/<robot>/prod.toml`：机器人生产环境差异配置

当你选择机器人和环境后，程序会按以下顺序合并配置，后者覆盖前者：

```text
config/base.toml
config/robots/<robot>/base.toml
config/robots/<robot>/<env>.toml
环境变量字段覆盖
```

因此建议将全项目默认值放在 `config/base.toml`，将某台机器人 test/prod 都相同的路径、上传认证、`desktop.runtime` 放在 `config/robots/<robot>/base.toml`，将 test/prod 真正不同的数据库、云端地址、存储目录等放在对应环境文件。

最小可用配置示例：

```toml
[server]
host = "0.0.0.0"
port = 8000
debug = true

[database]
path = "data/percept.db"

[cloud]
base_url = "http://your-cloud-api"
timeout = 30

[zeromq]
endpoint = "ipc:///tmp/robotos_collection"

[webrtc]
stun_server = "stun:stun.l.google.com:19302"

[storage]
base_path = "data/collections"

[upload]
remote_user = "user"
remote_host = "upload.example.com"
remote_port = 22
remote_path = "/data/uploads"
ssh_key_path = "/path/to/id_rsa"
max_retries = 3
notify_endpoint = "/data/upload"
notify_timeout = 10
notify_retries = 3

[auth]
enabled = true
whitelist_paths = ["/", "/health", "/docs", "/openapi.json", "/redoc", "/static"]

[auth.iam]
verify_endpoint = "/auth/me"
timeout = 10

[task]
interval = 300

[monitor]
interval = 300

[heartbeat]
enabled = true
interval = 60

[collection]
frame_drop_threshold = 0.10
video_only = false

[desktop.runtime]
uv_bin = "uv"
api_cmd = "uv run main.py"
runtime_health_interval = 300.0
```

### 3.1 数据库迁移

项目启动时会自动执行 `sql/` 目录下尚未应用的手工迁移，并记录到 `schema_migrations` 表。

当前与采集链路相关的迁移包括：

- `sql/2026-04-17_add_collection_record_validation_fields.sql`
- `sql/2026-04-21_add_collection_raw_capture_fields.sql`
- `sql/2026-04-21_add_collection_lock.sql`

`[collection].frame_drop_threshold` 和 `[collection].video_only` 为**启动期生效**配置，修改后需重启服务。
`video_only = true` 仅用于虚拟机器人等纯视频采集场景，标准机器人保持 `false`。

配置选择优先级：`--robot/--env 参数 > PERCEPT_ROBOT/PERCEPT_ENV > APP_ROBOT/APP_ENV`。
⚠️ 必须显式指定机器人和运行环境，缺失任一参数时启动会报错。
在选中的配置文件基础上，环境变量仍可覆盖字段，优先级为：`初始化参数 > 环境变量字段 > config/robots/<robot>/<env>.toml > config/robots/<robot>/base.toml > config/base.toml > 默认值`。
环境变量支持双下划线嵌套，例如：

```bash
export CLOUD__BASE_URL="http://127.0.0.1:9000/api"
export AUTH__ENABLED="false"
export DESKTOP__RUNTIME__ROBOT_OS_CMD="/usr/local/bin/python3.10 /path/to/robot_os.pyz core --run-mode mode1"
```

推荐示例：

```bash
# 本地开发/联调：显式指定 robot 和 test 环境
uv run main.py --robot robot-w1 --env test

# 虚拟机器人：只接收 collection ZeroMQ 视频帧，不要求 monitor/关节数据
uv run main.py --robot robot-virtual --env test

# 生产部署：通过环境变量指定 robot + prod
PERCEPT_ROBOT=robot-cr4c PERCEPT_ENV=prod uv run uvicorn src.main:app --host 0.0.0.0 --port 8000

# 在选中的环境基础上继续覆盖单个字段
PERCEPT_ROBOT=robot-w1 PERCEPT_ENV=test CLOUD__BASE_URL="http://127.0.0.1:9000/api" uv run main.py
```

### 4. 启动服务

```bash
# 推荐：本地开发直接显式指定 robot + env
uv run main.py --robot robot-w1 --env test

# 虚拟机器人（video-only）
uv run main.py --robot robot-virtual --env test

# 或直接使用 uvicorn
PERCEPT_ROBOT=robot-w1 PERCEPT_ENV=test uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

# 生产部署时配合环境变量选择环境
PERCEPT_ROBOT=robot-cr4c PERCEPT_ENV=prod uv run uvicorn src.main:app --host 0.0.0.0 --port 8000
```

启动后访问：

- Swagger: `http://127.0.0.1:8000/docs`
- OpenAPI: `http://127.0.0.1:8000/openapi.json`
- 健康检查: `http://127.0.0.1:8000/health`

## 程序部署

本程序部署在每台机器人本机上运行，固定部署目录为：

```text
/home/${USER}/workspaces/percept-edge/
```

该目录下约定同时放置 Robot OS、测试环境 API 代码和生产环境 API 代码：

```text
.
├── ontology-core
│   └── robot_os.pyz
├── percept-edge-api-test
└── percept-edge-api
```

- `ontology-core`：Robot OS 程序目录，包含 `robot_os.pyz`。
- `percept-edge-api-test`：测试环境代码目录。
- `percept-edge-api`：生产环境代码目录。

首次部署时，在机器人上创建工作目录并克隆本项目：

```bash
mkdir -p /home/${USER}/workspaces/percept-edge
cd /home/${USER}/workspaces/percept-edge
git clone <this-repository-url> percept-edge-api
cd percept-edge-api
```

先创建 Python 虚拟环境，再安装运行依赖：

建议使用 python 3.10，为了兼容各机器人，pyside 的版本选的较低，使用较高版本的 python 会无法运行 desktop 程序

```bash
uv venv --python 3.10
uv sync
```

如果该机器人需要使用 desktop 运行控制台，则安装 desktop 额外依赖：

```bash
uv sync --extra desktop
```

依赖安装完成后，在项目根目录运行当前程序：

```bash
uv run main.py
```

## 认证与响应约定

### 认证

- 默认开启鉴权（`auth.enabled=true`）。
- 非白名单接口需携带：

```http
Authorization: Bearer <token>
```

- 本地联调可关闭鉴权（`auth.enabled=false`），系统会注入调试用户。

### 统一响应

```json
{
  "code": 200,
  "msg": "success",
  "data": {}
}
```

根路径 `/` 的返回示例：

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "message": "Percept Edge API",
    "version": "0.1.0",
    "env": "test"
  }
}
```

## API 总览

| 模块 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 根路径 | GET | `/` | 服务基本信息 |
| 健康检查 | GET | `/health` | 云端 API + ZeroMQ 状态 |
| 任务 | GET | `/api/tasks` | 任务列表（分页/过滤） |
| 任务 | GET | `/api/tasks/filters` | 任务过滤选项 |
| 采集 | POST | `/api/collection/start?task_id={id}` | 开始采集 |
| 采集 | POST | `/api/collection/stop` | 停止采集并转入后台整理 |
| 采集 | POST | `/api/collection/discard` | 丢弃采集 |
| 采集 | GET | `/api/collection/status` | 当前采集状态 |
| 采集 | GET | `/api/collection/cameras` | 摄像头列表 |
| 采集 | WS | `/api/collection/preview` | WebRTC 信令通道 |
| 存储 | GET | `/api/storage/files` | 采集记录列表（分页/过滤） |
| 存储 | GET | `/api/storage/files/filters` | 存储过滤选项 |
| 存储 | POST | `/api/storage/files/{record_id}/retry-materialize` | 重试后台整理 |
| 管理 | CLI | `uv run scripts/admin/release_collection_lock.py --robot <robot> --env <env>` | 查询/释放全局采集锁 |
| 上传 | POST | `/api/upload/start` | 单条上传，仅允许已整理并校验通过的记录 |
| 上传 | POST | `/api/upload/batch` | 批量上传，任一待上传记录未就绪会同步拒绝 |
| 上传 | GET | `/api/upload/progress/{record_id}` | 上传进度 |
| 调试 | GET | `/debug/zeromq` | ZeroMQ 消费状态与 watchdog 断流判定 |

`/debug/zeromq` 除了基础收包统计外，还会返回 watchdog 相关字段：

- `watchdog_enabled`：是否启用运行期断流 watchdog
- `is_stale`：当前是否已判定为长时间未收到数据
- `stale_seconds`：距离最后一次收到数据已经过去的秒数
- `stale_threshold_seconds`：判定为 stale 的阈值秒数

## 采集与后台整理说明

### 采集链路

当前采集流程已拆为两个阶段：

1. **实时采集阶段**：ZeroMQ 原始消息顺序写入 `output_dir/.capture/`
2. **后台整理阶段**：停止采集后异步回放 raw spool，标准机器人生成最终 `mp4/json/snapshot`

典型目录结构：

```text
output_dir/
  .capture/
    manifest.json
    segment-000001.bin
    SEALED
  episode_20260509153020_1234567890abcdef1234567890abcdef.json
  episode_20260509153020_1234567890abcdef1234567890abcdef_camera1_rgb.mp4
  writer_frame_counts.snapshot
```

最终产物文件名前缀为 `episode_{experiment_time}_{uuid_hex}`，其中
`experiment_time` 为采集开始时间（`YYYYMMDDHHMMSS`），`uuid_hex` 为去除横线的
UUID。同一条采集数据的 JSON 与所有 MP4 共享同一个前缀；后台整理重试会复用
`.capture/manifest.json` 中记录的 `filename_prefix`。

后台整理成功后，最终产物文件清单会写入 `collection_records.files`。自动上传和手动上传
都只会在 `collection_status=completed` 且 `validation_status=success` 后执行，rsync 实际同步
的是该清单中的顶层成品文件；`.capture/` 是 raw spool 中间目录，不属于上传对象。

### 虚拟机器人 video-only 模式

`robot-virtual` 是纯视频采集配置，用于录制人的操作供算法测试人手关节检测/转换。该模式在
`config/robots/robot-virtual/base.toml` 中设置 `[collection].video_only = true`。

video-only 模式约定：

- ZeroMQ collection 帧只需要相机数据（如 `camera1.data.color_data`），不要求 `joint_data`。
- 开始采集时只检查 `robotos_collection` 是否有近期数据，跳过 `robotos_command` monitor 命令和任务设备类型校验。
- WebRTC 预览仍使用现有 latest-frame 链路推流视频。
- 后台整理只写第一路 RGB 视频，最终目录只应保留一个 `*_rgb.mp4`；不会生成 episode JSON、depth mp4 或 `writer_frame_counts.snapshot`。
- `.capture/manifest.json` 会记录 `capture_mode = "video_only"`，后台整理重试和校验都会按该模式处理。
- 校验通过后保持现有自动上传流程；`robot-virtual` 的 test/prod 上传远端配置与其他机器人一致，分别使用测试/生产 NAS 目录。

示例最终目录：

```text
output_dir/
  episode_20260509153020_1234567890abcdef1234567890abcdef_camera1_rgb.mp4
```

### `/api/collection/stop` 语义

`/api/collection/stop` 成功返回表示：

- 实时采集已停止
- raw spool 已 flush 并 seal
- 后台整理任务已被调度

**不表示** 最终 `mp4/json` 已经生成完成。

### 后台整理状态

采集记录 `collection_status` 可能出现以下值：

- `collecting`：实时采集中
- `finalizing`：已停止采集，后台正在整理 raw spool
- `validating`：整理完成，正在做完整性校验
- `completed`：校验完成，可上传
- `finalize_failed`：后台整理失败，可重试
- `validation_failed`：校验失败
- `aborted`：异常中断

调用 `/api/collection/discard` 丢弃正在进行的采集时，会删除本地采集目录并删除对应数据库记录，因此不会在 `collection_records` 中保留 `discarded` 状态记录。

存储记录响应中还会返回：

- `materialize_progress`
- `materialize_error`
- `raw_bytes`
- `raw_frame_count`
- `raw_capture_dir`

### 重试后台整理

当记录处于 `finalize_failed` 或 `finalizing` 时，可调用：

```http
POST /api/storage/files/{record_id}/retry-materialize
```

用于重新调度 raw spool 的后台整理任务。

### 采集锁（Collection Lock）

当后置校验结果为 `FAILED` 时，系统会自动触发**全局采集锁**，阻止新的采集开始。

典型触发原因包括：

- 单路视频丢帧率超过 `[collection].frame_drop_threshold`
- 无法获取视频帧数（`ffprobe_error`）
- 其他导致整次采集被判定为 `FAILED` 的完整性问题

锁定期间，`/api/collection/start` 会直接拒绝请求，并返回触发原因、触发记录 ID 与时间。

### 采集锁运维操作

可通过 CLI 查询或释放全局采集锁。由于锁状态存储在机器人和环境对应的 SQLite 数据库中，运维时应显式指定 `--robot` 和 `--env`；不传时会回退读取 `PERCEPT_ROBOT/APP_ROBOT` 与 `PERCEPT_ENV/APP_ENV` 环境变量。

```bash
# 仅查看当前锁状态
uv run scripts/admin/release_collection_lock.py --robot robot-w1 --env test --status

# 强制解锁（写入操作者与备注）
uv run scripts/admin/release_collection_lock.py --robot robot-w1 --env test --operator wang --note "camera fixed" --force
```

CLI 退出码约定：

- `0`：成功
- `1`：当前未锁定
- `2`：用户取消
- `3`：解锁失败

### 异常中断保护

- 应用启动时会恢复待整理任务，并重新调度 `finalizing/finalize_failed` 的记录
- 若上一次采集目录中存在 **未 seal 的 `.capture`**，新的采集会自动避让到新目录（如追加 `_retry1` 后缀），避免复用损坏的临时文件

## 开发与测试

```bash
# 运行全部测试
uv run pytest

# 运行单个测试文件
uv run pytest tests/test_debug.py -v

# 按关键字运行
uv run pytest -k "upload"

# 代码检查
uv run ruff check .
```

## 常用脚本

```bash
# 插入一条测试任务（task_id=1）
uv run scripts/insert_task.py

# 重建并填充测试数据
uv run scripts/seed_test_data.py
```

`scripts/validate_frame.py` 可用于校验视频帧数与元数据一致性。

## 项目结构

```text
percept-edge-api/
├── config/                    # base + robots/<robot>/base + robots/<robot>/<env> 分层配置
├── desktop/                   # 桌面运行控制台（按机器人型号分目录，详见 desktop/README.md）
├── docs/
│   └── plans/                 # 设计文档
├── scripts/                   # 统一入口与机器人启动脚本
├── src/
│   ├── api/                   # 路由层
│   ├── core/                  # 异常/日志/中间件/路径安全/任务管理
│   ├── models/                # 数据模型
│   ├── repositories/          # 数据访问层
│   ├── schemas/               # 请求/响应模型
│   ├── services/              # 业务层
│   ├── config.py              # 配置加载与优先级
│   ├── dependencies.py        # 依赖注入
│   └── main.py                # FastAPI 应用入口
├── tests/                     # 测试
├── main.py                    # 启动脚本
└── pyproject.toml             # 项目与依赖配置
```

## 说明

- 应用启动时会自动：
  - 将中断中的 `collecting` 记录标记为 `aborted`
  - 恢复 `finalizing / finalize_failed` 的后台整理任务（启动恢复链路不会自动上传）
  - 恢复 `validating` 的完整性校验任务（启动恢复链路不会自动上传）
  - 初始化 `collection_lock` 单行表，保证全局采集锁状态可读写
- 预览链路与保真采集链路已解耦：预览继续采用 latest-only 语义，允许丢帧。
