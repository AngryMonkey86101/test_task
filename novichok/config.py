"""Настройки. Всё читается из переменных окружения (или файла .env в корне)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
KB_DIR = ROOT / "kb"
REPORTS_DIR = ROOT / "reports"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DB_PATH = Path(os.environ.get("NOVICHOK_DB", DATA_DIR / "novichok.db"))
BITRIX_EXPORT = DATA_DIR / "bitrix_export.json"
BITRIX_USERS = DATA_DIR / "bitrix_users.json"

# Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Чаты HR, куда бот дублирует сигналы риска (через запятую). В демо-режиме
# копия всегда приходит и самому тестировщику.
HR_CHAT_IDS = [int(x) for x in os.environ.get("HR_CHAT_IDS", "").split(",") if x.strip()]
# Как часто бот проверяет, кому пора отправить опрос (секунды).
TICK_SECONDS = int(os.environ.get("TICK_SECONDS", "3600"))

# Битрикс24: если указан входящий вебхук — задачи создаются по-настоящему,
# иначе пишутся в локальную «исходящую очередь» (имитация).
BITRIX_WEBHOOK_URL = os.environ.get("BITRIX_WEBHOOK_URL", "").rstrip("/")
HR_BITRIX_ID = 3
HR_NAME = "Смирнова Ольга (HR)"
HR_PHONE = "+7 900 000-00-03"

# ИИ-помощник: если есть ключ Anthropic — ответ формулирует Claude по найденным
# статьям, иначе отдаётся сама статья из базы знаний.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
USE_LLM = bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("NOVICHOK_NO_LLM") != "1"

# Сколько стоит компании один ранний уход (найм + обучение + недоработка), ₽.
# Оценка для отчёта, HR может поменять на свои цифры.
EARLY_LEAVE_COST = {
    "Склад": 45_000,
    "Магазин": 50_000,
    "Отдел продаж": 90_000,
    "Офис": 70_000,
}
