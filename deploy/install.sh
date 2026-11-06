#!/usr/bin/env bash
# 一键部署到 Linux 服务器（Ubuntu/Debian）
#   bash deploy/install.sh
# 前置：把项目目录放到 /opt/qw-attendance，或先 git clone 到该路径
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/qw-attendance}"
PY="${PY:-python3}"

echo "== 1. 时区 =="
if [ "$(timedatectl show -p Timezone --value 2>/dev/null)" != "Asia/Shanghai" ]; then
  echo "   设置 Asia/Shanghai（排程时间是本地时间，时区错会全错）"
  sudo timedatectl set-timezone Asia/Shanghai || true
fi
date

echo "== 2. 依赖 =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip ca-certificates

cd "$APP_DIR"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "   python: $(.venv/bin/python -V)"

echo "== 3. 环境自检（网络 + 登录 + 排程）=="
.venv/bin/python scripts/deploy_check.py || {
  echo "!! 自检未通过，先修好再装服务"; exit 1; }

echo "== 4. systemd =="
sudo cp deploy/qw-attendance.service /etc/systemd/system/qw-attendance.service
sudo systemctl daemon-reload
sudo systemctl enable qw-attendance
sudo systemctl restart qw-attendance
sleep 2
systemctl --no-pager --lines=15 status qw-attendance || true

echo
echo "完成。常用命令："
echo "  journalctl -u qw-attendance -f        看日志"
echo "  systemctl restart qw-attendance       重排程后重启"
echo "  .venv/bin/python scripts/show_plan.py 看接下来的签到"
