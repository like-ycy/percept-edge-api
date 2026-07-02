#!/usr/bin/env bash

set -u
# 禁用 job control，确保后台任务的 $! 与 setsid 创建的会话/进程组 leader 对齐。
set +m

EXIT_PRECONDITION=10
EXIT_ROBOT_FAILED=11
EXIT_ROBOT_TIMEOUT=12
EXIT_API_FAILED=13
EXIT_NGINX_FAILED=14
EXIT_ROSCORE_FAILED=15
EXIT_CAN_INIT_FAILED=16

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
UV_BIN="${UV_BIN:-uv}"
ROBOT_NAME="robot-cr1"
WATCH_ROBOT_OS_LINK_SCRIPT="${REPO_ROOT}/scripts/debug/watch_robot_os_link.py"
CONDA_PYTHON="${CONDA_PYTHON:-/home/agilex/miniconda3/envs/py310/bin/python}"
CONDA_BIN="$(dirname "${CONDA_PYTHON}")"
DEFAULT_ROS_WORKSPACE_DIR="/home/agilex/workspaces/ros_env"
DEFAULT_ROBOTIC_ARM_WS="${DEFAULT_ROS_WORKSPACE_DIR}/robotic_arm_ws"
DEFAULT_ROSCORE_CWD="${DEFAULT_ROBOTIC_ARM_WS}/follow1"
DEFAULT_ROSCORE_SETUP_SCRIPT="${DEFAULT_ROSCORE_CWD}/devel/setup.bash"
DEFAULT_ROSCORE_CMD="roscore"
DEFAULT_ROS_MASTER1_CWD="${DEFAULT_ROBOTIC_ARM_WS}/master1"
DEFAULT_ROS_MASTER1_SETUP_SCRIPT="${DEFAULT_ROS_MASTER1_CWD}/devel/setup.bash"
DEFAULT_ROS_MASTER1_CMD="roslaunch arm_control L5.launch"
DEFAULT_ROS_POS_FOLLOW1_CWD="${DEFAULT_ROBOTIC_ARM_WS}/pos_follow1"
DEFAULT_ROS_POS_FOLLOW1_SETUP_SCRIPT="${DEFAULT_ROS_POS_FOLLOW1_CWD}/devel/setup.bash"
DEFAULT_ROS_POS_FOLLOW1_CMD="roslaunch arm_control L5.launch"
DEFAULT_ROS_MASTER2_CWD="${DEFAULT_ROBOTIC_ARM_WS}/master2"
DEFAULT_ROS_MASTER2_SETUP_SCRIPT="${DEFAULT_ROS_MASTER2_CWD}/devel/setup.bash"
DEFAULT_ROS_MASTER2_CMD="roslaunch arm_control L5.launch"
DEFAULT_ROS_POS_FOLLOW2_CWD="${DEFAULT_ROBOTIC_ARM_WS}/pos_follow2"
DEFAULT_ROS_POS_FOLLOW2_SETUP_SCRIPT="${DEFAULT_ROS_POS_FOLLOW2_CWD}/devel/setup.bash"
DEFAULT_ROS_POS_FOLLOW2_CMD="roslaunch arm_control L5.launch"
DEFAULT_ROBOT_OS_CWD="/home/agilex/workspaces/percept-edge/ontology-core"
DEFAULT_ROBOT_OS_CMD="${CONDA_PYTHON} /home/agilex/workspaces/percept-edge/ontology-core/robot_os.pyz runtime --mode homologous --log-level INFO"
DEFAULT_API_CMD="${UV_BIN} run main.py"
API_STARTUP_TIMEOUT="${API_STARTUP_TIMEOUT:-15}"
API_SHUTDOWN_GRACE="${API_SHUTDOWN_GRACE:-60}"
API_GATE_INTERVAL="${API_GATE_INTERVAL:-1}"
STATUS_POLL_INTERVAL="${STATUS_POLL_INTERVAL:-0.2}"
ROS_STARTUP_GRACE="${ROS_STARTUP_GRACE:-3}"
ROSCORE_STARTUP_GRACE="${ROSCORE_STARTUP_GRACE:-2}"
CAN_STARTUP_GRACE="${CAN_STARTUP_GRACE:-1}"
INT_SHUTDOWN_GRACE="${INT_SHUTDOWN_GRACE:-8}"
TERM_KILL_GRACE="${TERM_KILL_GRACE:-5}"
FORCE_KILL_GRACE="${FORCE_KILL_GRACE:-2}"
SELECTED_ENV="${PERCEPT_ENV:-${APP_ENV:-}}"
SELECTED_MODE="${PERCEPT_LAUNCH_MODE:-bilateral}"

