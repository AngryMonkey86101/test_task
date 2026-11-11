"""Разбор выгрузки задач Битрикс24 (tasks.task.list) в таблицу сотрудников.

Задачи о приёме и увольнении у HR обычно заполняются текстом «Поле: значение»,
поэтому разбираем описание построчно. Сотрудник связывается между задачами
по табельному номеру.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path

FIELD_RE = re.compile(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$")


def parse_fields(description: str) -> dict[str, str]:
    fields = {}
    for line in description.splitlines():
        m = FIELD_RE.match(line)
        if m:
            fields[m.group(1).lower()] = m.group(2)
    return fields


def task_kind(task: dict) -> str | None:
    title = task.get("title", "").lower()
    tags = [t.lower() for t in task.get("tags", [])]
    if title.startswith("приём") or title.startswith("прием") or "приём" in tags:
        return "hire"
    if title.startswith("увольнение") or "увольнение" in tags:
        return "fire"
    return None


def import_export(conn: sqlite3.Connection, export_path: Path, users_path: Path, seed: int = 7) -> int:
    rng = random.Random(seed)
    export = json.loads(export_path.read_text(encoding="utf-8"))
    users = json.loads(users_path.read_text(encoding="utf-8"))
    manager_ids = {name: int(uid) for uid, name in users.items()}

    people: dict[int, dict] = {}
    for task in export["result"]["tasks"]:
        kind = task_kind(task)
        if not kind:
            continue
        f = parse_fields(task.get("description", ""))
        tab = int(f["табельный номер"])
        p = people.setdefault(tab, {"id": tab})
        p.setdefault("full_name", f.get("фио"))
        p.setdefault("position", f.get("должность"))
        p.setdefault("dept", f.get("подразделение"))
        p.setdefault("location", f.get("точка"))
        if kind == "hire":
            p["hire_date"] = f.get("дата выхода")
            p["manager_name"] = f.get("руководитель")
            mentor = f.get("наставник", "")
            p["mentor_name"] = None if mentor.lower() in ("", "не назначен", "нет") else mentor
            p["source"] = f.get("источник кандидата", "не указан")
            p["has_hire_task"] = True
        else:
            p.setdefault("hire_date", f.get("дата приёма"))
            p["fire_date"] = f.get("последний рабочий день")
            p["fire_reason"] = f.get("причина (со слов сотрудника)")
            p["fire_initiator"] = f.get("инициатор")

    # руководитель для «старичков» без задачи о приёме — по точке
    loc_manager = {p["location"]: p["manager_name"] for p in people.values() if p.get("manager_name")}

    rows = []
    used_codes: set[str] = set()
    for p in people.values():
        manager = p.get("manager_name") or loc_manager.get(p["location"], "не указан")
        code = None
        if p.get("has_hire_task") and not p.get("fire_date"):
            while True:
                code = f"NV-{rng.randint(1000, 9999)}"
                if code not in used_codes:
                    used_codes.add(code)
                    break
        rows.append((
            p["id"], p["full_name"], p["full_name"].split()[1], p["position"], p["dept"], p["location"],
            manager, manager_ids.get(manager, 3), p.get("mentor_name"),
            f"+7 9{rng.randint(10, 99)} {rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}"
            if p.get("mentor_name") else None,
            p.get("source", "до Битрикс24"), p["hire_date"], p.get("fire_date"), p.get("fire_reason"),
            p.get("fire_initiator"), code,
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO employees (id, full_name, first_name, position, dept, location, manager_name,
               manager_bitrix_id, mentor_name, mentor_phone, source, hire_date, fire_date, fire_reason,
               fire_initiator, invite_code)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)
