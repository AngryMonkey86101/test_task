"""ИИ-помощник новичка: отвечает на вопросы по базе знаний (папка kb/).

1. Поиск: BM25 по статьям с упрощённым стеммингом для русского языка.
   Поле «ключи» каждой статьи содержит разговорные синонимы («зп», «получка»).
2. Ответ: если задан DEEPSEEK_API_KEY (или ANTHROPIC_API_KEY) — модель формулирует
   короткий ответ строго по найденным статьям; иначе бот отдаёт саму статью.
3. Нет ответа → вопрос сохраняется как «пробел в базе знаний» и уходит в HR.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_ENDINGS = sorted("""
ами ями ого его ому ему ыми ими ой ей ий ый ая яя ое ее ую юю ом ем ах ях ов ев ам ям ые ие
ешь ет ем ете ут ют ишь ит им ите ат ят ал ала али ало ил ила или ть ться ся сь ет ую ться
а я о е и ы у ю ь й
""".split(), key=len, reverse=True)
_STOP = set("""и в во не на я с со что как а то все она так его но да ты к у же вы за бы по только ее мне
было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас
нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем
была сам чтобы без будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем
ним здесь этом один почти мой тем чтоб нее сейчас были куда зачем всех никогда можно при наконец два об
другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем
хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между мне меня
подскажите подскажи скажите скажи пожалуйста привет здравствуйте добрый день вопрос
сотрудник сотрудника сотрудников сотрудникам работа работы работе компания компании нужно делать сделать
есть ли у вас""".split())


def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    for end in _ENDINGS:
        if len(w) - len(end) >= 3 and w.endswith(end):
            w = w[: -len(end)]
            break
    return w[:6]


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-zа-яё0-9]+(?:-[a-zа-яё0-9]+)?", text.lower())
    return [stem(w) for w in words if w not in _STOP and len(w) > 1]


@dataclass
class Article:
    id: str
    title: str
    keywords: str
    body: str


@dataclass
class Answer:
    text: str
    article_id: str | None
    found: bool


def load_articles(kb_dir: Path = config.KB_DIR) -> list[Article]:
    out = []
    for path in sorted(kb_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        head, _, body = raw.partition("\n---\n")
        lines = head.strip().splitlines()
        title = lines[0].lstrip("# ").strip()
        keywords = next((l.split(":", 1)[1] for l in lines if l.lower().startswith("ключи:")), "")
        out.append(Article(path.stem, title, keywords, body.strip()))
    return out


class KnowledgeBase:
    K1, B = 1.4, 0.6
    THRESHOLD = 2.4   # ниже — считаем, что ответа в базе нет

    def __init__(self, articles: list[Article]):
        self.articles = articles
        self.docs = []
        for a in articles:
            # заголовок и ключевые слова важнее текста — повторяем их
            toks = tokens(a.title) * 3 + tokens(a.keywords) * 3 + tokens(a.body)
            self.docs.append(Counter(toks))
        self.avg_len = sum(sum(d.values()) for d in self.docs) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k: int = 3) -> list[tuple[float, Article]]:
        q = set(tokens(query))
        scored = []
        for doc, art in zip(self.docs, self.articles):
            dl = sum(doc.values())
            s = 0.0
            for t in q:
                f = doc.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * dl / self.avg_len))
            scored.append((s, art))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]


SYSTEM_PROMPT = """Ты — помощник для новых сотрудников компании, которая продаёт спорттовары оптом и в розницу
(склад, магазины, отдел продаж, офис). Тебе пишут люди в первые недели работы, часто с телефона.

Отвечай на «ты», дружелюбно и коротко: 2–5 предложений, без markdown-заголовков. Используй только факты
из статей базы знаний ниже. Не придумывай суммы, даты, телефоны и правила, которых нет в статьях.
Если в статьях нет ответа на вопрос, ответь ровно одним словом: НЕТ_ОТВЕТА."""

NO_ANSWER = "НЕТ_ОТВЕТА"


class Assistant:
    def __init__(self, kb: KnowledgeBase | None = None, use_llm: bool | None = None,
                 provider: str | None = None):
        self.kb = kb or KnowledgeBase(load_articles())
        self.use_llm = config.USE_LLM if use_llm is None else use_llm
        self.provider = provider or (config.LLM_PROVIDER if config.LLM_PROVIDER != "none" else "deepseek")
        self._client = None

    def answer(self, question: str) -> Answer:
        hits = self.kb.search(question)
        best_score, best = hits[0] if hits else (0.0, None)
        if self.use_llm:
            try:
                return self._llm_answer(question, [a for s, a in hits if s > 0.5] or [a for _, a in hits])
            except Exception as exc:  # сеть, лимиты, отказ модели — отвечаем без ИИ
                log.warning("ИИ (%s) недоступен, отвечаю из базы знаний: %s", self.provider, exc)
        if best is None or best_score < self.kb.THRESHOLD:
            return Answer("", None, False)
        return Answer(f"📄 <b>{best.title}</b>\n\n{best.body}", best.id, True)

    def _llm_answer(self, question: str, articles: list[Article]) -> Answer:
        context = "\n\n".join(f"<статья id=\"{a.id}\" название=\"{a.title}\">\n{a.body}\n</статья>"
                              for a in articles)
        prompt = f"{context}\n\nВопрос сотрудника: {question}"
        text = self._ask_deepseek(prompt) if self.provider == "deepseek" else self._ask_claude(prompt)
        if not text or NO_ANSWER in text:
            return Answer("", None, False)
        return Answer(text, articles[0].id if articles else None, True)

    def _ask_deepseek(self, prompt: str) -> str:
        """DeepSeek: OpenAI-совместимый эндпоинт /chat/completions."""
        import httpx

        if self._client is None:
            self._client = httpx.Client(
                base_url=config.DEEPSEEK_BASE_URL, timeout=30,
                headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"})
        r = self._client.post("/chat/completions", json={
            "model": config.DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 600,
            "stream": False,
        })
        if r.status_code == 401:
            raise RuntimeError("DeepSeek: неверный API-ключ (401)")
        if r.status_code == 402:
            raise RuntimeError("DeepSeek: на балансе нет средств (402)")
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()

    def _ask_claude(self, prompt: str) -> str:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic()
        response = self._client.beta.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=2000,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("модель отказалась отвечать")
        return "".join(b.text for b in response.content if b.type == "text").strip()