PROBE_PID=""
API_PID=""
ROBOT_PID=""
ROBOT_WATCH_PID=""
ROSCORE_PID=""
ROS_MASTER1_PID=""
ROS_POS_FOLLOW1_PID=""
ROS_MASTER2_PID=""
ROS_POS_FOLLOW2_PID=""

log() {
  printf '[start_runtime] %s\n' "$*"
}

err() {
  printf '[start_runtime] ERROR: %s\n' "$*" >&2
}

cleanup() {
  local status=$?

  check_active_uploads "${SERVER__PORT:-8000}"

  stop_api_process "Percept Edge API" "${API_PID}"
  stop_managed_process "Robot OS Watchdog" "${ROBOT_WATCH_PID}"
  stop_managed_process "Robot OS Ready Probe" "${PROBE_PID}"
  stop_managed_process "robot_os" "${ROBOT_PID}"
  stop_managed_process "ROS pos_follow2" "${ROS_POS_FOLLOW2_PID}"
  stop_managed_process "ROS master2" "${ROS_MASTER2_PID}"
  stop_managed_process "ROS pos_follow1" "${ROS_POS_FOLLOW1_PID}"
  stop_managed_process "ROS master1" "${ROS_MASTER1_PID}"
  stop_managed_process "roscore" "${ROSCORE_PID}"

  return "${status}"
}

wait_for_pid_exit() {
  local pid="$1"
  local timeout="$2"
  local started_at=${SECONDS}

  while kill -0 "${pid}" 2>/dev/null; do
    if [ $((SECONDS - started_at)) -ge "${timeout}" ]; then
      return 1
    fi
    sleep "${STATUS_POLL_INTERVAL}"
  done

  return 0
}

signal_process_group() {
  local signal="$1"
  local pid="$2"

  kill -s "${signal}" -- "-${pid}" 2>/dev/null
}

signal_process_session() {
  local signal="$1"
  local pid="$2"
  local sid=""

  sid="$(ps -o sid= -p "${pid}" 2>/dev/null | tr -d ' ')"
  if [ -z "${sid}" ]; then
    return 1
  fi
  if [ "${sid}" != "${pid}" ]; then
    return 1
  fi

  pkill --signal "${signal}" -s "${sid}" 2>/dev/null
}

signal_process() {
  local signal="$1"
  local pid="$2"

  kill -s "${signal}" "${pid}" 2>/dev/null
}

signal_managed_process() {
  local signal="$1"
  local pid="$2"

  if signal_process_session "${signal}" "${pid}"; then
    return 0
  fi
  if signal_process_group "${signal}" "${pid}"; then
    return 0
  fi
  signal_process "${signal}" "${pid}"
}

