"""Дашборд для HR: один самодостаточный HTML-файл (без внешних библиотек)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from html import escape as e

from . import config
from .analytics import compute
from .pulse import CHECKPOINT_DAYS, TOPIC_NAMES

SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"]
DEPTS = ["Склад", "Магазин", "Отдел продаж", "Офис"]
STATUS = {
    "red": ("🔴", "Нужен разговор", "st-red"),
    "yellow": ("🟡", "Присмотреться", "st-yellow"),
    "green": ("🟢", "Всё в порядке", "st-green"),
    "none": ("⚪", "Не подключён к боту", "st-none"),
}


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def rub(x: int) -> str:
    return f"{x / 1_000_000:.1f} млн ₽".replace(".", ",") if x >= 1_000_000 else f"{x:,} ₽".replace(",", " ")


def hbars(rows: list[dict], avg: float | None = None, label_key="name") -> str:
    """Горизонтальные столбцы «доля ранних уходов» + таблица-двойник."""
    mx = max([r["rate"] for r in rows] + [avg or 0, 0.01])
    out = ['<div class="hbars">']
    for r in rows:
        w = r["rate"] / mx * 100
        tip = f"{r[label_key]}: ушли в первые 90 дней {r['early']} из {r['n']} ({pct(r['rate'])})"
        out.append(
            f'<div class="hb-row" tabindex="0" data-tip="{e(tip)}">'
            f'<div class="hb-label">{e(r[label_key])}</div>'
            f'<div class="hb-track"><div class="hb-bar" style="width:{w:.1f}%"></div>'
            + (f'<div class="hb-avg" style="left:{avg / mx * 100:.1f}%"></div>' if avg else "")
            + f'</div><div class="hb-val">{pct(r["rate"])} <span class="muted">· {r["early"]}/{r["n"]}</span></div>'
            "</div>")
    out.append("</div>")
    if avg:
        out.append(f'<div class="legend"><span class="lg-avg"></span>в среднем по компании {pct(avg)}</div>')
    return "".join(out)


def count_bars(items: list[tuple[str, int]]) -> str:
    mx = max([n for _, n in items] + [1])
    total = sum(n for _, n in items) or 1
    return '<div class="hbars">' + "".join(
        f'<div class="hb-row" tabindex="0" data-tip="{e(k)}: {n} ({n / total:.0%})">'
        f'<div class="hb-label">{e(k)}</div><div class="hb-track"><div class="hb-bar" '
        f'style="width:{n / mx * 100:.1f}%"></div></div><div class="hb-val">{n}</div></div>'
        for k, n in items) + "</div>"


def weeks_chart(weeks: list[dict]) -> str:
    mx = max(w["n"] for w in weeks) or 1
    total = sum(w["n"] for w in weeks) or 1
    cols = "".join(
        f'<div class="vb-col" tabindex="0" data-tip="{w["week"]}-я неделя: {w["n"]} чел. ({w["n"] / total:.0%})">'
        f'<div class="vb-bar" style="height:{w["n"] / mx * 100:.1f}%"></div><div class="vb-x">{w["week"]}</div></div>'
        for w in weeks)
    return f'<div class="vbars">{cols}</div><div class="axis-title">неделя работы</div>'


def survival_svg(survival: dict) -> str:
    W, H, L, R, T, B = 660, 260, 40, 140, 12, 28
    pw, ph = W - L - R, H - T - B
    ymin = 40
    x = lambda d: L + d / 180 * pw
    y = lambda v: T + (100 - v) / (100 - ymin) * ph
    parts = [f'<svg viewBox="0 0 {W} {H}" class="surv" role="img" aria-label="Доля оставшихся сотрудников по дням работы">']
    for v in range(ymin, 101, 20):
        parts.append(f'<line x1="{L}" x2="{L + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                     f'<text x="{L - 6}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">{v}%</text>')
    for d in (0, 30, 60, 90, 120, 150, 180):
        parts.append(f'<text x="{x(d):.1f}" y="{H - 8}" class="tick" text-anchor="middle">{d}</text>')
    parts.append(f'<rect x="{x(0):.1f}" y="{T}" width="{x(90) - x(0):.1f}" height="{ph}" class="zone"/>'
                 f'<text x="{x(45):.1f}" y="{T + 14}" class="zone-label" text-anchor="middle">испытательный срок</text>')
    labels = []
    for i, dept in enumerate(DEPTS):
        c = survival[dept]["curve"]
        pts = " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in enumerate(c))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{SERIES[i]}" stroke-width="2" '
                     'stroke-linejoin="round"/>')
        labels.append([y(c[-1]), dept, c[-1], i])
    labels.sort()
    for j in range(1, len(labels)):  # раздвигаем подписи, чтобы не налезали
        labels[j][0] = max(labels[j][0], labels[j - 1][0] + 15)
    for ly, dept, v, i in labels:
        parts.append(f'<circle cx="{x(180):.1f}" cy="{y(v):.1f}" r="4" fill="{SERIES[i]}" class="ring"/>'
                     f'<text x="{x(180) + 10:.1f}" y="{ly + 4:.1f}" class="dlabel">{e(dept)} {v:.0f}%</text>')
    parts.append(f'<line class="xhair" x1="0" x2="0" y1="{T}" y2="{T + ph}" style="display:none"/>'
                 f'<rect class="hit" x="{L}" y="{T}" width="{pw}" height="{ph}" fill="transparent"/>')
    parts.append("</svg>")
    return "".join(parts)


def trail_html(trail: list[dict]) -> str:
    by = {t["cp"]: t["level"] for t in trail}
    cls = {"green": "dot-green", "yellow": "dot-yellow", "red": "dot-red", "silent": "dot-silent", "sent": "dot-sent"}
    name = {"green": "ок", "yellow": "жёлтый", "red": "красный", "silent": "не ответил", "sent": "ждём ответа"}
    return '<span class="trail">' + "".join(
        f'<span class="dot {cls.get(by.get(cp), "dot-empty")}" title="День {cp}: {name.get(by.get(cp), "ещё впереди")}">'
        f'{cp}</span>' for cp in CHECKPOINT_DAYS) + "</span>"


def build(conn: sqlite3.Connection, today: date | None = None) -> str:
    today = today or date.today()
    a = compute(conn, today)
    k = a["kpi"]
    radar = a["radar"]
    n_red = sum(r["status"] == "red" for r in radar)
    n_yellow = sum(r["status"] == "yellow" for r in radar)
    worst_mgr = a["by_manager"][0]
    best_same_dept = min((m for m in a["by_manager"] if m["name"].split("·")[1].strip().startswith(
        worst_mgr["name"].split("·")[1].strip().split(",")[0])), key=lambda m: m["rate"])
    mentor = {m["name"]: m for m in a["by_mentor"]}
    src = {s["name"]: s for s in a["by_source"]}
    early_total = sum(w["n"] for w in a["weeks"]) or 1
    first2 = sum(w["n"] for w in a["weeks"][:2])

    # ---------- радар
    radar_rows = []
    for r in radar:
        icon, label, cls = STATUS[r["status"]]
        la = r["last_alert"]
        signal = ""
        if la:
            topics = ", ".join(TOPIC_NAMES.get(t, t) for t in la["topics"].split("; "))
            signal = (f'{e(topics)}<div class="muted small">задача #{la["bitrix_task_id"]} → '
                      f'{e(la["responsible"])}, {la["created_at"][:10]}</div>')
        radar_rows.append(
            f'<tr class="{cls}"><td><span class="chip {cls}">{icon} {label}</span></td>'
            f'<td><b>{e(r["name"])}</b>{" <span class=chip-demo>демо</span>" if r["is_demo"] else ""}'
            f'<div class="muted small">{e(r["position"])} · {e(r["location"])}</div></td>'
            f'<td class="num">{r["day"]}</td><td>{trail_html(r["trail"])}</td>'
            f'<td>{signal or "<span class=muted>—</span>"}</td></tr>')

    # ---------- задачи
    task_rows = "".join(
        f'<tr><td class="num">#{t["task_id"]}</td><td><details><summary>{e(t["title"])}</summary>'
        f'<pre>{e(t["description"])}</pre></details></td><td>{e(t["responsible"])}</td>'
        f'<td class="num">{t["deadline"][:10]}</td>'
        f'<td>{"имитация" if t["mode"] == "mock" else "Битрикс24" if t["mode"] == "live" else "ошибка"}</td></tr>'
        for t in a["tasks"])

    kb = a["kb"]
    gaps = "".join(f"<li>{e(q)} <span class='muted'>×{n}</span></li>" for q, n in kb["gaps"][:10]) \
        or "<li class='muted'>Пока нет</li>"
    surv_json = json.dumps({d: a["survival"][d]["curve"] for d in DEPTS}, ensure_ascii=False)
    weeks_tbl = "".join(f"<tr><td>{w['week']}</td><td class=num>{w['n']}</td></tr>" for w in a["weeks"])
    surv_tbl = "".join(
        f"<tr><td>{d}</td><td class=num>{a['survival'][d]['n']}</td>"
        + "".join(f"<td class=num>{a['survival'][d]['curve'][x]:.0f}%</td>" for x in (14, 30, 60, 90, 180))
        + "</tr>" for d in DEPTS)
    cost_tbl = ", ".join(f"{k_}: {v // 1000} тыс. ₽" for k_, v in a["costs"].items())
    topics_items = [(TOPIC_NAMES.get(t, t), n) for t, n in a["pulse"]["topics"]]

    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Новичок: первые 90 дней</title>
<style>
:root {{
  color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#6f6d68;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --good:#0ca30c; --warn:#fab219; --crit:#d03b3b; --zone:rgba(42,120,214,.06);
  --red-bg:#fbeaea; --yellow-bg:#fdf4dc; --green-bg:#e8f5e8; --none-bg:#f0efec;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --muted:#9a988f;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --zone:rgba(57,135,229,.10);
  --red-bg:#3a1d1d; --yellow-bg:#3a3018; --green-bg:#1b301b; --none-bg:#2a2a28;
}} }}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --muted:#9a988f;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --zone:rgba(57,135,229,.10);
  --red-bg:#3a1d1d; --yellow-bg:#3a3018; --green-bg:#1b301b; --none-bg:#2a2a28;
}}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--page); color:var(--ink); font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif }}
main {{ max-width:1180px; margin:0 auto; padding:24px 16px 48px }}
h1 {{ font-size:26px; margin:0 0 4px }} h2 {{ font-size:19px; margin:36px 0 4px }}
h3 {{ font-size:15px; margin:0 0 10px }}
.lead {{ color:var(--ink2); margin:0 0 14px; max-width:820px }}
.muted {{ color:var(--muted) }} .small {{ font-size:12.5px }}
.note {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:10px 14px; color:var(--ink2); font-size:13.5px }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr)); gap:12px; margin-top:16px }}
.tile, .card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:16px }}
.tile .v {{ font-size:30px; font-weight:650; margin:2px 0 }} .tile .l {{ color:var(--ink2); font-size:13.5px }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr)); gap:12px }}
.insight {{ border-left:3px solid var(--s1); padding:2px 0 2px 12px; margin:10px 0 0; color:var(--ink2); font-size:14px }}
.hbars {{ display:flex; flex-direction:column; gap:6px }}
.hb-row {{ display:grid; grid-template-columns:minmax(120px,38%) 1fr 92px; gap:10px; align-items:center; border-radius:6px; outline:none }}
.hb-row:hover, .hb-row:focus {{ background:var(--zone) }}
.hb-label {{ font-size:13.5px; overflow-wrap:anywhere }}
.hb-track {{ position:relative; height:14px }}
.hb-bar {{ height:100%; background:var(--s1); border-radius:0 4px 4px 0; min-width:2px }}
.hb-avg {{ position:absolute; top:-3px; bottom:-3px; width:2px; background:var(--ink2) }}
.hb-val {{ text-align:right; font-size:13px; font-variant-numeric:tabular-nums; white-space:nowrap }}
.legend {{ font-size:12.5px; color:var(--ink2); margin-top:8px; display:flex; align-items:center; gap:6px }}
.lg-avg {{ display:inline-block; width:2px; height:12px; background:var(--ink2) }}
.vbars {{ display:flex; align-items:flex-end; gap:2px; height:180px; padding-top:6px; border-bottom:1px solid var(--axis) }}
.vb-col {{ flex:1; height:100%; display:flex; flex-direction:column; justify-content:flex-end; position:relative; outline:none }}
.vb-col:hover .vb-bar, .vb-col:focus .vb-bar {{ opacity:.8 }}
.vb-bar {{ background:var(--s1); border-radius:4px 4px 0 0; min-height:2px }}
.vb-x {{ position:absolute; bottom:-20px; width:100%; text-align:center; font-size:12px; color:var(--muted) }}
.axis-title {{ margin-top:24px; font-size:12px; color:var(--muted); text-align:center }}
svg.surv {{ width:100%; height:auto; display:block }}
svg .grid {{ stroke:var(--grid); stroke-width:1 }} svg .tick {{ fill:var(--muted); font-size:11px }}
svg .zone {{ fill:var(--zone) }} svg .zone-label {{ fill:var(--muted); font-size:11px }}
svg .dlabel {{ fill:var(--ink); font-size:12px }} svg .ring {{ stroke:var(--surface); stroke-width:2 }}
svg .xhair {{ stroke:var(--axis); stroke-width:1 }}
.legend-series {{ display:flex; gap:14px; flex-wrap:wrap; font-size:12.5px; color:var(--ink2); margin-bottom:6px }}
.legend-series i {{ display:inline-block; width:14px; height:3px; border-radius:2px; vertical-align:middle; margin-right:5px }}
#tip {{ position:fixed; pointer-events:none; background:var(--ink); color:var(--surface); font-size:12.5px; padding:6px 9px; border-radius:6px; max-width:280px; z-index:10; display:none; white-space:pre-line }}
details.tv {{ margin-top:10px; font-size:13px }} details.tv summary {{ cursor:pointer; color:var(--ink2) }}
table {{ border-collapse:collapse; width:100% }}
th, td {{ text-align:left; padding:8px; border-bottom:1px solid var(--grid); vertical-align:top; font-size:13.5px }}
th {{ color:var(--ink2); font-weight:600; font-size:12.5px }}
td.num {{ font-variant-numeric:tabular-nums; white-space:nowrap }}
.scroll {{ overflow-x:auto }}
.chip {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:12.5px; white-space:nowrap }}
.chip.st-red {{ background:var(--red-bg) }} .chip.st-yellow {{ background:var(--yellow-bg) }}
.chip.st-green {{ background:var(--green-bg) }} .chip.st-none {{ background:var(--none-bg) }}
.chip-demo {{ font-size:11px; padding:1px 6px; border-radius:6px; background:var(--zone); color:var(--ink2) }}
.trail {{ display:inline-flex; gap:3px }}
.dot {{ display:inline-flex; align-items:center; justify-content:center; width:24px; height:24px; border-radius:50%; font-size:10.5px; border:1px solid var(--border); color:var(--ink2) }}
.dot-green {{ background:var(--good); color:#fff; border-color:transparent }}
.dot-yellow {{ background:var(--warn); color:#0b0b0b; border-color:transparent }}
.dot-red {{ background:var(--crit); color:#fff; border-color:transparent }}
.dot-silent {{ background:var(--none-bg); border:1.5px dashed var(--ink2); color:var(--ink) }}
.dot-sent {{ background:var(--none-bg) }}
.dot-empty {{ opacity:.45 }}
pre {{ white-space:pre-wrap; font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace; background:var(--page); padding:10px; border-radius:8px; margin:8px 0 0 }}
ul.gaps {{ margin:0; padding-left:18px }} ul.gaps li {{ margin:4px 0 }}
footer {{ margin-top:40px; color:var(--muted); font-size:12.5px }}
@media (max-width:600px) {{ .hb-row {{ grid-template-columns:1fr auto; }} .hb-track {{ grid-column:1 / -1; order:3 }} }}
</style></head>
<body><main>
<h1>Новичок: первые 90 дней</h1>
<p class="lead">Дашборд для HR на {today:%d.%m.%Y}. Сверху — кто из нынешних новичков нуждается в разговоре прямо сейчас (по ответам в Telegram-боте), ниже — где и почему мы теряем новых сотрудников (по задачам «Приём» и «Увольнение» из Битрикс24).</p>
<div class="note">⚠️ Все данные вымышленные и сгенерированы для демонстрации. Люди, руководители и точки не настоящие.</div>

<div class="tiles">
  <div class="tile"><div class="l">Приняли за 12 месяцев</div><div class="v">{k['hires_12m']}</div></div>
  <div class="tile"><div class="l">Ушли за 12 месяцев</div><div class="v">{k['leavers_12m']}</div>
    <div class="l">из них в первые 90 дней — <b>{k['early_12m']} ({pct(k['early_share'])})</b></div></div>
  <div class="tile"><div class="l">Не доработали 90 дней из каждых 10 принятых</div><div class="v">{k['base_rate'] * 10:.1f}</div>
    <div class="l">по {k['cohort_n']} новичкам за 2 года</div></div>
  <div class="tile"><div class="l">Потери на ранних уходах за год (оценка)</div><div class="v">≈ {rub(k['cost_12m'])}</div>
    <div class="l">найм, обучение, недозагрузка</div></div>
  <div class="tile"><div class="l">Новички на радаре сейчас</div><div class="v">{len(radar)}</div>
    <div class="l">🔴 {n_red} нужен разговор · 🟡 {n_yellow} присмотреться</div></div>
</div>

<h2>Радар: новички прямо сейчас</h2>
<p class="lead">Бот задаёт по 2 вопроса на 3, 7, 14, 30, 60 и 90 день. Тревожный ответ или молчание — задача в Битрикс24
руководителю (или HR, если проблема в руководителе) с подсказкой, о чём говорить. Кружки — контрольные точки:
<span class="dot dot-green">7</span> всё хорошо, <span class="dot dot-yellow">7</span> есть вопросы,
<span class="dot dot-red">7</span> тревожный сигнал, <span class="dot dot-silent">7</span> не ответил.</p>
<div class="card scroll"><table>
<thead><tr><th>Статус</th><th>Сотрудник</th><th>День</th><th>Опросы</th><th>Последний сигнал</th></tr></thead>
<tbody>{''.join(radar_rows)}</tbody></table></div>

<h2>Где теряем новичков</h2>
<p class="lead">Доля сотрудников, ушедших в первые 90 дней, среди всех принятых за 2 года (у кого 90 дней уже прошли).</p>
<div class="grid2">
  <div class="card"><h3>По руководителям</h3>{hbars(a['by_manager'], k['base_rate'])}
    <p class="insight">{e(worst_mgr['name'].split('·')[1].strip())} (рук. {e(worst_mgr['name'].split('·')[0].strip())})
    теряет {pct(worst_mgr['rate'])} новичков, а {e(best_same_dept['name'].split('·')[1].strip())}
    (рук. {e(best_same_dept['name'].split('·')[0].strip())}) — {pct(best_same_dept['rate'])}.
    Работа та же, разница — в том, как встречают новичков.</p></div>
  <div class="card"><h3>По источникам найма</h3>{hbars(a['by_source'], k['base_rate'])}
    <p class="insight">По рекомендации сотрудников уходят {pct(src['Рекомендация сотрудника']['rate'])}, с Авито —
    {pct(src['Авито Работа']['rate'])}. Стоит развивать реферальную программу и сверять текст вакансий с реальностью.</p></div>
  <div class="card"><h3>Наставник</h3>{hbars(a['by_mentor'], k['base_rate'])}
    <p class="insight">Без наставника не дорабатывают испытательный срок {pct(mentor['Без наставника']['rate'])} против
    {pct(mentor['С наставником']['rate'])}. Бот автоматически ставит задачу «назначить наставника», если его нет в задаче о приёме.</p></div>
  <div class="card"><h3>По подразделениям</h3>{hbars(a['by_dept'], k['base_rate'])}</div>
</div>

<h2>Когда уходят</h2>
<div class="grid2">
  <div class="card"><h3>Ранние уходы по неделям работы</h3>{weeks_chart(a['weeks'])}
    <p class="insight">{first2 / early_total:.0%} ранних уходов — в первые 2 недели: человек сравнивает обещанное с реальностью.
    Второй всплеск — 5–6 неделя, после первой зарплаты. Поэтому опросы стоят на 3, 7 и 30 день.</p>
    <details class="tv"><summary>Таблица</summary><table><tr><th>Неделя</th><th>Ушли</th></tr>{weeks_tbl}</table></details></div>
  <div class="card"><h3>Сколько сотрудников остаётся, % от принятых</h3>
    <div class="legend-series">{''.join(f'<span><i style="background:{SERIES[i]}"></i>{d}</span>' for i, d in enumerate(DEPTS))}</div>
    <div id="surv-wrap">{survival_svg(a['survival'])}</div><div class="axis-title" style="margin-top:0">день работы</div>
    <details class="tv"><summary>Таблица</summary><table><tr><th>Подразделение</th><th>Принято</th><th>14 дн.</th><th>30 дн.</th><th>60 дн.</th><th>90 дн.</th><th>180 дн.</th></tr>{surv_tbl}</table></details></div>
</div>

<h2>Почему уходят</h2>
<div class="grid2">
  <div class="card"><h3>Причины ранних уходов (до 90 дней)</h3>{count_bars(a['reasons_early'])}
    <p class="insight">Больше половины причин — то, что можно заметить и исправить в первые недели: руководитель, никто не объяснил,
    график и зарплата не как обещали. Именно об этом спрашивает бот.</p></div>
  <div class="card"><h3>Для сравнения: причины поздних уходов</h3>{count_bars(a['reasons_late'])}</div>
</div>

<h2>Что говорят новички</h2>
<div class="grid2">
  <div class="card"><h3>Сигналы из опросов и кнопки «Нужна помощь»</h3>
    <p class="muted small">Ответили на {pct(a['pulse']['done'] / max(a['pulse']['total'], 1))} опросов ({a['pulse']['done']} из {a['pulse']['total']})</p>
    {count_bars(topics_items) if topics_items else '<p class="muted">Пока нет</p>'}</div>
  <div class="card"><h3>ИИ-помощник: вопросы новичков</h3>
    <p class="muted small">Всего вопросов: {kb['total']}, бот ответил сам на {pct(kb['answered'] / max(kb['total'], 1))}</p>
    {count_bars(kb['top'])}
    <h3 style="margin-top:16px">Нет ответа в базе знаний → стоит дописать</h3><ul class="gaps">{gaps}</ul></div>
</div>

<h2>Задачи в Битрикс24, созданные ботом</h2>
<p class="lead">Нажмите на задачу, чтобы увидеть текст, который получит ответственный.</p>
<div class="card scroll"><table><thead><tr><th>№</th><th>Задача</th><th>Ответственный</th><th>Срок</th><th>Режим</th></tr></thead>
<tbody>{task_rows}</tbody></table></div>

<footer>
<p><b>Как считается.</b> Ранний уход — увольнение раньше 90-го дня. «Где теряем» — по сотрудникам, у которых 90 дней уже прошли,
группы меньше 5 человек не показываются. Кривая «сколько остаётся» — метод Каплана — Майера по всем принятым (учитывает тех,
кто работает недавно). Стоимость раннего ухода (настраивается в config.py): {cost_tbl}.</p>
<p>Собрано: python -m novichok dashboard · {today:%d.%m.%Y}</p>
</footer>
</main>
<div id="tip" role="tooltip"></div>
<script>
(() => {{
  const tip = document.getElementById('tip');
  const show = (t, x, y) => {{ tip.textContent = t; tip.style.display = 'block';
    tip.style.left = Math.min(x + 12, innerWidth - tip.offsetWidth - 8) + 'px'; tip.style.top = (y + 12) + 'px'; }};
  const hide = () => tip.style.display = 'none';
  document.querySelectorAll('[data-tip]').forEach(el => {{
    el.addEventListener('mousemove', ev => show(el.dataset.tip, ev.clientX, ev.clientY));
    el.addEventListener('mouseleave', hide);
    el.addEventListener('focus', () => {{ const r = el.getBoundingClientRect(); show(el.dataset.tip, r.left, r.bottom); }});
    el.addEventListener('blur', hide);
  }});
  const data = {surv_json};
  const svg = document.querySelector('svg.surv'); const hit = svg.querySelector('.hit'); const xh = svg.querySelector('.xhair');
  hit.addEventListener('mousemove', ev => {{
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    const L = 40, pw = 660 - 40 - 140; const d = Math.max(0, Math.min(180, Math.round((p.x - L) / pw * 180)));
    const x = L + d / 180 * pw; xh.setAttribute('x1', x); xh.setAttribute('x2', x); xh.style.display = '';
    show('День ' + d + '\\n' + Object.entries(data).map(([k, v]) => k + ': ' + Math.round(v[d]) + '%').join('\\n'), ev.clientX, ev.clientY);
  }});
  hit.addEventListener('mouseleave', () => {{ xh.style.display = 'none'; hide(); }});
}})();
</script>
</body></html>"""
    config.REPORTS_DIR.mkdir(exist_ok=True)
    path = config.REPORTS_DIR / "dashboard.html"
    path.write_text(html, encoding="utf-8")
    return str(path)
