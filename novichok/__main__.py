"""Точка входа: python -m novichok <команда>

  setup      сгенерировать данные Битрикс24, загрузить, прогнать симуляцию, собрать дашборд
  dashboard  пересобрать дашборд reports/dashboard.html из текущей базы
  chat       поговорить с ботом в терминале (без Telegram)
  bot        запустить Telegram-бота (нужен TELEGRAM_BOT_TOKEN)
"""
from __future__ import annotations

import argparse
from datetime import date


def setup(today: date) -> None:
    from . import config, dashboard, db, generate_data, importer, simulate

    generate_data.main(["--today", today.isoformat()])
    conn = db.connect(config.DB_PATH)
    db.reset(conn)
    n = importer.import_export(conn, config.BITRIX_EXPORT, config.BITRIX_USERS)
    print(f"Загружено сотрудников из задач Битрикс24: {n}")
    stats = simulate.run(conn, today)
    print(f"Симуляция: новичков {stats['newcomers']}, подключились к боту {stats['joined']}")
    print(f"Дашборд: {dashboard.build(conn, today)}")
    sample = conn.execute("""SELECT full_name, invite_code FROM employees WHERE invite_code IS NOT NULL
                             AND fire_date IS NULL AND is_demo=0 ORDER BY hire_date DESC LIMIT 3""").fetchall()
    print("Коды для входа под настоящими «новичками»: " +
          ", ".join(f"{r['invite_code']} ({r['full_name']})" for r in sample))


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m novichok", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "dashboard", "chat", "bot"])
    ap.add_argument("--today", default=date.today().isoformat(), help="«сегодня» для генерации данных")
    args = ap.parse_args()

    from . import config

    if args.command == "setup" or (args.command in ("chat", "bot") and not config.DB_PATH.exists()):
        setup(date.fromisoformat(args.today))
    if args.command == "setup":
        return
    if args.command == "dashboard":
        from . import dashboard, db

        print(dashboard.build(db.connect(config.DB_PATH), date.fromisoformat(args.today)))
    elif args.command == "chat":
        from . import cli_chat

        cli_chat.main()
    elif args.command == "bot":
        from . import bot

        bot.main()


if __name__ == "__main__":
    main()