stop_managed_process() {
  local name="$1"
  local pid="$2"

  if [ -z "${pid}" ] || ! kill -0 "${pid}" 2>/dev/null; then
    return 0
  fi

  err "正在停止 ${name}，PID=${pid}"
  signal_managed_process INT "${pid}" || true
  if wait_for_pid_exit "${pid}" "${INT_SHUTDOWN_GRACE}"; then
    wait "${pid}" 2>/dev/null || true
    return 0
  fi

  err "${name} 未在 ${INT_SHUTDOWN_GRACE}s 内响应 SIGINT，发送 SIGTERM，PID=${pid}"
  signal_managed_process TERM "${pid}" || true
  if wait_for_pid_exit "${pid}" "${TERM_KILL_GRACE}"; then
    wait "${pid}" 2>/dev/null || true
    return 0
  fi

  err "${name} 未在 ${TERM_KILL_GRACE}s 内退出，发送 SIGKILL，PID=${pid}"
  signal_managed_process KILL "${pid}" || true
  if ! wait_for_pid_exit "${pid}" "${FORCE_KILL_GRACE}"; then
    err "${name} 在 SIGKILL 后仍未退出，可能存在内核态阻塞"
  fi
  wait "${pid}" 2>/dev/null || true
}

check_active_uploads() {
  local port="${1:-8000}"
  local url="http://127.0.0.1:${port}/api/upload/active"

  local response
  response=$(curl -fsS --max-time 5 "${url}" 2>/dev/null) || return 0

  local has_active
  has_active=$(echo "${response}" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['has_active_upload'])" 2>/dev/null) || return 0

  if [ "${has_active}" = "True" ]; then
    local count
    count=$(echo "${response}" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']['records']))" 2>/dev/null) || count="?"

    err "检测到 ${count} 个上传任务正在运行"
    err "等待上传完成或设置 FORCE_STOP_WITH_UPLOAD=1 强制停止"

    if [ "${FORCE_STOP_WITH_UPLOAD}" = "1" ]; then
      err "FORCE_STOP_WITH_UPLOAD=1，强制继续停止"
      return 0
    fi

    local wait_count=0
    local max_wait=${UPLOAD_DRAIN_TIMEOUT:-120}

    while [ ${wait_count} -lt "${max_wait}" ]; do
      response=$(curl -fsS --max-time 5 "${url}" 2>/dev/null) || break
      has_active=$(echo "${response}" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['has_active_upload'])" 2>/dev/null) || break

      if [ "${has_active}" != "True" ]; then
        err "上传已完成"
        return 0
      fi

      sleep 5
      wait_count=$((wait_count + 5))
      err "等待上传完成... (${wait_count}/${max_wait}s)"
    done

    err "等待上传超时，继续停止"
  fi
}

stop_api_process() {
  local name="$1"
  local pid="$2"

  if [ -z "${pid}" ] || ! kill -0 "${pid}" 2>/dev/null; then
    return 0
  fi

  err "正在停止 ${name}，PID=${pid}"
  signal_managed_process TERM "${pid}" || true
  if wait_for_pid_exit "${pid}" "${API_SHUTDOWN_GRACE}"; then
    wait "${pid}" 2>/dev/null || true
    return 0
  fi

  err "${name} 未在 ${API_SHUTDOWN_GRACE}s 内完成 graceful shutdown，发送 SIGKILL，PID=${pid}"
  signal_managed_process KILL "${pid}" || true
  if ! wait_for_pid_exit "${pid}" "${FORCE_KILL_GRACE}"; then
    err "${name} 在 SIGKILL 后仍未退出，可能存在内核态阻塞"
  fi
  wait "${pid}" 2>/dev/null || true
}

handle_signal() {
  err "收到中断信号，退出启动流程"
  exit 130
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "缺少命令: $1"
    exit "${EXIT_PRECONDITION}"
  fi
}

require_file() {
  if [ ! -f "$1" ]; then
    err "缺少文件: $1"
    exit "${EXIT_PRECONDITION}"
  fi
}

require_dir() {
  if [ ! -d "$1" ]; then
    err "缺少目录: $1"
    exit "${EXIT_PRECONDITION}"
  fi
}

require_ros_command() {
  local setup_script="$1"
  local command_name="$2"

  if ! bash -lc "export PATH='${CONDA_BIN}':\$PATH; source '${setup_script}' && command -v '${command_name}' >/dev/null 2>&1"; then
    err "source ${setup_script} 后未找到 ${command_name} 命令"
    exit "${EXIT_PRECONDITION}"
  fi
}

usage() {
  cat <<'EOF'
Usage: ./scripts/robots/robot-cr1.sh [--env <test|prod>] [--mode <bilateral>]

Options:
  -e, --env      指定 API 使用的运行环境
  -m, --mode     启动模式: bilateral (默认, CR1 全流程)
EOF
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -e|--env)
        if [ "$#" -lt 2 ]; then
          err "--env 缺少参数"
          usage
          exit "${EXIT_PRECONDITION}"
        fi
        SELECTED_ENV="$2"
        shift 2
        ;;
      --env=*)
        SELECTED_ENV="${1#*=}"
        shift
        ;;
      -m|--mode)
        if [ "$#" -lt 2 ]; then
          err "--mode 缺少参数"
          usage
          exit "${EXIT_PRECONDITION}"
        fi
        SELECTED_MODE="$2"
        shift 2
        ;;
      --mode=*)
        SELECTED_MODE="${1#*=}"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        err "未知参数: $1"
        usage
        exit "${EXIT_PRECONDITION}"
        ;;
    esac
  done
}

