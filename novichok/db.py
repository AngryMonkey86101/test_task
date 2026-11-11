"""SQLite-хранилище."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id               INTEGER PRIMARY KEY,      -- табельный номер
    full_name        TEXT NOT NULL,
    first_name       TEXT NOT NULL,
    position         TEXT NOT NULL,
    dept             TEXT NOT NULL,            -- Склад / Магазин / Отдел продаж / Офис
    location         TEXT NOT NULL,            -- конкретная точка
    manager_name     TEXT NOT NULL,
    manager_bitrix_id INTEGER NOT NULL,
    mentor_name      TEXT,
    mentor_phone     TEXT,
    source           TEXT NOT NULL,
    hire_date        TEXT NOT NULL,
    fire_date        TEXT,
    fire_reason      TEXT,
    fire_initiator   TEXT,
    invite_code      TEXT UNIQUE,
    tg_chat_id       INTEGER,
    is_demo          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pulse_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id  INTEGER NOT NULL,
    checkpoint   INTEGER NOT NULL,
    sent_at      TEXT NOT NULL,
    current_q    INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,               -- sent / done / silent / skipped
    completed_at TEXT,
    score        INTEGER,
    level        TEXT,                        -- green / yellow / red
    UNIQUE (employee_id, checkpoint)
);
CREATE TABLE IF NOT EXISTS pulse_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,
    employee_id INTEGER NOT NULL,
    checkpoint  INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    option_idx  INTEGER NOT NULL,
    score       INTEGER NOT NULL,
    answered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id    INTEGER NOT NULL,
    checkpoint     INTEGER,
    level          TEXT NOT NULL,
    kind           TEXT NOT NULL,              -- pulse / silence / help
    topics         TEXT NOT NULL,              -- через «; »
    responsible    TEXT NOT NULL,
    bitrix_task_id INTEGER,
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bitrix_outbox (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        INTEGER,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL,
    responsible_id INTEGER NOT NULL,
    responsible    TEXT NOT NULL,
    deadline       TEXT NOT NULL,
    employee_id    INTEGER,
    mode           TEXT NOT NULL,              -- mock / live / error
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER,
    question    TEXT NOT NULL,
    article_id  TEXT,
    answered    INTEGER NOT NULL,
    asked_at    TEXT NOT NULL
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def reset(conn: sqlite3.Connection) -> None:
    for table in ("employees", "pulse_sessions", "pulse_answers", "alerts", "bitrix_outbox", "kb_questions"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
