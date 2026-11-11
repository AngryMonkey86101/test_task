"""Чат с ботом прямо в терминале — без Telegram и токена.

Команды те же, что в Telegram: /start NV-1234, /demo, /day 7.
Кнопки нумеруются: чтобы нажать, введите номер.
"""
from __future__ import annotations

import re

from . import config, db
from .engine import Engine, Out

CHAT_ID = 1


def render(msgs: list[Out], buttons: list[str]) -> None:
    buttons.clear()
    for m in msgs:
        text = re.sub(r"<[^>]+>", "", m.text)
        print("\n🤖 " + text.replace("\n", "\n   "))
        if m.buttons:
            for row in m.buttons:
                for label, data in row:
                    buttons.append(data)
                    print(f"   [{len(buttons)}] {label}")
        if m.menu:
            print("   меню: план | контакты | помощь")


def main() -> None:
    conn = db.connect(config.DB_PATH)
    engine = Engine(conn)
    buttons: list[str] = []
    print("Терминальный чат с ботом «Новичок». Ctrl+C — выход. Начните с /demo или /start NV-xxxx.")
    render(engine.on_start(CHAT_ID), buttons)
    aliases = {"план": "🗓 Мой план", "контакты": "📇 Контакты", "помощь": "🆘 Нужна помощь"}
    while True:
        try:
            text = input("\n👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text.isdigit() and 0 < int(text) <= len(buttons):
            msgs = engine.on_button(CHAT_ID, buttons[int(text) - 1])
        elif text.startswith("/start"):
            msgs = engine.on_start(CHAT_ID, text[6:].strip())
        elif text == "/demo":
            msgs = engine.demo(CHAT_ID)
        elif text.startswith("/day"):
            n = re.findall(r"\d+", text)
            msgs = engine.demo_day(CHAT_ID, int(n[0]) if n else 1)
        else:
            msgs = engine.on_text(CHAT_ID, aliases.get(text.lower(), text))
        render(msgs, buttons)


if __name__ == "__main__":
    main()