probe_api_root() {
  local url="$1"

  "${UV_BIN}" run python - "${url}" <<'PY'
import json
import sys
import urllib.error
import urllib.request

url = sys.argv[1]

try:
    with urllib.request.urlopen(url, timeout=1.0) as response:
        if response.status != 200:
            raise SystemExit(1)
        payload = json.load(response)
except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
    raise SystemExit(1)

data = payload.get("data")
if payload.get("code") == 200 and isinstance(data, dict) and data.get("message") == "Percept Edge API":
    raise SystemExit(0)

raise SystemExit(1)
PY
}

wait_for_robot_ready() {
  local robot_pid="$1"
  local robot_status=0
  local probe_status=0

  (
    cd "${REPO_ROOT}" || exit "${EXIT_PRECONDITION}"
    exec setsid "${UV_BIN}" run python scripts/debug/wait_robot_os_ready.py
  ) &
  PROBE_PID=$!

  while kill -0 "${PROBE_PID}" 2>/dev/null; do
    if ! kill -0 "${robot_pid}" 2>/dev/null; then
      wait "${robot_pid}" || robot_status=$?
      err "robot_os 在 ready 前退出，退出码: ${robot_status}"
      return "${EXIT_ROBOT_FAILED}"
    fi
    sleep "${STATUS_POLL_INTERVAL}"
  done

  wait "${PROBE_PID}" || probe_status=$?
  PROBE_PID=""
  if [ "${probe_status}" -ne 0 ]; then
    err "robot_os 在限定时间内未 ready"
    return "${EXIT_ROBOT_TIMEOUT}"
  fi

  return 0
}

