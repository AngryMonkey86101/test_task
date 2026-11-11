"""Симуляция: «прогоняем» текущих новичков через настоящего бота.

Берём сотрудников, вышедших за последние 90 дней, и день за днём (виртуальные
часы) проходим их первые недели: бот сам рассылает опросы по расписанию, а
«новички» отвечают в соответствии со скрытым профилем из генератора (кому
тяжело и почему). Также они задают типичные вопросы помощнику.

Вся логика — та же, что в Telegram: Engine, правила риска, задачи в Битрикс24.
Подставляются только ответы людей.
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, datetime, timedelta

from . import config
from .assistant import Assistant
from .bitrix import Bitrix
from .engine import Engine, Out
from .pulse import CHECKPOINTS

SIM_CHAT_BASE = 10_000_000

CAUSE_TOPICS = {
    "load": {"load", "mood"}, "schedule": {"promise", "mood"}, "salary": {"salary", "promise"},
    "manager": {"manager", "team"}, "onboarding": {"onboarding", "mentor", "clarity"},
    "other_job": {"leave", "recommend"}, "probation": {"clarity", "onboarding"}, "personal": {"mood", "leave"},
}
QUESTIONS_OK = [
    "когда зп?", "Когда будет аванс?", "что делать если заболел", "как поменяться сменой с коллегой",
    "где взять форму", "есть скидка для сотрудников?", "сколько длится испытательный срок",
    "как оформить возврат без чека", "можно ли взять отпуск в первые месяцы", "не работает касса, куда звонить",
    "нужна ли медкнижка", "где обедать на складе", "сколько кг можно поднимать", "как стать старшим продавцом",
    "как получить справку с работы", "когда придёт расчётный лист", "переработки оплачиваются?",
]
QUESTIONS_GAP = [
    ("Есть ли развозка до склада от метро?", 5), ("Можно ли оставить машину на парковке у склада?", 3),
    ("Где посмотреть мой план продаж и KPI?", 3), ("Как получить доступ к CRM для оптовых клиентов?", 2),
    ("Можно ли подрабатывать в другом магазине сети?", 2), ("Будет ли корпоратив на Новый год?", 1),
    ("Как оформить налоговый вычет через работу?", 1),
]


def _choose(rng: random.Random, q, profile: dict, day: int) -> int:
    scores = [o.score for o in q.options]
    good = [i for i, s in enumerate(scores) if s == 0 and "Ещё не" not in q.options[i].label]
    mid = [i for i, s in enumerate(scores) if s == 1]
    bad = [i for i, s in enumerate(scores) if s >= 3]
    if profile.get("struggling"):
        hit = q.topic in CAUSE_TOPICS.get(profile.get("cause"), set())
        p_bad = (0.65 if hit else 0.12) * (1.15 if day >= 14 else 1.0)
        p_mid = 0.25 if hit else 0.3
    else:
        p_bad, p_mid = 0.025, 0.11
    r = rng.random()
    if r < p_bad and bad:
        return rng.choice(bad)
    if r < p_bad + p_mid and mid:
        return rng.choice(mid)
    return rng.choice(good)


def run(conn: sqlite3.Connection, today: date, seed: int = 11) -> dict:
    rng = random.Random(seed)
    profiles = {p["tab_no"]: p for p in json.loads((config.DATA_DIR / "sim_profiles.json").read_text("utf-8"))}
    since = (today - timedelta(days=95)).isoformat()
    newcomers = conn.execute("""SELECT * FROM employees WHERE invite_code IS NOT NULL AND fire_date IS NULL
                                AND is_demo=0 AND hire_date>=? ORDER BY hire_date""", (since,)).fetchall()
    clock = {"now": datetime.combine(date.fromisoformat(newcomers[0]["hire_date"]), datetime.min.time())}
    now = lambda: clock["now"]
    engine = Engine(conn, bitrix=Bitrix(conn, "", now=now), assistant=Assistant(use_llm=False), now=now,
                    hr_chat_ids=[])

    gap_pool = [q for q, w in QUESTIONS_GAP for _ in range(w)]
    rng.shuffle(gap_pool)
    joined = 0
    plans: dict[int, dict] = {}
    for emp in newcomers:
        prof = profiles.get(emp["id"], {"struggling": False, "silent": False})
        if rng.random() < 0.08:   # не подключились к боту
            continue
        joined += 1
        asks = {rng.randint(1, 40): rng.choice(QUESTIONS_OK) for _ in range(rng.randint(0, 3))}
        if gap_pool and rng.random() < 0.45:
            asks[rng.randint(2, 30)] = gap_pool.pop()
        plans[emp["id"]] = {"profile": prof, "asks": asks, "join_day": rng.choice([1, 1, 1, 2, 3])}

    def handle(outs: list[Out], emp_by_chat: dict):
        for o in outs:
            sid = o.meta.get("session_id")
            if sid is None:
                continue
            emp_id = emp_by_chat[o.chat_id]
            plan = plans[emp_id]
            if plan["profile"].get("silent") or rng.random() < 0.07:
                continue  # не ответил
            q = CHECKPOINTS[o.meta["checkpoint"]][o.meta["q_idx"]]
            oi = _choose(rng, q, plan["profile"], o.meta["checkpoint"])
            handle(engine.on_button(o.chat_id, f"a:{sid}:{o.meta['q_idx']}:{oi}"), emp_by_chat)

    emp_by_chat: dict[int, int] = {}
    d = clock["now"].date()
    while d <= today:
        for hour in (9, 11, 15):
            clock["now"] = datetime.combine(d, datetime.min.time()).replace(hour=hour, minute=rng.randint(0, 59))
            if hour == 9:  # утром новички подключаются и задают вопросы
                for emp_id, plan in plans.items():
                    emp = engine.employee(emp_id)
                    day = (d - date.fromisoformat(emp["hire_date"])).days + 1
                    chat = SIM_CHAT_BASE + emp_id
                    if day == plan["join_day"]:
                        emp_by_chat[chat] = emp_id
                        handle(engine.on_start(chat, emp["invite_code"]), emp_by_chat)
                    if chat in emp_by_chat and day in plan["asks"]:
                        engine.on_text(chat, plan["asks"][day])
            else:
                handle(engine.tick(), emp_by_chat)
        d += timedelta(days=1)

    # «отвязываем» вымышленные чаты, чтобы настоящий бот не пытался им писать
    conn.execute("UPDATE employees SET tg_chat_id=NULL WHERE tg_chat_id>=?", (SIM_CHAT_BASE,))
    conn.commit()
    return {"newcomers": len(newcomers), "joined": joined}
