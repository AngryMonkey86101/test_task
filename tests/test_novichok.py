import json
from datetime import date, datetime, timedelta

import pytest

from novichok import analytics, config, dashboard, db, generate_data, importer, simulate
from novichok.assistant import Assistant
from novichok.engine import MENU_HELP, Engine
from novichok.pulse import CHECKPOINTS, assess

TODAY = date(2026, 11, 11)


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    conn = db.connect(":memory:")
    conn.execute("""INSERT INTO employees (id, full_name, first_name, position, dept, location, manager_name,
                        manager_bitrix_id, mentor_name, source, hire_date, invite_code)
                    VALUES (1, 'Тестов Пётр Иванович', 'Пётр', 'Кладовщик', 'Склад', 'Склад, смена А',
                            'Гусев Алексей', 21, 'Наставников Иван', 'hh.ru', '2026-11-11', 'NV-1111')""")
    conn.commit()
    clock = Clock(datetime(2026, 11, 11, 11, 0))
    engine = Engine(conn, assistant=Assistant(use_llm=False), now=clock, hr_chat_ids=[])
    return conn, engine, clock


def answer_all(engine, chat, outs, option_for):
    """Отвечает на все вопросы опроса, выбирая вариант через option_for(question)."""
    result = []
    while True:
        q = next((o for o in outs if "session_id" in o.meta), None)
        if not q:
            return result + outs
        question = CHECKPOINTS[q.meta["checkpoint"]][q.meta["q_idx"]]
        outs = engine.on_button(chat, f"a:{q.meta['session_id']}:{q.meta['q_idx']}:{option_for(question)}")
        result += outs


def test_assess_levels():
    assert assess(7, {"d7_mood": 0, "d7_promise": 0}).level == "green"
    assert assess(7, {"d7_mood": 2, "d7_promise": 1}).level == "yellow"
    red = assess(14, {"d14_clarity": 0, "d14_manager": 2})
    assert red.level == "red" and red.about_manager and red.topics == ["manager"]


@pytest.mark.parametrize("q,article", [
    ("когда зп?", "salary"), ("я заболел, что делать", "sick"), ("как оформить возврат без чека", "cash_return"),
    ("сколько длится испытательный срок", "probation"), ("где пообедать", "lunch"),
])
def test_assistant_finds_answers(q, article):
    a = Assistant(use_llm=False).answer(q)
    assert a.found and a.article_id == article


@pytest.mark.parametrize("q", ["есть ли ипотека для сотрудников", "как зовут директора",
                               "можно ли парковать машину у склада"])
def test_assistant_admits_unknown(q):
    assert not Assistant(use_llm=False).answer(q).found


def test_bind_and_first_pulse_green(env):
    conn, engine, clock = env
    outs = engine.on_start(100, "NV-1111")
    assert "Пётр" in outs[0].text
    clock.t += timedelta(days=2)               # 3-й день
    outs = engine.tick()
    assert outs and outs[0].meta["checkpoint"] == 3
    answer_all(engine, 100, outs, lambda q: 0)
    assert conn.execute("SELECT level FROM pulse_sessions WHERE checkpoint=3").fetchone()[0] == "green"
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


def test_bad_answer_creates_task_for_manager_or_hr(env):
    conn, engine, clock = env
    engine.on_start(100, "NV-1111")
    clock.t += timedelta(days=6)               # 7-й день: тяжело
    answer_all(engine, 100, engine.tick(), lambda q: len(q.options) - 1)
    task = conn.execute("SELECT * FROM bitrix_outbox ORDER BY id DESC").fetchone()
    assert task["responsible"] == "Гусев Алексей" and "О чём поговорить" in task["description"]
    clock.t += timedelta(days=7)               # 14-й день: проблема с руководителем → в HR
    answer_all(engine, 100, engine.tick(), lambda q: 2 if q.about_manager else 0)
    task = conn.execute("SELECT * FROM bitrix_outbox ORDER BY id DESC").fetchone()
    assert task["responsible"] == config.HR_NAME


def test_silence_on_key_checkpoint(env):
    conn, engine, clock = env
    engine.on_start(100, "NV-1111")
    clock.t += timedelta(days=6)
    assert engine.tick()                        # опрос 7-го дня отправлен, но не отвечен
    clock.t += timedelta(days=2)
    engine.tick()
    assert conn.execute("SELECT kind FROM alerts").fetchone()["kind"] == "silence"


