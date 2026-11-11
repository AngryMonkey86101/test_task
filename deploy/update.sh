#!/usr/bin/env bash
# Обновить бота до последней версии из git и перезапустить.  sudo bash deploy/update.sh [ветка]
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
SERVICE=novichok
APP_USER=novichok

[ "$(id -u)" -eq 0 ] || { echo "Запустите через sudo: sudo bash deploy/update.sh"; exit 1; }

GIT=(git -C "$APP_DIR" -c safe.directory="$APP_DIR")
# Эти файлы бот пересоздаёт сам (демо-выгрузка и дашборд), а в git лежат их образцы.
# Локальные версии сбрасываем, иначе git откажется обновляться. База (data/novichok.db) и .env не трогаются.
"${GIT[@]}" checkout -- data/bitrix_export.json data/bitrix_users.json data/sim_profiles.json reports/ 2>/dev/null || true
BRANCH="${1:-}"   # sudo bash deploy/update.sh main — переключиться на ветку main
if [ -n "$BRANCH" ]; then
  "${GIT[@]}" fetch origin "$BRANCH"
  "${GIT[@]}" checkout "$BRANCH"
fi
"${GIT[@]}" pull --ff-only
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
if [ ! -f "/etc/systemd/system/$SERVICE.service" ]; then
  echo "Служба ещё не установлена — запускаю установку"; exec bash "$APP_DIR/deploy/install.sh"
fi
systemctl restart "$SERVICE"
sleep 4
journalctl -u "$SERVICE" -n 10 --no-pager
