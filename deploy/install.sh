#!/usr/bin/env bash
# Установка бота «Новичок» на VPS (Ubuntu 22.04+/Debian 12+) как службы systemd.
# Запуск из папки с проектом:  sudo bash deploy/install.sh
# Повторный запуск безопасен: обновит зависимости и перезапустит службу, .env не трогает.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE=novichok
APP_USER=novichok
TZ_NAME="${TZ_NAME:-Europe/Moscow}"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустите через sudo: sudo bash deploy/install.sh"; exit 1; }

say "Системные пакеты"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Нужен Python 3.10 или новее, на сервере: $(python3 --version)"; exit 1; }

say "Пользователь $APP_USER"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

say "Python-окружение и зависимости"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  say "Ключи (ввод скрыт, на экран не выводится)"
  read -rsp "Токен Telegram-бота от @BotFather: " TG_TOKEN; echo
  [ -n "$TG_TOKEN" ] || { echo "Токен обязателен"; exit 1; }
  read -rsp "Ключ DeepSeek (Enter — без ИИ, ответы статьями): " DS_KEY; echo
  read -rp  "chat_id сотрудников HR через запятую (Enter — пропустить): " HR_IDS
  ( umask 077
    { echo "TELEGRAM_BOT_TOKEN=$TG_TOKEN"
      echo "DEEPSEEK_API_KEY=$DS_KEY"
      if [ -n "$HR_IDS" ]; then echo "HR_CHAT_IDS=$HR_IDS"; fi
    } > "$APP_DIR/.env" )
else
  say ".env уже есть — оставляю как есть"
fi

say "Проверка доступа к Telegram и DeepSeek"
if curl -s -m 10 -o /dev/null https://api.telegram.org; then
  echo "api.telegram.org — доступен"
else
  warn "api.telegram.org недоступен с этого сервера. Бот не сможет работать без прокси:"
  warn "добавьте в $APP_DIR/.env строку TELEGRAM_PROXY=socks5://user:pass@host:port"
fi
curl -s -m 10 -o /dev/null https://api.deepseek.com && echo "api.deepseek.com — доступен" \
  || warn "api.deepseek.com недоступен — помощник будет отвечать статьями из базы знаний"

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

if [ ! -f "$APP_DIR/data/novichok.db" ]; then
  say "Демо-данные (выгрузка Битрикс24, симуляция, дашборд)"
  runuser -u "$APP_USER" -- env TZ="$TZ_NAME" "$APP_DIR/.venv/bin/python" -m novichok setup
fi

say "Служба systemd"
cat > "/etc/systemd/system/$SERVICE.service" <<UNIT
[Unit]
Description=Novichok Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
Environment=TZ=$TZ_NAME
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python -m novichok bot
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 6

say "Готово"
systemctl --no-pager --lines=0 status "$SERVICE" | head -n 4 || true
echo
journalctl -u "$SERVICE" -n 15 --no-pager || true
cat <<TXT

Бот запущен и будет перезапускаться сам (в т.ч. после перезагрузки сервера).
  Логи:        journalctl -u $SERVICE -f
  Перезапуск:  systemctl restart $SERVICE
  Обновление:  sudo bash $APP_DIR/deploy/update.sh
  Ключи:       $APP_DIR/.env  (после правки: systemctl restart $SERVICE)
TXT
