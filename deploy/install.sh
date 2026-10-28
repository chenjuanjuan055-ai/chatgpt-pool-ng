#!/usr/bin/env bash
# 一键部署：在应用目录创建 venv、安装依赖、注册 systemd 服务。
# 用法：sudo APP_DIR=/opt/chatgpt2api bash deploy/install.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/chatgpt2api}"
PYTHON="${PYTHON:-python3}"
SERVICE_NAME="chatgpt2api"

if [[ ! -f "main.py" ]]; then
  echo "请在仓库根目录执行本脚本" >&2
  exit 1
fi

echo "[1/4] 同步代码到 ${APP_DIR}"
mkdir -p "${APP_DIR}"
if [[ "$(cd "$(dirname "$0")/.." && pwd)" != "${APP_DIR}" ]]; then
  tar -cf - --exclude='.venv' --exclude='node_modules' --exclude='data' . | tar -xf - -C "${APP_DIR}"
fi

echo "[2/4] 创建虚拟环境"
cd "${APP_DIR}"
[[ -d .venv ]] || "${PYTHON}" -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q \
  "curl-cffi>=0.15.0" "fastapi>=0.136.0" "pillow>=12.2.0" "pybase64>=1.4.3" \
  "python-multipart>=0.0.26" "tiktoken>=0.12.0" "uvicorn>=0.44.0" \
  "sqlalchemy>=2.0.0" "psycopg2-binary>=2.9.0" "gitpython>=3.1.0"

# 前端产物 web_dist 需先用 web/ 构建；版本与更新日志随产物一起提供
if [ -d web_dist ]; then
  cp -f CHANGELOG.md VERSION web_dist/ 2>/dev/null || true
fi

echo "[3/4] 安装 systemd 单元"
sed "s#/opt/chatgpt2api#${APP_DIR}#g; s#/opt/chatgpt2api/.venv#${APP_DIR}/.venv#g" \
  deploy/systemd/chatgpt2api.service > "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

echo "[4/4] 启动"
systemctl restart "${SERVICE_NAME}"
sleep 2
systemctl --no-pager --lines=5 status "${SERVICE_NAME}" || true
echo "完成：http://127.0.0.1:8000/ （默认 auth-key 见 config.json，请及时修改）"
