"""Создание задач в Битрикс24.

Без вебхука задачи складываются в таблицу bitrix_outbox — это имитация, её
видно в дашборде. С переменной BITRIX_WEBHOOK_URL
(вида https://portal.bitrix24.ru/rest/1/xxxxxxxx) задачи создаются методом
tasks.task.add по-настоящему.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import httpx


class Bitrix:
    def __init__(self, conn: sqlite3.Connection, webhook_url: str = "", now=datetime.now):
        self.conn = conn
        self.webhook_url = webhook_url
        self.now = now

    def create_task(self, *, title: str, description: str, responsible_id: int, responsible: str,
                    deadline: datetime, employee_id: int | None) -> int:
        mode = "mock"
        task_id = None
        if self.webhook_url:
            try:
                r = httpx.post(f"{self.webhook_url}/tasks.task.add.json", timeout=10, json={"fields": {
                    "TITLE": title, "DESCRIPTION": description, "RESPONSIBLE_ID": responsible_id,
                    "DEADLINE": deadline.isoformat(), "TAGS": ["Новичок"],
                }})
                r.raise_for_status()
                task_id = int(r.json()["result"]["task"]["id"])
                mode = "live"
            except Exception as exc:  # задача не должна теряться, если портал недоступен
                mode = "error"
                description += f"\n\n[Не удалось отправить в Битрикс24: {exc}]"
        cur = self.conn.execute(
            """INSERT INTO bitrix_outbox (task_id, title, description, responsible_id, responsible, deadline,
                   employee_id, mode, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (task_id, title, description, responsible_id, responsible, deadline.isoformat(timespec="minutes"),
             employee_id, mode, self.now().isoformat(timespec="seconds")),
        )
        if task_id is None:
            task_id = 70000 + cur.lastrowid
            self.conn.execute("UPDATE bitrix_outbox SET task_id=? WHERE id=?", (task_id, cur.lastrowid))
        self.conn.commit()
        return task_id
