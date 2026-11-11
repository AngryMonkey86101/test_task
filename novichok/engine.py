"""Логика бота, не зависящая от Telegram.

Движок получает события (команда /start, текст, нажатие кнопки, «тик» таймера)
и возвращает список исходящих сообщений. Telegram-адаптер (bot.py), терминальный
чат (cli_chat.py) и симуляция (simulate.py) используют один и тот же движок.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape

from . import config
from .assistant import Assistant
from .bitrix import Bitrix
from .generate_data import UNITS, fake_person
from .pulse import (CHECKPOINTS, SILENCE_DAYS, TALKING_POINTS, TOPIC_NAMES, assess, due_checkpoint)

MENU_PLAN = "🗓 Мой план"
MENU_CONTACTS = "📇 Контакты"
MENU_HELP = "🆘 Нужна помощь"
MENU = [[MENU_PLAN, MENU_CONTACTS], [MENU_HELP]]

HELP_TOPICS = {
    "schedule": "График или зарплата не как обещали",
    "manager": "Сложности с руководителем или коллегами",
    "onboarding": "Не понимаю, что делать, никто не объясняет",
    "leave": "Думаю уйти",
    "other": "Другое — хочу поговорить с HR",
}
CODE_RE = re.compile(r"\bNV-?\d{4}\b", re.IGNORECASE)
SEND_HOURS = range(10, 20)   # опросы отправляем только днём


@dataclass
class Out:
    chat_id: int
    text: str                                    # HTML-разметка Telegram
    buttons: list[list[tuple[str, str]]] | None = None
    menu: bool = False
    meta: dict = field(default_factory=dict)     # для симуляции/тестов


def _workdays_after(d: datetime, n: int) -> datetime:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d.replace(hour=18, minute=0, second=0, microsecond=0)


def first_payments(hire: date) -> list[tuple[date, str]]:
    """Даты первых выплат по правилам из базы знаний (аванс 25-го, остаток 10-го)."""
    out = []
    if hire.day <= 15:
        out.append((hire.replace(day=25), "аванс"))
    nxt = (hire.replace(day=1) + timedelta(days=32)).replace(day=10)
    out.append((nxt, "первая полная зарплата"))
    return out


class Engine:
    def __init__(self, conn: sqlite3.Connection, bitrix: Bitrix | None = None,
                 assistant: Assistant | None = None, now=datetime.now, hr_chat_ids=None):
        self.conn = conn
        self.now = now
        self.bitrix = bitrix or Bitrix(conn, config.BITRIX_WEBHOOK_URL, now=now)
        self.assistant = assistant or Assistant()
        self.hr_chat_ids = list(config.HR_CHAT_IDS if hr_chat_ids is None else hr_chat_ids)

    # ------------------------------------------------------------------ helpers
    def employee_by_chat(self, chat_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM employees WHERE tg_chat_id=? AND fire_date IS NULL", (chat_id,)).fetchone()

    def employee(self, emp_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()

    def day_of_work(self, emp: sqlite3.Row) -> int:
        return (self.now().date() - date.fromisoformat(emp["hire_date"])).days + 1

    # ------------------------------------------------------------------ entry points
    def on_start(self, chat_id: int, payload: str = "") -> list[Out]:
        if payload and CODE_RE.search(payload):
            return self._bind(chat_id, CODE_RE.search(payload).group(0))
        emp = self.employee_by_chat(chat_id)
        if emp:
            return [Out(chat_id, f"С возвращением, {escape(emp['first_name'])}! Просто напиши вопрос "
                                 "или выбери пункт меню.", menu=True)]
        return [Out(chat_id,
                    "Привет! Я помощник для новых сотрудников 👋\n\n"
                    "Пришли код из приветственного сообщения HR — он выглядит так: <code>NV-1234</code>.\n\n"
                    "Хочешь просто посмотреть, как всё работает? Нажми /demo.")]

    def on_text(self, chat_id: int, text: str) -> list[Out]:
        text = text.strip()
        emp = self.employee_by_chat(chat_id)
        if not emp:
            m = CODE_RE.search(text)
            if m:
                return self._bind(chat_id, m.group(0))
            return [Out(chat_id, "Сначала пришли код из сообщения HR (например <code>NV-1234</code>) "
                                 "или нажми /demo для демонстрации.")]
        if text == MENU_PLAN:
            return [self._plan(emp, chat_id)]
        if text == MENU_CONTACTS:
            return [self._contacts(emp, chat_id)]
        if text == MENU_HELP:
            return [Out(chat_id, "Что случилось? Выбери вариант — я сразу передам HR, тебе перезвонят сегодня. "
                                 "Руководитель это сообщение не увидит.",
                        buttons=[[(label, f"h:{key}")] for key, label in HELP_TOPICS.items()])]
        return self._ask(emp, chat_id, text)

    def on_button(self, chat_id: int, data: str) -> list[Out]:
        emp = self.employee_by_chat(chat_id)
        if data.startswith("demo:"):
            return self._demo_create(chat_id, data.split(":", 1)[1])
        if not emp:
            return self.on_start(chat_id)
        if data.startswith("a:"):
            _, sid, qi, oi = data.split(":")
            return self._answer(emp, chat_id, int(sid), int(qi), int(oi))
        if data.startswith("h:"):
            return self._help(emp, chat_id, data[2:])
        return []

    def tick(self, force_chat_id: int | None = None) -> list[Out]:
        """Плановая проверка: кому пора отправить опрос, кто молчит."""
        now = self.now()
        out: list[Out] = []
        q = "SELECT * FROM employees WHERE tg_chat_id IS NOT NULL AND fire_date IS NULL"
        params: tuple = ()
        if force_chat_id is not None:
            q += " AND tg_chat_id=?"
            params = (force_chat_id,)
        for emp in self.conn.execute(q, params).fetchall():
            out += self._check_silence(emp)
            if force_chat_id is None and now.hour not in SEND_HOURS:
                continue
            out += self._send_due_pulse(emp)
        return out

    # ------------------------------------------------------------------ demo
    def demo(self, chat_id: int) -> list[Out]:
        return [Out(chat_id, "Демо-режим: я создам тебе карточку нового сотрудника, как будто HR закрыл "
                             "задачу «Приём» в Битрикс24. Где ты «работаешь»?",
                    buttons=[[("📦 Склад", "demo:Склад"), ("🏪 Магазин", "demo:Магазин")],
                             [("💼 Отдел продаж", "demo:Отдел продаж"), ("🏢 Офис", "demo:Офис")]])]

    def _demo_create(self, chat_id: int, dept: str) -> list[Out]:
        import random

        rng = random.Random(chat_id)
        units = [u for u in UNITS if u.dept == dept] or UNITS[:1]
        unit = units[0]
        full, first, _ = fake_person(rng)
        mentor_full, _, _ = fake_person(rng)
        new_id = (self.conn.execute("SELECT MAX(id) FROM employees WHERE id >= 90000").fetchone()[0] or 89999) + 1
        self.conn.execute("UPDATE employees SET tg_chat_id=NULL WHERE tg_chat_id=?", (chat_id,))
        code = f"NV-{new_id % 10000:04d}"
        self.conn.execute(
            """INSERT INTO employees (id, full_name, first_name, position, dept, location, manager_name,
                   manager_bitrix_id, mentor_name, mentor_phone, source, hire_date, invite_code, is_demo)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (new_id, full, first, unit.positions[0], unit.dept, unit.location, unit.manager,
             unit.manager_bitrix_id, " ".join(mentor_full.split()[:2]), "+7 900 123-45-67", "Демо",
             self.now().date().isoformat(), code))
        self.conn.commit()
        out = self._bind(chat_id, code)
        out.append(Out(chat_id,
                       "🧪 <b>Как тестировать</b>\n"
                       "Чтобы не ждать 3 месяца, «перематывай» время командой <code>/day N</code>:\n"
                       "/day 3 · /day 7 · /day 14 · /day 30 · /day 60 · /day 90\n\n"
                       "Отвечай на опросы по-разному: на тревожные ответы бот создаст задачу в Битрикс24 — "
                       "в демо её копия придёт сюда же. Пиши любые вопросы: «когда зарплата?», "
                       "«как оформить возврат?», «я заболел».\n"
                       "/demo — начать заново."))
        return out

    def demo_day(self, chat_id: int, day: int) -> list[Out]:
        emp = self.employee_by_chat(chat_id)
        if not emp or not emp["is_demo"]:
            return [Out(chat_id, "Команда работает только в демо-режиме. Нажми /demo.")]
        day = max(1, min(day, 120))
        hire = self.now().date() - timedelta(days=day - 1)
        self.conn.execute("UPDATE employees SET hire_date=? WHERE id=?", (hire.isoformat(), emp["id"]))
        # «перемотка» назад во времени: забываем опросы из будущего
        for table in ("pulse_answers", "pulse_sessions"):
            self.conn.execute(f"DELETE FROM {table} WHERE employee_id=? AND checkpoint>?", (emp["id"], day))
        # в демо не ждём 2 дня молчания: незаконченные опросы считаем пропущенными
        self.conn.execute("UPDATE pulse_sessions SET status='skipped' WHERE employee_id=? AND status='sent'",
                          (emp["id"],))
        self.conn.commit()
        out = [Out(chat_id, f"⏩ Сегодня твой <b>{day}-й день</b> работы.")]
        sent = self.tick(force_chat_id=chat_id)
        if not sent:
            nxt = [d for d in CHECKPOINTS if d > day]
            out.append(Out(chat_id, "На сегодня опроса нет." +
                           (f" Следующий — на {nxt[0]}-й день: /day {nxt[0]}" if nxt else "")))
        return out + sent

    # ------------------------------------------------------------------ binding
    def _bind(self, chat_id: int, code: str) -> list[Out]:
        code = code.upper().replace("NV", "NV-").replace("--", "-")
        emp = self.conn.execute("SELECT * FROM employees WHERE invite_code=? AND fire_date IS NULL",
                                (code,)).fetchone()
        if not emp:
            return [Out(chat_id, "Не нашёл такой код 🤔 Проверь его в сообщении HR или напиши: "
                                 f"{config.HR_NAME}, {config.HR_PHONE}.")]
        self.conn.execute("UPDATE employees SET tg_chat_id=NULL WHERE tg_chat_id=?", (chat_id,))
        self.conn.execute("UPDATE employees SET tg_chat_id=? WHERE id=?", (chat_id, emp["id"]))
        self.conn.commit()
        emp = self.employee(emp["id"])
        mentor = (f"Твой наставник — <b>{escape(emp['mentor_name'])}</b>, к нему можно с любым вопросом."
                  if emp["mentor_name"] else "Наставника тебе назначат в ближайшие дни — я напомню руководителю.")
        out = [Out(chat_id,
                   f"Привет, {escape(emp['first_name'])}! Рады, что ты с нами 🎉\n\n"
                   f"Ты — {escape(emp['position'].lower())}, {escape(emp['location'])}. "
                   f"Руководитель — <b>{escape(emp['manager_name'])}</b>. {mentor}\n\n"
                   "<b>Что я умею</b>\n"
                   "❓ Отвечать на вопросы — просто напиши: «когда зарплата?», «что делать, если заболел?»\n"
                   "🗓 Показать план первых 3 месяцев\n"
                   "🆘 Передать HR, если что-то идёт не так\n\n"
                   "Иногда (6 раз за 3 месяца) я буду задавать 2 коротких вопроса о том, как дела. "
                   "Это 10 секунд, а нам поможет вовремя заметить, если тебе нужна помощь. "
                   "Ответы видит HR и не влияют на оценку испытательного срока.",
                   menu=True)]
        if not emp["mentor_name"]:
            out += self._alert(emp, "yellow", "mentor", ["mentor"], None,
                               extra="При выходе сотруднику не назначен наставник. Новички без наставника "
                                     "уходят в первые 3 месяца заметно чаще — назначьте, пожалуйста.")
        out += self.tick(force_chat_id=chat_id) if self.now().hour in SEND_HOURS or emp["is_demo"] else []
        return out

    # ------------------------------------------------------------------ pulse
    def _send_due_pulse(self, emp: sqlite3.Row) -> list[Out]:
        day = self.day_of_work(emp)
        cp = due_checkpoint(day)
        if cp is None:
            return []
        exists = self.conn.execute("SELECT 1 FROM pulse_sessions WHERE employee_id=? AND checkpoint=?",
                                   (emp["id"], cp)).fetchone()
        if exists:
            return []
        now = self.now().isoformat(timespec="seconds")
        # пропущенные точки (подключился поздно / перемотка в демо) не досылаем
        for earlier in [d for d in CHECKPOINTS if d < cp]:
            self.conn.execute("""INSERT OR IGNORE INTO pulse_sessions (employee_id, checkpoint, sent_at, status)
                                 VALUES (?,?,?, 'skipped')""", (emp["id"], earlier, now))
        cur = self.conn.execute("""INSERT INTO pulse_sessions (employee_id, checkpoint, sent_at, status)
                                   VALUES (?,?,?, 'sent')""", (emp["id"], cp, now))
        self.conn.commit()
        intro = {3: "Как первые дни?", 7: "Прошла первая неделя!", 14: "Две недели позади.",
                 30: "Уже месяц с нами!", 60: "Два месяца — экватор испытательного срока.",
                 90: "Три месяца! Испытательный срок заканчивается."}[cp]
        q = self._question_out(emp["tg_chat_id"], cur.lastrowid, cp, 0)
        q.text = f"{intro} Два коротких вопроса 👇\n\n{q.text}"
        return [q]

    def _question_out(self, chat_id: int, sid: int, cp: int, qi: int) -> Out:
        q = CHECKPOINTS[cp][qi]
        rows, row = [], []
        for oi, opt in enumerate(q.options):
            row.append((opt.label, f"a:{sid}:{qi}:{oi}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return Out(chat_id, f"<b>{escape(q.text)}</b>", buttons=rows,
                   meta={"session_id": sid, "checkpoint": cp, "question_id": q.id, "q_idx": qi})

    def _answer(self, emp, chat_id, sid, qi, oi) -> list[Out]:
        s = self.conn.execute("SELECT * FROM pulse_sessions WHERE id=? AND employee_id=?",
                              (sid, emp["id"])).fetchone()
        if not s or s["status"] not in ("sent", "silent") or s["current_q"] != qi:
            return [Out(chat_id, "Этот опрос уже закрыт, спасибо!")]
        q = CHECKPOINTS[s["checkpoint"]][qi]
        now = self.now().isoformat(timespec="seconds")
        self.conn.execute("""INSERT INTO pulse_answers (session_id, employee_id, checkpoint, question_id,
                                 option_idx, score, answered_at) VALUES (?,?,?,?,?,?,?)""",
                          (sid, emp["id"], s["checkpoint"], q.id, oi, q.options[oi].score, now))
        nq = qi + 1
        self.conn.execute("UPDATE pulse_sessions SET current_q=? WHERE id=?", (nq, sid))
        self.conn.commit()
        if nq < len(CHECKPOINTS[s["checkpoint"]]):
            return [self._question_out(chat_id, sid, s["checkpoint"], nq)]
        return self._finish(emp, chat_id, s)

    def _finish(self, emp, chat_id, s) -> list[Out]:
        cp = s["checkpoint"]
        answers = {r["question_id"]: r["option_idx"] for r in self.conn.execute(
            "SELECT question_id, option_idx FROM pulse_answers WHERE session_id=?", (s["id"],))}
        a = assess(cp, answers)
        self.conn.execute("""UPDATE pulse_sessions SET status='done', completed_at=?, score=?, level=?
                             WHERE id=?""", (self.now().isoformat(timespec="seconds"), a.score, a.level, s["id"]))
        self.conn.commit()
        out = []
        if a.level == "green":
            tips = {3: "Не стесняйся спрашивать — меня или наставника. Новичку нормально всего не знать.",
                    7: "Совет: запиши вопросы, которые копятся за неделю, и обсуди их с наставником разом.",
                    14: "Скоро встреча с руководителем по итогам двух недель — подумай, что хочешь обсудить.",
                    30: "Если что-то в расчётном листе непонятно — спроси меня «как читать расчётный лист».",
                    60: "Половина начальников смен и старших продавцов выросли внутри компании 😉",
                    90: "Поздравляем с окончанием испытательного срока! 🎉"}
            out.append(Out(chat_id, f"Спасибо! Рад, что всё хорошо 👍\n{tips[cp]}", menu=True))
        else:
            who = "HR" if a.about_manager else "твоему руководителю"
            out.append(Out(chat_id,
                           f"Спасибо за честность 🙏 Я передал это {who} — с тобой поговорят "
                           f"{'в течение дня' if a.level == 'red' else 'в ближайшие пару дней'}. Это нужно, чтобы помочь, "
                           "а не чтобы оценить тебя.\n\nЕсли нужно срочно — жми «🆘 Нужна помощь».", menu=True))
            details = [f"• {escape(q.text)} — <b>{escape(q.options[answers[q.id]].label)}</b>"
                       for q in CHECKPOINTS[cp] if q.id in answers]
            out += self._alert(emp, a.level, "pulse", a.topics, cp, details=details,
                               to_hr=a.about_manager)
        return out

    def _check_silence(self, emp) -> list[Out]:
        limit = (self.now() - timedelta(days=SILENCE_DAYS)).isoformat(timespec="seconds")
        s = self.conn.execute("""SELECT * FROM pulse_sessions WHERE employee_id=? AND status='sent'
                                 AND sent_at<=? ORDER BY checkpoint DESC LIMIT 1""", (emp["id"], limit)).fetchone()
        if not s:
            return []
        self.conn.execute("UPDATE pulse_sessions SET status='silent' WHERE id=?", (s["id"],))
        self.conn.commit()
        # одно молчание бывает у всех; сигналим, если человек молчит уже второй раз подряд
        # или это ключевые точки (первая неделя / первая зарплата)
        prev = self.conn.execute("""SELECT status FROM pulse_sessions WHERE employee_id=? AND checkpoint<?
                                    AND status!='skipped' ORDER BY checkpoint DESC LIMIT 1""",
                                 (emp["id"], s["checkpoint"])).fetchone()
        if not (s["checkpoint"] in (7, 30) or (prev and prev["status"] == "silent")):
            return []
        # не дёргаем руководителя чаще раза в 3 недели по одному и тому же молчуну
        recent = (self.now() - timedelta(days=21)).isoformat(timespec="seconds")
        if self.conn.execute("SELECT 1 FROM alerts WHERE employee_id=? AND kind='silence' AND created_at>=?",
                             (emp["id"], recent)).fetchone():
            return []
        return self._alert(emp, "yellow", "silence", ["silence"], s["checkpoint"])

    # ------------------------------------------------------------------ help / questions
    def _help(self, emp, chat_id, key) -> list[Out]:
        label = HELP_TOPICS.get(key, "Другое")
        topic = {"schedule": "promise", "manager": "manager", "onboarding": "onboarding",
                 "leave": "leave"}.get(key, "help")
        out = [Out(chat_id, f"Понял. Передал HR: «{escape(label)}». {config.HR_NAME} свяжется с тобой сегодня. "
                            f"Если очень срочно — позвони: {config.HR_PHONE}.", menu=True)]
        return out + self._alert(emp, "red", "help", ["help", topic] if topic != "help" else ["help"], None,
                                 details=[f"• Сотрудник сам нажал «Нужна помощь»: <b>{escape(label)}</b>"],
                                 to_hr=True)

    def _ask(self, emp, chat_id, text) -> list[Out]:
        ans = self.assistant.answer(text)
        self.conn.execute("""INSERT INTO kb_questions (employee_id, question, article_id, answered, asked_at)
                             VALUES (?,?,?,?,?)""", (emp["id"], text, ans.article_id, int(ans.found),
                                                     self.now().isoformat(timespec="seconds")))
        self.conn.commit()
        if ans.found:
            return [Out(chat_id, ans.text + "\n\n<i>Не то? Спроси иначе или нажми «🆘 Нужна помощь».</i>",
                        meta={"article": ans.article_id})]
        self.bitrix.create_task(
            title=f"Вопрос новичка без ответа: {emp['full_name']}",
            description=(f"Сотрудник {emp['full_name']} ({emp['position']}, {emp['location']}) спросил у бота:\n"
                         f"«{text}»\n\nВ базе знаний ответа нет. Ответьте сотруднику и, если вопрос типовой, "
                         "добавьте статью в базу знаний бота (папка kb/)."),
            responsible_id=config.HR_BITRIX_ID, responsible=config.HR_NAME,
            deadline=_workdays_after(self.now(), 1), employee_id=emp["id"])
        return [Out(chat_id, "Пока не знаю точного ответа 🤔 Передал вопрос HR — тебе ответят в течение "
                             f"рабочего дня. Если срочно: {config.HR_NAME}, {config.HR_PHONE}.",
                    meta={"article": None})]

    # ------------------------------------------------------------------ alerts
    def _alert(self, emp, level, kind, topics, checkpoint, details=None, extra="", to_hr=False) -> list[Out]:
        if to_hr:
            resp_id, resp = config.HR_BITRIX_ID, config.HR_NAME
        else:
            resp_id, resp = emp["manager_bitrix_id"], emp["manager_name"]
        day = self.day_of_work(emp)
        icon = "🔴" if level == "red" else "🟡"
        topic_names = ", ".join(TOPIC_NAMES.get(t, t) for t in dict.fromkeys(topics))
        title = f"{icon} Новичок: поговорить с {emp['full_name']} — {topic_names}"
        if kind == "mentor":
            title = f"🟡 Назначить наставника: {emp['full_name']}"
        deadline = _workdays_after(self.now(), 1 if level == "red" else 3)
        plain = lambda s: re.sub(r"<[^>]+>", "", s)
        lines = [
            f"Сотрудник: {emp['full_name']}, {emp['position']}, {emp['location']}",
            f"Вышел: {emp['hire_date']} (сегодня {day}-й день), руководитель: {emp['manager_name']}",
            f"Наставник: {emp['mentor_name'] or 'не назначен'}",
            "",
        ]
        if details:
            lines += ["Что ответил сотрудник:"] + [plain(d) for d in details] + [""]
        if extra:
            lines += [extra, ""]
        lines += ["О чём поговорить:"] + [f"— {TALKING_POINTS[t]}" for t in dict.fromkeys(topics)
                                          if t in TALKING_POINTS]
        lines += ["", "После разговора закройте задачу и коротко напишите в комментарии, о чём договорились.",
                  "Ответы сотрудника конфиденциальны: не пересылайте их коллегам."]
        task_id = self.bitrix.create_task(title=title, description="\n".join(lines), responsible_id=resp_id,
                                          responsible=resp, deadline=deadline, employee_id=emp["id"])
        self.conn.execute("""INSERT INTO alerts (employee_id, checkpoint, level, kind, topics, responsible,
                                 bitrix_task_id, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                          (emp["id"], checkpoint, level, kind, "; ".join(dict.fromkeys(topics)), resp, task_id,
                           self.now().isoformat(timespec="seconds")))
        self.conn.commit()
        card = (f"📨 <b>Задача в Битрикс24 #{task_id}</b>\n{escape(title)}\n"
                f"Ответственный: {escape(resp)} · срок {deadline:%d.%m %H:%M}")
        recipients = list(self.hr_chat_ids)
        if emp["is_demo"]:
            card = "🧪 <i>Демо: так сигнал увидит HR/руководитель</i>\n\n" + card
            recipients.append(emp["tg_chat_id"])
        return [Out(cid, card, meta={"alert": True, "task_id": task_id}) for cid in dict.fromkeys(recipients)
                if cid is not None]

    # ------------------------------------------------------------------ plan / contacts
    def _plan(self, emp, chat_id) -> Out:
        hire = date.fromisoformat(emp["hire_date"])
        today = self.now().date()
        steps = [
            (0, "Инструктаж, форма, пропуск, знакомство с наставником"),
            (2, "Первый опрос в боте «как дела»"),
            (6, "Итоги первой недели с наставником"),
            (13, "Встреча с руководителем: что получается, критерии испытательного срока"),
            (29, "Месяц работы. Обучение в Битрикс24 «Обучение» должно быть пройдено"),
            (59, "Промежуточная встреча с руководителем"),
            (89, "Конец испытательного срока, итоговая встреча"),
        ]
        items = [(hire + timedelta(days=d), t) for d, t in steps]
        items += [(d, f"💰 {name}") for d, name in first_payments(hire)]
        items.sort()
        lines = [f"🗓 <b>Твои первые 3 месяца</b> (сегодня {self.day_of_work(emp)}-й день)\n"]
        for d, t in items:
            mark = "✅" if d < today else "👉" if d == today else "▫️"
            lines.append(f"{mark} {d:%d.%m} — {escape(t)}")
        return Out(chat_id, "\n".join(lines), menu=True)

    def _contacts(self, emp, chat_id) -> Out:
        mentor = (f"{escape(emp['mentor_name'])}, {emp['mentor_phone']}" if emp["mentor_name"]
                  else "пока не назначен")
        return Out(chat_id,
                   "📇 <b>Твои контакты</b>\n"
                   f"Руководитель: {escape(emp['manager_name'])}\n"
                   f"Наставник: {mentor}\n"
                   f"HR: {config.HR_NAME}, {config.HR_PHONE}\n"
                   "Бухгалтерия: доб. 214 · IT-поддержка: доб. 300", menu=True)
