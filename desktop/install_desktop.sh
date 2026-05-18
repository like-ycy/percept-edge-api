#!/usr/bin/env bash
# 安装 Percept Edge Runtime Console 桌面快捷方式
# 在 Ubuntu ARM 目标机器上执行：bash desktop/install_desktop.sh [--robot robot-cr4c]

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROBOT_NAME="${PERCEPT_ROBOT:-robot-cr4c}"
UV_BIN="${UV_BIN:-uv}"
if [[ "${1:-}" == "--robot" && -n "${2:-}" ]]; then
    ROBOT_NAME="$2"
fi

DESKTOP_FILE="$HOME/.local/share/applications/percept-edge-console.desktop"
AUTOSTART_DIR="$HOME/.config/autostart"

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"

echo "📦 正在安装桌面运行依赖（PySide6，首次约 200MB）..."
echo "   命令: ${UV_BIN} sync --locked --extra desktop"
cd "$REPO_DIR"
"$UV_BIN" sync --locked --extra desktop

mkdir -p "$(dirname "$DESKTOP_FILE")"

cat > "$DESKTOP_FILE" <<ENTRY
[Desktop Entry]
Version=1.0
Type=Application
Name=Percept Edge Console
Name[zh_CN]=Percept Edge 运行控制台
Comment=Runtime management console for Percept Edge
Comment[zh_CN]=Percept Edge 运行时管理控制台
Exec=${REPO_DIR}/desktop/launch.sh --robot ${ROBOT_NAME}
Path=${REPO_DIR}
Icon=${REPO_DIR}/desktop/assets/icons/icon.png
Terminal=false
Categories=Development;Utility;
StartupNotify=true
ENTRY

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

echo "✅ 桌面快捷方式已安装: $DESKTOP_FILE (机型: ${ROBOT_NAME})"
echo "   可在应用菜单中搜索 'Percept Edge Console' 启动"

read -rp "是否设置开机自启? [y/N] " answer
if [[ "${answer,,}" == "y" ]]; then
    mkdir -p "$AUTOSTART_DIR"
    cp "$DESKTOP_FILE" "$AUTOSTART_DIR/percept-edge-console.desktop"
    echo "✅ 已设置开机自启"
fi
