#!/usr/bin/env bash
# Percept Edge Runtime Console 启动脚本
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# 确保 uv 可用
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV_BIN="${UV_BIN:-uv}"

notify_missing_dependencies() {
    local message="Percept Edge Console 缺少桌面依赖。请先在项目目录运行：uv sync --locked --extra desktop"

    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Percept Edge Console" --text="$message" >/dev/null 2>&1 || true
        return
    fi
    if command -v notify-send >/dev/null 2>&1; then
        notify-send "Percept Edge Console" "$message" >/dev/null 2>&1 || true
        return
    fi

    printf '%s\n' "$message" >&2
}

if ! "$UV_BIN" run --no-sync --extra desktop python -c "import PySide6" >/dev/null 2>&1; then
    notify_missing_dependencies
    exit 1
fi

exec "$UV_BIN" run --no-sync --extra desktop python -m desktop.main "$@"
