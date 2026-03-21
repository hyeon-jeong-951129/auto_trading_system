"""Naver Finance 투자자별 매매동향 (종목별)."""

from __future__ import annotations

import re
from io import StringIO
from typing import Optional

import pandas as pd
import requests

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
    }
)


def _parse_number(x) -> float:
    if pd.isna(x):
        return 0.0
    s = str(x).strip()
    if not s or s == "-":
        return 0.0
    s = re.sub(r"[,+%]", "", s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def fetch_investor_daily(code: str) -> Optional[pd.DataFrame]:
    """
    종목코드(6자리) 기준 최근 일별: 기관·외국인 순매매량(주).
    실패 시 None.
    """
    code = str(code).zfill(6)
    url = f"https://finance.naver.com/item/frgn.naver?code={code}"
    r = _SESSION.get(url, timeout=20)
    r.encoding = "euc-kr"
    if r.status_code != 200:
        return None
    try:
        tables = pd.read_html(StringIO(r.text))
    except ValueError:
        return None
    raw = None
    for cand in tables:
        if not isinstance(cand.columns, pd.MultiIndex):
            continue
        flat = "".join(str(x) for x in cand.columns.values)
        if "기관" in flat and "외국인" in flat and "순매매" in flat:
            raw = cand
            break
    if raw is None:
        return None
    raw = raw.copy()
    raw.columns = [
        "날짜",
        "종가",
        "전일비",
        "등락률",
        "거래량",
        "기관순매매",
        "외국인순매매",
        "외국인보유주수",
        "외국인보유율",
    ]
    df = raw.copy()
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df.dropna(subset=["날짜"])
    for col in ("기관순매매", "외국인순매매", "거래량"):
        df[col] = df[col].map(_parse_number)
    df["등락률"] = df["등락률"].astype(str).str.replace("%", "", regex=False)
    df["등락률"] = pd.to_numeric(df["등락률"], errors="coerce").fillna(0.0)
    df = df.sort_values("날짜")
    return df[["날짜", "종가", "등락률", "거래량", "기관순매매", "외국인순매매"]]


def sum_flow(df: pd.DataFrame, days: int) -> tuple[float, float]:
    """최근 `days`거래일 기관·외국인 순매매 합(주)."""
    tail = df.tail(days)
    inst = float(tail["기관순매매"].sum())
    frgn = float(tail["외국인순매매"].sum())
    return inst, frgn