start_api_and_wait() {
  local robot_pid="$1"
  local api_url="http://127.0.0.1:${SERVER__PORT:-8000}/"
  local api_status=0
  local robot_status=0
  local started_at=${SECONDS}
  local api_cwd="${API_CWD:-${REPO_ROOT}}"
  local api_cmd="${API_CMD:-${DEFAULT_API_CMD}}"

  if [ ! -d "${api_cwd}" ]; then
    err "API_CWD 不存在: ${api_cwd}"
    return "${EXIT_PRECONDITION}"
  fi

  (
    cd "${api_cwd}" || exit "${EXIT_PRECONDITION}"
    export SERVER__DEBUG=false
    exec setsid bash -lc "${api_cmd}"
  ) &
  API_PID=$!
  log "Percept Edge API 启动中，PID=${API_PID}"

  while true; do
    if ! kill -0 "${robot_pid}" 2>/dev/null; then
      wait "${robot_pid}" || robot_status=$?
      err "robot_os 在 API startup gate 完成前退出，退出码: ${robot_status}"
      return "${EXIT_ROBOT_FAILED}"
    fi

    if ! kill -0 "${API_PID}" 2>/dev/null; then
      wait "${API_PID}" || api_status=$?
      err "API 在启动观察窗口内退出，退出码: ${api_status}"
      return "${EXIT_API_FAILED}"
    fi

    if probe_api_root "${api_url}"; then
      log "API startup gate 已通过: ${api_url}"
      if ! sudo -n systemctl start nginx; then
        err "nginx 启动失败"
        return "${EXIT_NGINX_FAILED}"
      fi
      log "nginx 已启动"
      if [ -f "${WATCH_ROBOT_OS_LINK_SCRIPT}" ]; then
        (
          cd "${REPO_ROOT}" || exit "${EXIT_PRECONDITION}"
          exec setsid "${UV_BIN}" run python scripts/debug/watch_robot_os_link.py
        ) &
        ROBOT_WATCH_PID=$!
        log "Robot OS Watchdog 启动中，PID=${ROBOT_WATCH_PID}"
      else
        log "未找到 Robot OS Watchdog 脚本，跳过链路监控"
      fi
      wait "${API_PID}" || api_status=$?
      API_PID=""
      return "${api_status}"
    fi

    if [ $((SECONDS - started_at)) -ge "${API_STARTUP_TIMEOUT}" ]; then
      err "API 在 ${API_STARTUP_TIMEOUT}s 内未通过 startup gate: ${api_url}"
      return "${EXIT_API_FAILED}"
    fi

    sleep "${API_GATE_INTERVAL}"
  done
}

start_ros_process() {
  local name="$1"
  local cwd="$2"
  local setup_script="$3"
  local cmd="$4"
  local pid_var="$5"
  local pid=""
  local wrapped_cmd="${cmd}"

  require_dir "${cwd}"
  require_file "${setup_script}"

  case "${cmd}" in
    roslaunch\ *)
      wrapped_cmd="${cmd} >/dev/null 2>&1"
      ;;
  esac

  (
    cd "${cwd}" || exit "${EXIT_PRECONDITION}"
    exec setsid bash -lc "export PATH='${CONDA_BIN}':\$PATH; source '${setup_script}' && exec ${wrapped_cmd}"
  ) &
  pid=$!
  printf -v "${pid_var}" '%s' "${pid}"
  log "${name} 启动中，PID=${pid}"
}

start_roscore() {
  local cwd="$1"
  local setup_script="$2"
  local cmd="$3"
  local status=0

  require_dir "${cwd}"
  require_file "${setup_script}"

  (
    cd "${cwd}" || exit "${EXIT_PRECONDITION}"
    exec setsid bash -lc "export PATH='${CONDA_BIN}':\$PATH; source '${setup_script}' && exec ${cmd} >/dev/null 2>&1"
  ) &
  ROSCORE_PID=$!
  log "roscore 启动中，PID=${ROSCORE_PID}"

  sleep "${ROSCORE_STARTUP_GRACE}"
  if ! kill -0 "${ROSCORE_PID}" 2>/dev/null; then
    wait "${ROSCORE_PID}" || status=$?
    ROSCORE_PID=""
    err "roscore 在启动后 ${ROSCORE_STARTUP_GRACE}s 内退出，退出码: ${status}"
    return "${EXIT_ROSCORE_FAILED}"
  fi

  log "roscore 已通过启动存活检查"
  return 0
}

init_can_devices() {
  local index=""

  for index in 0 1 2 3 4; do
    if [ ! -e "/dev/arxcan${index}" ]; then
      err "CAN 设备不存在: /dev/arxcan${index}"
      return "${EXIT_CAN_INIT_FAILED}"
    fi

    log "正在初始化 arxcan${index}"
    if ! sudo -n slcand -o -f -s8 "/dev/arxcan${index}" "can${index}" >/dev/null 2>&1; then
      err "slcand 初始化失败: /dev/arxcan${index}"
      return "${EXIT_CAN_INIT_FAILED}"
    fi

    if ! sudo -n ifconfig "can${index}" up >/dev/null 2>&1; then
      err "CAN 网卡启动失败: can${index}"
      return "${EXIT_CAN_INIT_FAILED}"
    fi

    sleep "${CAN_STARTUP_GRACE}"
    log "arxcan${index} 初始化完成"
  done

  return 0
}

