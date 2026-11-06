#!/usr/bin/env bash
# Ubuntu/Debian + systemd；先以运行服务的普通用户完成登录、排程和照片准备。
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
export TZ=Asia/Shanghai

if [[ "$APP_DIR" =~ [[:space:]%] ]]; then
  echo "安装路径不能包含空白或 %，请移动项目目录后重试" >&2
  exit 1
fi
command -v systemctl >/dev/null || { echo "需要 systemd" >&2; exit 1; }
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip ca-certificates
cd "$APP_DIR"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/deploy_check.py

# 使用当前目录与当前用户渲染服务配置，避免安装到其他路径后启动失败。
SERVICE_FILE="$(mktemp)"
trap 'rm -f "$SERVICE_FILE"' EXIT
.venv/bin/python - "$APP_DIR" "$RUN_USER" "$SERVICE_FILE" <<'PY'
import sys
from pathlib import Path
app_dir, user, output = sys.argv[1:]
text = Path('deploy/qw-attendance.service').read_text('utf-8')
text = text.replace('/opt/qw-attendance', app_dir).replace('User=root', 'User=' + user)
Path(output).write_text(text, 'utf-8')
PY
sudo install -m 644 "$SERVICE_FILE" /etc/systemd/system/qw-attendance.service
sudo systemctl daemon-reload
sudo systemctl enable qw-attendance
sudo systemctl restart qw-attendance
sudo systemctl --no-pager --lines=15 status qw-attendance
echo "查看日志：journalctl -u qw-attendance -f"
echo "重建排程后重启：sudo systemctl restart qw-attendance"
