#!/usr/bin/env bash
# Обновить бота до последней версии из git и перезапустить.  sudo bash deploy/update.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
SERVICE=novichok
APP_USER=novichok

[ "$(id -u)" -eq 0 ] || { echo "Запустите через sudo: sudo bash deploy/update.sh"; exit 1; }

git -C "$APP_DIR" -c safe.directory="$APP_DIR" pull --ff-only
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
if [ ! -f "/etc/systemd/system/$SERVICE.service" ]; then
  echo "Служба ещё не установлена — запускаю установку"; exec bash "$APP_DIR/deploy/install.sh"
fi
systemctl restart "$SERVICE"
sleep 4
journalctl -u "$SERVICE" -n 10 --no-pager
