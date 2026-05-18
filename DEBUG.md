# Debug URL 速查

本文档用于快速查看 Percept Edge API 的调试接口和调试网页入口。

默认本地服务地址：`http://127.0.0.1:8000`

## 常用网页

| 名称 | URL | 说明 |
| --- | --- | --- |
| API 文档 | `http://127.0.0.1:8000/docs` | Swagger UI |
| OpenAPI JSON | `http://127.0.0.1:8000/openapi.json` | OpenAPI 原始定义 |
| ReDoc | `http://127.0.0.1:8000/redoc` | ReDoc API 文档 |
| 服务信息 | `http://127.0.0.1:8000/` | 返回服务版本、机器人和环境 |
| 健康检查 | `http://127.0.0.1:8000/health` | 云端 API 与 Robot OS 连通性检查 |

## 静态调试页面

| 名称 | URL | 文件 | 说明 |
| --- | --- | --- | --- |
| 数据采集测试 | `http://127.0.0.1:8000/static/collection.html` | `static/collection.html` | 手动测试采集流程 |
| 系统监控面板 | `http://127.0.0.1:8000/static/monitor.html` | `static/monitor.html` | 查看系统/机器人监控信息 |
| 进程监控面板 | `http://127.0.0.1:8000/static/processes.html` | `static/processes.html` | 查看主进程及子进程指标 |
| 原始采集数据查看 | `http://127.0.0.1:8000/static/raw_info.html` | `static/raw_info.html` | 查看 raw spool / 原始采集信息 |
| 采集锁解除 | `http://127.0.0.1:8000/static/unlock.html` | `static/unlock.html` | 查看或解除采集锁 |

## Debug API

`/debug` 路由仅允许本机访问，远程访问会被 `LocalOnlyMiddleware` 拒绝。

| 方法 | URL | 说明 |
| --- | --- | --- |
| `GET` | `http://127.0.0.1:8000/debug/zeromq` | 查看 ZeroMQ 消费状态 |
| `GET` | `http://127.0.0.1:8000/debug/processes` | 查看主进程及子进程指标（缓存） |
| `GET` | `http://127.0.0.1:8000/debug/processes?refresh=true` | 强制刷新进程指标 |

## 启动示例

```bash
# 本地测试环境
uv run main.py --robot robot-w1 --env test

# 或使用 uvicorn
PERCEPT_ROBOT=robot-w1 PERCEPT_ENV=test uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

## 备注

- 如果服务端口通过 `SERVER__PORT` 覆盖，请把上面的 `8000` 替换为实际端口。
- 非白名单 API 默认需要 `Authorization: Bearer <token>`。
- `/debug/*` 接口受本地访问限制，适合在部署机器本机浏览器或 SSH 端口转发后访问。
