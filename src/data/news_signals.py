"""뉴스 헤드라인 기반 단순 키워드 점수 (Google News RSS)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List
from urllib.parse import quote

import feedparser

# 실적·톤을 대략적으로만 반영 (한국어 헤드라인용)
POSITIVE = (
    "호실적",
    "최대실적",
    "사상최대",
    "역대최대",
    "어닝서프라이즈",
    "서프라이즈",
    "흑자전환",
    "개선",
    "상승",
    "수주",
    "돌파",
    "성장",
    "확대",
    "긍정",
    "기대",
)
NEGATIVE = (
    "악실적",
    "적자",
    "우려",
    "하락",
    "급락",
    "손실",
    "감소",
    "축소",
    "악화",
    "제재",
    "조사",
    "소송",
    "경고",
)


@dataclass
class NewsScore:
    pos_hits: int
    neg_hits: int
    sample_titles: List[str]

    @property
    def net(self) -> int:
        return self.pos_hits - self.neg_hits


def score_stock_news(name: str, max_entries: int = 20) -> NewsScore:
    q = quote(f"{name} 주식")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    parsed = feedparser.parse(url)
    pos = neg = 0
    titles: List[str] = []
    for entry in parsed.entries[:max_entries]:
        t = getattr(entry, "title", "") or ""
        titles.append(t)
        tl = t.lower()
        if any(kw in t or kw.lower() in tl for kw in POSITIVE):
            pos += 1
        if any(kw in t or kw.lower() in tl for kw in NEGATIVE):
            neg += 1
    return NewsScore(pos_hits=pos, neg_hits=neg, sample_titles=titles[:5])