wait_for_process_stable() {
  local name="$1"
  local pid="$2"
  local grace="$3"
  local started_at=${SECONDS}
  local status=0

  while [ $((SECONDS - started_at)) -lt "${grace}" ]; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      wait "${pid}" || status=$?
      err "${name} 在启动后 ${grace}s 内退出，退出码: ${status}"
      return "${EXIT_ROBOT_FAILED}"
    fi
    sleep "${STATUS_POLL_INTERVAL}"
  done

  log "${name} 已通过启动存活检查"
  return 0
}

main() {
  local roscore_cwd="${ROSCORE_CWD:-${DEFAULT_ROSCORE_CWD}}"
  local roscore_setup_script="${ROSCORE_SETUP_SCRIPT:-${DEFAULT_ROSCORE_SETUP_SCRIPT}}"
  local roscore_cmd="${ROSCORE_CMD:-${DEFAULT_ROSCORE_CMD}}"
  local ros_master1_cwd="${ROS_MASTER1_CWD:-${DEFAULT_ROS_MASTER1_CWD}}"
  local ros_master1_setup_script="${ROS_MASTER1_SETUP_SCRIPT:-${DEFAULT_ROS_MASTER1_SETUP_SCRIPT}}"
  local ros_master1_cmd="${ROS_MASTER1_CMD:-${DEFAULT_ROS_MASTER1_CMD}}"
  local ros_pos_follow1_cwd="${ROS_POS_FOLLOW1_CWD:-${DEFAULT_ROS_POS_FOLLOW1_CWD}}"
  local ros_pos_follow1_setup_script="${ROS_POS_FOLLOW1_SETUP_SCRIPT:-${DEFAULT_ROS_POS_FOLLOW1_SETUP_SCRIPT}}"
  local ros_pos_follow1_cmd="${ROS_POS_FOLLOW1_CMD:-${DEFAULT_ROS_POS_FOLLOW1_CMD}}"
  local ros_master2_cwd="${ROS_MASTER2_CWD:-${DEFAULT_ROS_MASTER2_CWD}}"
  local ros_master2_setup_script="${ROS_MASTER2_SETUP_SCRIPT:-${DEFAULT_ROS_MASTER2_SETUP_SCRIPT}}"
  local ros_master2_cmd="${ROS_MASTER2_CMD:-${DEFAULT_ROS_MASTER2_CMD}}"
  local ros_pos_follow2_cwd="${ROS_POS_FOLLOW2_CWD:-${DEFAULT_ROS_POS_FOLLOW2_CWD}}"
  local ros_pos_follow2_setup_script="${ROS_POS_FOLLOW2_SETUP_SCRIPT:-${DEFAULT_ROS_POS_FOLLOW2_SETUP_SCRIPT}}"
  local ros_pos_follow2_cmd="${ROS_POS_FOLLOW2_CMD:-${DEFAULT_ROS_POS_FOLLOW2_CMD}}"
  local robot_os_cwd="${ROBOT_OS_CWD:-${DEFAULT_ROBOT_OS_CWD}}"
  local robot_os_cmd="${ROBOT_OS_CMD:-${DEFAULT_ROBOT_OS_CMD}}"
  local selected_env="${SELECTED_ENV}"

  trap cleanup EXIT
  trap handle_signal INT TERM

  parse_args "$@"
  selected_env="${SELECTED_ENV}"

  if [ -n "${selected_env}" ]; then
    log "当前运行环境: ${selected_env}"
  else
    err "未指定运行环境，请通过 --env 参数或 PERCEPT_ENV 环境变量指定"
    exit "${EXIT_PRECONDITION}"
  fi

  case "${SELECTED_MODE}" in
    bilateral) ;;
    *)
      err "不支持的启动模式: ${SELECTED_MODE}，robot-cr1 仅支持 bilateral"
      exit "${EXIT_PRECONDITION}"
      ;;
  esac
  log "启动模式: ${SELECTED_MODE}"

  require_command "${UV_BIN}"
  require_command bash
  require_command setsid
  require_command pkill
  require_command ps
  require_command tr
  require_command sudo
  require_command slcand
  require_command ifconfig
  require_file "${CONDA_PYTHON}"
  case "${selected_env}" in
    test|prod)
      require_file "${REPO_ROOT}/config/robots/${ROBOT_NAME}/${selected_env}.toml"
      ;;
    *)
      err "不支持的环境: ${selected_env}，仅支持 test 或 prod"
      exit "${EXIT_PRECONDITION}"
      ;;
  esac

  require_file "${REPO_ROOT}/main.py"
  require_file "${REPO_ROOT}/scripts/debug/wait_robot_os_ready.py"
  require_file "/home/agilex/workspaces/percept-edge/ontology-core/robot_os.pyz"

  export PERCEPT_ROBOT="${ROBOT_NAME}"
  export PERCEPT_ENV="${selected_env}"
  export PERCEPT_LAUNCH_MODE="${SELECTED_MODE}"
  export PATH="${CONDA_BIN}:${PATH}"

  require_ros_command "${roscore_setup_script}" roscore
  require_ros_command "${ros_master1_setup_script}" roslaunch

  if [ ! -d "${robot_os_cwd}" ]; then
    err "ROBOT_OS_CWD 不存在: ${robot_os_cwd}"
    exit "${EXIT_PRECONDITION}"
  fi

  start_roscore "${roscore_cwd}" "${roscore_setup_script}" "${roscore_cmd}" || exit $?
  init_can_devices || exit $?

  start_ros_process "ROS master1" "${ros_master1_cwd}" "${ros_master1_setup_script}" "${ros_master1_cmd}" ROS_MASTER1_PID
  wait_for_process_stable "ROS master1" "${ROS_MASTER1_PID}" "${ROS_STARTUP_GRACE}" || exit $?

  start_ros_process "ROS pos_follow1" "${ros_pos_follow1_cwd}" "${ros_pos_follow1_setup_script}" "${ros_pos_follow1_cmd}" ROS_POS_FOLLOW1_PID
  wait_for_process_stable "ROS pos_follow1" "${ROS_POS_FOLLOW1_PID}" "${ROS_STARTUP_GRACE}" || exit $?

  start_ros_process "ROS master2" "${ros_master2_cwd}" "${ros_master2_setup_script}" "${ros_master2_cmd}" ROS_MASTER2_PID
  wait_for_process_stable "ROS master2" "${ROS_MASTER2_PID}" "${ROS_STARTUP_GRACE}" || exit $?

  start_ros_process "ROS pos_follow2" "${ros_pos_follow2_cwd}" "${ros_pos_follow2_setup_script}" "${ros_pos_follow2_cmd}" ROS_POS_FOLLOW2_PID
  wait_for_process_stable "ROS pos_follow2" "${ROS_POS_FOLLOW2_PID}" "${ROS_STARTUP_GRACE}" || exit $?

  (
    cd "${robot_os_cwd}" || exit "${EXIT_PRECONDITION}"
    exec setsid bash -lc "exec ${robot_os_cmd}"
  ) &
  ROBOT_PID=$!
  log "robot_os 启动中，PID=${ROBOT_PID}"

  wait_for_robot_ready "${ROBOT_PID}" || exit $?
  log "robot_os 已 ready"

  start_api_and_wait "${ROBOT_PID}"
}

main "$@"