def test_help_button_goes_to_hr(env):
    conn, engine, _ = env
    engine.on_start(100, "NV-1111")
    outs = engine.on_text(100, MENU_HELP)
    engine.on_button(100, outs[0].buttons[3][0][1])  # «Думаю уйти»
    alert = conn.execute("SELECT * FROM alerts").fetchone()
    assert alert["level"] == "red" and alert["responsible"] == config.HR_NAME


def test_unknown_question_goes_to_hr(env):
    conn, engine, _ = env
    engine.on_start(100, "NV-1111")
    engine.on_text(100, "есть ли ипотека для сотрудников")
    assert conn.execute("SELECT answered FROM kb_questions").fetchone()[0] == 0
    assert "Вопрос новичка без ответа" in conn.execute("SELECT title FROM bitrix_outbox").fetchone()[0]


def test_demo_time_travel(env):
    conn, engine, _ = env
    engine.on_button(200, "demo:Магазин")
    outs = engine.demo_day(200, 30)
    q = next(o for o in outs if "session_id" in o.meta)
    assert q.meta["checkpoint"] == 30
    outs = answer_all(engine, 200, outs, lambda q: 2 if q.id == "d30_salary" else 0)
    assert any("Задача в Битрикс24" in o.text for o in outs)   # копия сигнала в демо-чат


def test_import_parses_bitrix_export(tmp_path):
    export, users, _ = generate_data.generate(TODAY)
    (tmp_path / "e.json").write_text(json.dumps(export, ensure_ascii=False), "utf-8")
    (tmp_path / "u.json").write_text(json.dumps(users, ensure_ascii=False), "utf-8")
    conn = db.connect(":memory:")
    n = importer.import_export(conn, tmp_path / "e.json", tmp_path / "u.json")
    assert n > 300
    row = conn.execute("SELECT * FROM employees WHERE source!='до Битрикс24' LIMIT 1").fetchone()
    assert row["manager_bitrix_id"] in {u.manager_bitrix_id for u in generate_data.UNITS}


def test_full_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    export, users, profiles = generate_data.generate(TODAY)
    for name, obj in (("e.json", export), ("u.json", users), ("sim_profiles.json", profiles)):
        (tmp_path / name).write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
    conn = db.connect(":memory:")
    importer.import_export(conn, tmp_path / "e.json", tmp_path / "u.json")
    stats = simulate.run(conn, TODAY)
    assert stats["joined"] > 0
    a = analytics.compute(conn, TODAY)
    assert 0.25 < a["kpi"]["early_share"] < 0.45      # «каждый третий из ушедших»
    assert a["pulse"]["done"] > 0 and a["tasks"]
    html = open(dashboard.build(conn, TODAY), encoding="utf-8").read()
    assert "Радар" in html and "Гусев" in html


def test_llm_failure_falls_back_to_kb():
    class Broken:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("нет сети")

    a = Assistant(use_llm=True, provider="claude")
    a._client = Broken()
    assert a.answer("когда зп?").article_id == "salary"


def test_telegram_app_builds():
    from novichok import bot

    app = bot.build_app("123456:TEST-TOKEN")
    assert app.job_queue is not None and len(app.handlers[0]) == 7


def _deepseek(handler):
    import httpx

    a = Assistant(use_llm=True, provider="deepseek")
    a._client = httpx.Client(base_url="https://api.deepseek.com", transport=httpx.MockTransport(handler))
    return a


def test_deepseek_answer_uses_kb_context():
    import httpx

    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen.update(path=request.url.path, body=body)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                                  "content": "Аванс приходит 25-го числа."}}]})

    ans = _deepseek(handler).answer("когда зп?")
    assert ans.found and ans.text == "Аванс приходит 25-го числа." and ans.article_id == "salary"
    assert seen["path"] == "/chat/completions" and seen["body"]["messages"][0]["role"] == "system"
    assert "Зарплата и аванс" in seen["body"]["messages"][1]["content"]


def test_deepseek_no_answer_and_errors():
    import httpx

    no = _deepseek(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "НЕТ_ОТВЕТА"}}]}))
    assert not no.answer("есть ли ипотека").found
    broke = _deepseek(lambda r: httpx.Response(402, json={"error": {"message": "Insufficient Balance"}}))
    assert broke.answer("когда зп?").article_id == "salary"   # без денег на балансе — ответ из базы знаний
