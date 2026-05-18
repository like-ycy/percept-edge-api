#!/usr/bin/env bash

set -u

EXIT_PRECONDITION=10

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOTS_DIR="${SCRIPT_DIR}/robots"

SELECTED_ENV="${PERCEPT_ENV:-${APP_ENV:-}}"
SELECTED_ROBOT="${PERCEPT_ROBOT:-${APP_ROBOT:-}}"
SELECTED_MODE="${PERCEPT_LAUNCH_MODE:-}"

err() {
  printf '[start] ERROR: %s\n' "$*" >&2
}

check_disk_usage() {
  local usage
  usage=$(df / --output=pcent | tail -1 | tr -d ' %')
  if [ "${usage}" -ge 90 ] 2>/dev/null; then
    printf '\033[31m[start] WARNING: 磁盘使用率达到 %s%%，请及时清理磁盘\033[0m\n' "${usage}" >&2
  fi
}

usage() {
  cat <<'EOF'
Usage: ./scripts/start.sh --robot <robot-id> --env <test|prod> [--mode <bilateral|vr>]

Options:
  -r, --robot    指定机器人标识，例如 robot-w1 / robot-cr4c
  -e, --env      指定运行环境，仅支持 test / prod
  -m, --mode     启动模式: bilateral (同构臂全流程) / vr (仅 robot_os + api)
                 默认值按机器人决定: robot-w1=vr, CR 系列=bilateral
  -h, --help     显示帮助
EOF
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -r|--robot)
        if [ "$#" -lt 2 ]; then
          err "--robot 缺少参数"
          usage
          exit "${EXIT_PRECONDITION}"
        fi
        SELECTED_ROBOT="$2"
        shift 2
        ;;
      --robot=*)
        SELECTED_ROBOT="${1#*=}"
        shift
        ;;
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

main() {
  local robot_script=""
  local mode_args=()

  parse_args "$@"

  if [ -z "${SELECTED_ROBOT}" ]; then
    err "未指定机器人，请通过 --robot 或 PERCEPT_ROBOT/APP_ROBOT 指定"
    exit "${EXIT_PRECONDITION}"
  fi

  if [ -z "${SELECTED_ENV}" ]; then
    err "未指定运行环境，请通过 --env 或 PERCEPT_ENV/APP_ENV 指定"
    exit "${EXIT_PRECONDITION}"
  fi

  robot_script="${ROBOTS_DIR}/${SELECTED_ROBOT}.sh"
  if [ ! -f "${robot_script}" ]; then
    err "未找到机器人启动脚本: ${robot_script}"
    exit "${EXIT_PRECONDITION}"
  fi

  case "${SELECTED_ROBOT}" in
    robot-w1)
      SELECTED_MODE="${SELECTED_MODE:-vr}"
      case "${SELECTED_MODE}" in
        vr) ;;
        *)
          err "不支持的启动模式: ${SELECTED_MODE}，${SELECTED_ROBOT} 仅支持 vr"
          exit "${EXIT_PRECONDITION}"
          ;;
      esac
      mode_args=(--mode "${SELECTED_MODE}")
      ;;
    robot-cr1)
      SELECTED_MODE="${SELECTED_MODE:-bilateral}"
      case "${SELECTED_MODE}" in
        bilateral) ;;
        *)
          err "不支持的启动模式: ${SELECTED_MODE}，${SELECTED_ROBOT} 没有 VR 采集，仅支持 bilateral"
          exit "${EXIT_PRECONDITION}"
          ;;
      esac
      mode_args=()
      ;;
    robot-cr4a|robot-cr4c)
      SELECTED_MODE="${SELECTED_MODE:-bilateral}"
      case "${SELECTED_MODE}" in
        bilateral|vr) ;;
        *)
          err "不支持的启动模式: ${SELECTED_MODE}，${SELECTED_ROBOT} 仅支持 bilateral 或 vr"
          exit "${EXIT_PRECONDITION}"
          ;;
      esac
      mode_args=(--mode "${SELECTED_MODE}")
      ;;
    *)
      SELECTED_MODE="${SELECTED_MODE:-bilateral}"
      case "${SELECTED_MODE}" in
        bilateral|vr) ;;
        *)
          err "不支持的启动模式: ${SELECTED_MODE}，仅支持 bilateral 或 vr"
          exit "${EXIT_PRECONDITION}"
          ;;
      esac
      mode_args=(--mode "${SELECTED_MODE}")
      ;;
  esac

  export PERCEPT_ROBOT="${SELECTED_ROBOT}"
  export PERCEPT_ENV="${SELECTED_ENV}"
  export PERCEPT_LAUNCH_MODE="${SELECTED_MODE}"

  check_disk_usage

  if [ "${#mode_args[@]}" -gt 0 ]; then
    exec bash "${robot_script}" --env "${SELECTED_ENV}" "${mode_args[@]}"
  fi

  exec bash "${robot_script}" --env "${SELECTED_ENV}"
}

main "$@"
