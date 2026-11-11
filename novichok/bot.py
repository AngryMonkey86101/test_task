"""Telegram-бот: тонкий адаптер над Engine.

Запуск: TELEGRAM_BOT_TOKEN=... python -m novichok bot
"""
from __future__ import annotations

import asyncio
import logging

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

from . import config, db
from .engine import MENU, Engine, Out

log = logging.getLogger("novichok.bot")
MENU_MARKUP = ReplyKeyboardMarkup([[KeyboardButton(t) for t in row] for row in MENU], resize_keyboard=True)


async def send(app: Application, msgs: list[Out]) -> None:
    for m in msgs:
        markup = None
        if m.buttons:
            markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row]
                                           for row in m.buttons])
        elif m.menu:
            markup = MENU_MARKUP
        try:
            await app.bot.send_message(m.chat_id, m.text, parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception:  # один недоступный чат не должен ронять рассылку
            log.exception("Не удалось отправить сообщение в чат %s", m.chat_id)


def build_app(token: str) -> Application:
    conn = db.connect(config.DB_PATH)
    engine = Engine(conn)
    app = Application.builder().token(token).build()

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await send(ctx.application, engine.on_start(update.effective_chat.id, " ".join(ctx.args or [])))

    async def demo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await send(ctx.application, engine.demo(update.effective_chat.id))

    async def day(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        n = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 1
        await send(ctx.application, engine.demo_day(update.effective_chat.id, n))

    async def myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"chat_id: {update.effective_chat.id} — его можно указать в HR_CHAT_IDS")

    async def text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        # ответ ИИ может занять пару секунд — не блокируем остальных пользователей
        msgs = await asyncio.to_thread(engine.on_text, update.effective_chat.id, update.message.text)
        await send(ctx.application, msgs)

    async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        try:  # убираем кнопки у отвеченного вопроса, чтобы не нажимали дважды
            await q.edit_message_reply_markup(None)
        except Exception:
            pass
        await send(ctx.application, engine.on_button(update.effective_chat.id, q.data))

    async def tick(ctx: ContextTypes.DEFAULT_TYPE):
        await send(ctx.application, engine.tick())

    async def post_init(application: Application):
        await application.bot.set_my_commands([
            BotCommand("start", "Начать / ввести код"),
            BotCommand("demo", "Демо-режим для проверки"),
            BotCommand("day", "Демо: перемотать на N-й день, например /day 7"),
        ])

    app.post_init = post_init
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("demo", demo))
    app.add_handler(CommandHandler("day", day))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    app.job_queue.run_repeating(tick, interval=config.TICK_SECONDS, first=10)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not config.TELEGRAM_TOKEN:
        raise SystemExit("Нужен TELEGRAM_BOT_TOKEN (получить у @BotFather). Без Telegram можно попробовать "
                         "бота в терминале: python -m novichok chat")
    log.info("ИИ-помощник: %s", {"deepseek": f"DeepSeek ({config.DEEPSEEK_MODEL})",
                                 "claude": f"Claude ({config.ANTHROPIC_MODEL})"}.get(
        config.LLM_PROVIDER, "выключен — ответы статьями из базы знаний"))
    log.info("Битрикс24: %s", "вебхук" if config.BITRIX_WEBHOOK_URL else "имитация (таблица bitrix_outbox)")
    build_app(config.TELEGRAM_TOKEN).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
