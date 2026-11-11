"""Генератор правдоподобной выгрузки задач «Приём» / «Увольнение» из Битрикс24.

Все люди вымышленные: ФИО собираются случайно из частых имён и фамилий.
В данные специально «зашиты» закономерности, которые встречаются в рознице и
на складах: у одного руководителя новички уходят заметно чаще, без наставника
уходят чаще, через кадровое агентство и Авито — чаще, по рекомендации — реже,
пики ухода в первые две недели и после первой зарплаты.

Кроме выгрузки пишется data/sim_profiles.json — «скрытая правда» о текущих
новичках (кому будет тяжело), которой пользуется только симуляция опросов.
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import config

MALE_FIRST = ["Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Артём", "Илья", "Кирилл",
              "Михаил", "Никита", "Матвей", "Роман", "Егор", "Арсений", "Иван", "Денис", "Евгений",
              "Тимофей", "Владислав", "Игорь", "Олег", "Павел", "Руслан", "Виктор"]
FEMALE_FIRST = ["Анастасия", "Мария", "Анна", "Виктория", "Екатерина", "Наталья", "Марина", "Полина",
                "Дарья", "Алина", "Ксения", "Ольга", "Елена", "Татьяна", "Юлия", "Ирина", "Светлана",
                "Валерия", "Вероника", "Кристина"]
PATRONYMIC_BASE = ["Александров", "Дмитриев", "Сергеев", "Андреев", "Алексеев", "Игорев", "Михайлов",
                   "Олегов", "Николаев", "Викторов", "Евгеньев", "Павлов", "Владимиров", "Юрьев"]
SURNAMES = ["Смирнов", "Иванов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов",
            "Новиков", "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров",
            "Павлов", "Козлов", "Степанов", "Николаев", "Орлов", "Андреев", "Макаров", "Никитин",
            "Захаров", "Зайцев", "Соловьёв", "Борисов", "Яковлев", "Григорьев", "Романов", "Воробьёв",
            "Сергеев", "Кузьмин", "Фролов", "Александров", "Дмитриев", "Королёв", "Гусев", "Киселёв",
            "Ильин", "Максимов", "Поляков", "Сорокин", "Виноградов", "Ковалёв", "Белов", "Медведев",
            "Антонов", "Тарасов", "Жуков", "Баранов", "Филиппов", "Комаров", "Давыдов", "Беляев"]


@dataclass(frozen=True)
class Unit:
    dept: str
    location: str
    manager: str
    manager_bitrix_id: int
    manager_risk: float      # множитель риска раннего ухода (скрытый)
    hire_share: float
    positions: tuple[str, ...]


UNITS = [
    Unit("Склад", "Склад, смена А", "Гусев Алексей", 21, 2.0, 0.22,
         ("Кладовщик", "Комплектовщик", "Грузчик", "Водитель погрузчика")),
    Unit("Склад", "Склад, смена Б", "Орлова Татьяна", 22, 1.0, 0.18,
         ("Кладовщик", "Комплектовщик", "Грузчик", "Водитель погрузчика")),
    Unit("Магазин", "Магазин «Центральный»", "Белова Марина", 31, 1.0, 0.13,
         ("Продавец-консультант", "Кассир", "Старший продавец")),
    Unit("Магазин", "Магазин «Южный»", "Кравцов Денис", 32, 1.5, 0.12,
         ("Продавец-консультант", "Кассир", "Старший продавец")),
    Unit("Магазин", "Магазин «Северный»", "Никитина Елена", 33, 0.9, 0.10,
         ("Продавец-консультант", "Кассир", "Старший продавец")),
    Unit("Отдел продаж", "Отдел оптовых продаж", "Соколов Павел", 41, 1.1, 0.15,
         ("Менеджер по оптовым продажам", "Менеджер по работе с клиентами")),
    Unit("Офис", "Офис", "Зайцева Ирина", 51, 0.8, 0.10,
         ("Бухгалтер", "Специалист по закупкам", "Контент-менеджер", "Логист")),
]

DEPT_BASE_EARLY = {"Склад": 0.22, "Магазин": 0.17, "Отдел продаж": 0.16, "Офис": 0.07}
SOURCES = {  # доля найма, множитель риска
    "hh.ru": (0.33, 1.0),
    "Авито Работа": (0.30, 1.25),
    "Рекомендация сотрудника": (0.14, 0.45),
    "Объявление в магазине": (0.10, 0.9),
    "Кадровое агентство": (0.13, 1.5),
}
NO_MENTOR_SHARE = 0.27
NO_MENTOR_RISK = 1.45

EARLY_REASONS = {
    "load": "Тяжёлая работа, не ожидал(а) такой нагрузки",
    "schedule": "График не совпал с обещанным на собеседовании",
    "salary": "Зарплата ниже, чем ожидал(а)",
    "manager": "Не сложилось с руководителем",
    "onboarding": "Не объяснили обязанности, некому было помочь",
    "other_job": "Нашёл(ла) другую работу",
    "probation": "Не прошёл(ла) испытательный срок",
    "personal": "Личные обстоятельства",
}
LATE_REASONS = [
    ("Нашёл(ла) работу с большей зарплатой", 30), ("Нет карьерного роста", 14), ("Переезд", 10),
    ("Семейные обстоятельства", 12), ("Учёба", 7), ("Выход на пенсию", 5),
    ("Не сложилось с руководителем", 7), ("Устал(а) от графика", 10), ("Сокращение должности", 3),
]

HR_USERS = {config.HR_BITRIX_ID: config.HR_NAME, 4: "Ершова Дарья (рекрутер)"}


def _pick(rng: random.Random, weighted: dict | list):
    items = list(weighted.items()) if isinstance(weighted, dict) else list(weighted)
    total = sum(w for _, w in items)
    x = rng.uniform(0, total)
    for item, w in items:
        x -= w
        if x <= 0:
            return item
    return items[-1][0]


def fake_person(rng: random.Random) -> tuple[str, str, bool]:
    female = rng.random() < 0.45
    first = rng.choice(FEMALE_FIRST if female else MALE_FIRST)
    surname = rng.choice(SURNAMES) + ("а" if female else "")
    patr = rng.choice(PATRONYMIC_BASE) + ("на" if female else "ич")
    return f"{surname} {first} {patr}", first, female


def _early_reason(rng: random.Random, unit: Unit, has_mentor: bool) -> str:
    w = {"load": 8, "schedule": 14, "salary": 14, "manager": 6, "onboarding": 8,
         "other_job": 16, "probation": 9, "personal": 7}
    if unit.dept == "Склад":
        w["load"] += 18
    if unit.dept in ("Магазин",):
        w["schedule"] += 8
    if unit.dept == "Отдел продаж":
        w["salary"] += 10
    if unit.manager_risk >= 1.5:
        w["manager"] += 22
        w["onboarding"] += 10
    if not has_mentor:
        w["onboarding"] += 14
    return _pick(rng, w)


def _early_day(rng: random.Random) -> int:
    r = rng.random()
    if r < 0.38:
        return rng.randint(1, 14)
    if r < 0.75:
        return rng.randint(20, 45)  # после первой зарплаты
    return rng.randint(46, 89)


def _workday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def generate(today: date, seed: int = 42, months: int = 24) -> tuple[dict, dict, list]:
    rng = random.Random(seed)
    start = date(today.year - months // 12, today.month, 1)
    tasks: list[dict] = []
    profiles: list[dict] = []
    tab_no = 3000
    task_id = 10000
    used_names: set[str] = set()

    def new_name():
        while True:
            full, first, female = fake_person(rng)
            if full not in used_names:
                used_names.add(full)
                return full, first, female

    def add_task(title, description, created: date, responsible: int, tags: list[str]):
        nonlocal task_id
        task_id += rng.randint(1, 7)
        hour = rng.randint(9, 17)
        tasks.append({
            "id": str(task_id),
            "title": title,
            "description": description,
            "status": "5",
            "createdDate": f"{created.isoformat()}T{hour:02d}:{rng.randint(0, 59):02d}:00+03:00",
            "closedDate": f"{created.isoformat()}T{min(hour + 3, 19):02d}:00:00+03:00",
            "responsibleId": str(responsible),
            "groupId": "7",
            "tags": tags,
        })

    # --- Новые сотрудники (есть задача «Приём»)
    d = start
    while d < today:
        month_factor = {3: 1.3, 4: 1.35, 5: 1.2, 9: 1.15, 11: 1.2, 12: 1.1, 1: 0.7, 7: 0.85}.get(d.month, 1.0)
        n_hires = max(0, round(rng.gauss(2.15 * month_factor, 1.0)))
        for _ in range(n_hires):
            hire = _workday(d + timedelta(days=rng.randint(0, 6)))
            if hire >= today:
                continue
            unit = _pick(rng, [(u, u.hire_share) for u in UNITS])
            source = _pick(rng, {k: v[0] for k, v in SOURCES.items()})
            has_mentor = rng.random() > NO_MENTOR_SHARE
            full, first, female = new_name()
            tab_no += 1
            position = rng.choice(unit.positions)
            mentor = None
            if has_mentor:
                m_full, _, _ = fake_person(rng)
                mentor = " ".join(m_full.split()[:2])

            p_early = DEPT_BASE_EARLY[unit.dept] * unit.manager_risk * SOURCES[source][1]
            if not has_mentor:
                p_early *= NO_MENTOR_RISK
            p_early = min(p_early, 0.8)

            fire_day = None
            reason_key = None
            if rng.random() < p_early:
                reason_key = _early_reason(rng, unit, has_mentor)
                fire_day = _early_day(rng)
            else:
                # поздний уход: ~1.4% в месяц после испытательного срока
                months_ahead = 0
                while months_ahead < 30:
                    months_ahead += 1
                    if rng.random() < 0.014:
                        fire_day = 90 + months_ahead * 30 + rng.randint(-14, 14)
                        break
            fire_date = hire + timedelta(days=fire_day) if fire_day else None

            add_task(
                f"Приём сотрудника: {full}",
                "\n".join([
                    f"ФИО: {full}",
                    f"Табельный номер: {tab_no}",
                    f"Должность: {position}",
                    f"Подразделение: {unit.dept}",
                    f"Точка: {unit.location}",
                    f"Руководитель: {unit.manager}",
                    f"Наставник: {mentor or 'не назначен'}",
                    f"Источник кандидата: {source}",
                    f"Дата выхода: {hire.isoformat()}",
                ]),
                hire - timedelta(days=rng.randint(2, 6)), 4, ["HR", "Приём"],
            )

            if fire_date and fire_date < today:
                if reason_key:
                    reason = EARLY_REASONS[reason_key]
                    initiator = "Работодатель" if reason_key == "probation" else "Сотрудник"
                else:
                    reason = _pick(rng, LATE_REASONS)
                    initiator = "Работодатель" if reason == "Сокращение должности" else "Сотрудник"
                add_task(
                    f"Увольнение: {full}",
                    "\n".join([
                        f"ФИО: {full}",
                        f"Табельный номер: {tab_no}",
                        f"Должность: {position}",
                        f"Подразделение: {unit.dept}",
                        f"Точка: {unit.location}",
                        f"Дата приёма: {hire.isoformat()}",
                        f"Последний рабочий день: {fire_date.isoformat()}",
                        f"Инициатор: {initiator}",
                        f"Причина (со слов сотрудника): {reason}",
                    ]),
                    fire_date - timedelta(days=rng.randint(0, 3)), config.HR_BITRIX_ID, ["HR", "Увольнение"],
                )
            elif (today - hire).days <= 95:
                # текущий новичок: запоминаем «скрытую правду» для симуляции опросов
                profiles.append({
                    "tab_no": tab_no,
                    "struggling": bool(reason_key),
                    "cause": reason_key,
                    "silent": rng.random() < 0.12,
                })
        d += timedelta(days=7)

    # --- Уходы «старичков» (приняты до запуска задач в Битрикс24)
    d = start
    while d < today:
        for _ in range(max(0, round(rng.gauss(1.05, 0.9)))):
            fire = _workday(d + timedelta(days=rng.randint(0, 6)))
            if fire >= today:
                continue
            unit = _pick(rng, [(u, u.hire_share) for u in UNITS])
            full, _, _ = new_name()
            tab_no_old = rng.randint(100, 2900)
            hire = fire - timedelta(days=rng.randint(200, 3600))
            hire = max(hire, date(2014, 3, 1))
            reason = _pick(rng, LATE_REASONS)
            add_task(
                f"Увольнение: {full}",
                "\n".join([
                    f"ФИО: {full}",
                    f"Табельный номер: {tab_no_old}",
                    f"Должность: {rng.choice(unit.positions)}",
                    f"Подразделение: {unit.dept}",
                    f"Точка: {unit.location}",
                    f"Дата приёма: {hire.isoformat()}",
                    f"Последний рабочий день: {fire.isoformat()}",
                    f"Инициатор: {'Работодатель' if reason == 'Сокращение должности' else 'Сотрудник'}",
                    f"Причина (со слов сотрудника): {reason}",
                ]),
                fire - timedelta(days=rng.randint(0, 3)), config.HR_BITRIX_ID, ["HR", "Увольнение"],
            )
        d += timedelta(days=7)

    tasks.sort(key=lambda t: t["createdDate"])
    export = {"result": {"tasks": tasks}, "total": len(tasks),
              "time": {"start": datetime.now().timestamp()}}
    users = {str(u.manager_bitrix_id): u.manager for u in UNITS}
    users.update({str(k): v for k, v in HR_USERS.items()})
    return export, users, profiles


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Сгенерировать выгрузку Битрикс24")
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)
    export, users, profiles = generate(date.fromisoformat(args.today), args.seed)
    config.DATA_DIR.mkdir(exist_ok=True)
    config.BITRIX_EXPORT.write_text(json.dumps(export, ensure_ascii=False, indent=1), encoding="utf-8")
    config.BITRIX_USERS.write_text(json.dumps(users, ensure_ascii=False, indent=1), encoding="utf-8")
    (config.DATA_DIR / "sim_profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=1),
                                                      encoding="utf-8")
    print(f"Задач в выгрузке: {export['total']}, текущих новичков: {len(profiles)}")


if __name__ == "__main__":
    main()
