"""Расчёт показателей для дашборда HR."""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import config

EARLY_DAYS = 90


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _tenure(r, today: date) -> int:
    end = _d(r["fire_date"]) or today
    return (end - _d(r["hire_date"])).days


def compute(conn: sqlite3.Connection, today: date) -> dict:
    rows = conn.execute("SELECT * FROM employees WHERE is_demo=0").fetchall()
    y_ago = today - timedelta(days=365)
    hires = [r for r in rows if r["source"] != "до Битрикс24"]
    leavers_12m = [r for r in rows if r["fire_date"] and _d(r["fire_date"]) >= y_ago]
    early_12m = [r for r in leavers_12m if _tenure(r, today) < EARLY_DAYS]
    hires_12m = [r for r in hires if _d(r["hire_date"]) >= y_ago]

    # когорта, по которой уже известен исход первых 90 дней
    cohort = [r for r in hires if (today - _d(r["hire_date"])).days >= EARLY_DAYS]
    is_early = lambda r: bool(r["fire_date"]) and _tenure(r, today) < EARLY_DAYS

    def rate_by(key) -> list[dict]:
        groups = defaultdict(list)
        for r in cohort:
            groups[key(r)].append(r)
        out = [{"name": k, "n": len(v), "early": sum(map(is_early, v)),
                "rate": sum(map(is_early, v)) / len(v)} for k, v in groups.items() if len(v) >= 5]
        return sorted(out, key=lambda x: -x["rate"])

    base_rate = sum(map(is_early, cohort)) / len(cohort) if cohort else 0

    # кривая «доживаемости» (Каплан — Майер) по подразделениям, 0–180 дней
    survival = {}
    for dept in ("Склад", "Магазин", "Отдел продаж", "Офис"):
        group = [r for r in hires if r["dept"] == dept]
        events = Counter(_tenure(r, today) for r in group if r["fire_date"])
        at_risk = len(group)
        exits_by_day = Counter(min(_tenure(r, today), 999) for r in group)
        s, curve = 1.0, []
        for day in range(0, 181):
            if at_risk > 0 and events.get(day):
                s *= 1 - events[day] / at_risk
            curve.append(round(s * 100, 1))
            at_risk -= exits_by_day.get(day, 0)
        survival[dept] = {"n": len(group), "curve": curve}

    # когда уходят: неделя работы
    early_all = [r for r in hires if is_early(r)]
    by_week = Counter(min(_tenure(r, today) // 7 + 1, 13) for r in early_all)
    weeks = [{"week": w, "n": by_week.get(w, 0)} for w in range(1, 14)]

    reasons_early = Counter(r["fire_reason"] for r in early_all).most_common()
    reasons_late = Counter(r["fire_reason"] for r in rows
                           if r["fire_date"] and not is_early(r) and _d(r["fire_date"]) >= y_ago).most_common(5)

    cost_12m = sum(config.EARLY_LEAVE_COST.get(r["dept"], 50_000) for r in early_12m)

    # ---- радар текущих новичков
    radar = []
    since = (today - timedelta(days=95)).isoformat()
    recent = (today - timedelta(days=21)).isoformat()
    for r in conn.execute("""SELECT * FROM employees WHERE invite_code IS NOT NULL AND fire_date IS NULL
                             AND hire_date>=? ORDER BY hire_date DESC""", (since,)):
        sessions = conn.execute("SELECT * FROM pulse_sessions WHERE employee_id=? ORDER BY checkpoint",
                                (r["id"],)).fetchall()
        alerts = conn.execute("SELECT * FROM alerts WHERE employee_id=? ORDER BY created_at DESC",
                              (r["id"],)).fetchall()
        joined = bool(sessions) or r["tg_chat_id"] is not None or bool(
            conn.execute("SELECT 1 FROM kb_questions WHERE employee_id=?", (r["id"],)).fetchone())
        # статус = состояние на последней пройденной контрольной точке
        # (+ просьба о помощи за последние 3 недели всегда «красная»)
        last = next((x for x in reversed(sessions) if x["status"] in ("done", "silent")), None)
        help_recent = any(a["kind"] == "help" and a["created_at"] >= recent for a in alerts)
        if not joined:
            status = "none"
        elif help_recent or (last and last["level"] == "red"):
            status = "red"
        elif last and (last["level"] == "yellow" or last["status"] == "silent"):
            status = "yellow"
        else:
            status = "green"
        trail = [{"cp": s["checkpoint"], "level": s["level"] or s["status"]} for s in sessions
                 if s["status"] != "skipped"]
        radar.append({
            "name": r["full_name"], "position": r["position"], "location": r["location"],
            "manager": r["manager_name"], "mentor": r["mentor_name"], "is_demo": r["is_demo"],
            "day": (today - _d(r["hire_date"])).days + 1, "status": status, "trail": trail,
            "last_alert": dict(alerts[0]) if alerts else None,
        })
    order = {"red": 0, "yellow": 1, "none": 2, "green": 3}
    radar.sort(key=lambda x: (order[x["status"]], -x["day"]))

    sessions = conn.execute("SELECT status FROM pulse_sessions WHERE status IN ('done','silent','sent')").fetchall()
    done = sum(s["status"] == "done" for s in sessions)
    topic_counts = Counter(t for a in conn.execute("SELECT topics FROM alerts WHERE kind='pulse' OR kind='help'")
                           for t in a["topics"].split("; ") if t)

    tasks = [dict(t) for t in conn.execute("SELECT * FROM bitrix_outbox ORDER BY id DESC LIMIT 40")]

    kb = conn.execute("SELECT * FROM kb_questions").fetchall()
    titles = {}
    for p in config.KB_DIR.glob("*.md"):
        titles[p.stem] = p.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
    kb_top = Counter(titles.get(q["article_id"], q["article_id"]) for q in kb if q["answered"]).most_common(8)
    kb_gaps = Counter(q["question"].strip().rstrip("?").capitalize() for q in kb if not q["answered"]).most_common()

    return {
        "today": today.isoformat(),
        "kpi": {
            "hires_12m": len(hires_12m), "leavers_12m": len(leavers_12m), "early_12m": len(early_12m),
            "early_share": len(early_12m) / len(leavers_12m) if leavers_12m else 0,
            "base_rate": base_rate, "cohort_n": len(cohort), "cost_12m": cost_12m,
        },
        "by_dept": rate_by(lambda r: r["dept"]),
        "by_manager": rate_by(lambda r: f"{r['manager_name']} · {r['location']}"),
        "by_source": rate_by(lambda r: r["source"]),
        "by_mentor": rate_by(lambda r: "С наставником" if r["mentor_name"] else "Без наставника"),
        "survival": survival,
        "weeks": weeks,
        "reasons_early": reasons_early,
        "reasons_late": reasons_late,
        "radar": radar,
        "pulse": {"done": done, "total": len(sessions), "topics": topic_counts.most_common()},
        "tasks": tasks,
        "kb": {"total": len(kb), "answered": sum(q["answered"] for q in kb), "top": kb_top, "gaps": kb_gaps},
        "costs": config.EARLY_LEAVE_COST,
    }
