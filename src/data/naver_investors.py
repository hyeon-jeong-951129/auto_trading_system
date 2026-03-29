"""Naver Finance 투자자별 매매동향 (종목별)."""

from __future__ import annotations

import re
from io import StringIO
from typing import Any, Dict, Optional

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


def _parse_frgn_page_session_date(html: str) -> Optional[pd.Timestamp]:
    """
    외국인/투자자 표 상단에 표시되는 '기준일'(예: 2026.03.27).
    tbody 일별 행이 아직 한 줄 덜 내려온 경우(표 막일 < 기준일) 감지용.
    """
    m = re.search(r'class="date">\s*(\d{4})\.(\d{2})\.(\d{2})', html)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return pd.Timestamp(year=y, month=mo, day=d)


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


def fetch_investor_daily(
    code: str,
) -> tuple[Optional[pd.DataFrame], Optional[pd.Timestamp]]:
    """
    종목코드(6자리) 기준 최근 일별: 개인·기관·외국인 순매매량(주).

    반환:
    - 표(DataFrame): 파싱된 일별 테이블(서버 HTML 기준, 최신 거래일 행이 아직 없을 수 있음)
    - 페이지 기준일: 상단 `em.date` 기준일(브라우저와 표 tbody가 어긋날 때 비교용)
    실패 시 (None, None).
    """
    code = str(code).zfill(6)
    url = f"https://finance.naver.com/item/frgn.naver?code={code}"
    r = _SESSION.get(url, timeout=20)
    r.encoding = "euc-kr"
    if r.status_code != 200:
        return None, None
    page_session = _parse_frgn_page_session_date(r.text)
    try:
        tables = pd.read_html(StringIO(r.text))
    except ValueError:
        return None, page_session
    raw = None
    for cand in tables:
        if not isinstance(cand.columns, pd.MultiIndex):
            continue
        flat = "".join(str(x) for x in cand.columns.values)
        if "기관" in flat and "외국인" in flat and "순매매" in flat:
            raw = cand
            break
    if raw is None:
        return None, page_session
    raw = raw.copy()
    # 네이버 표 컬럼 수가 환경에 따라 다를 수 있어 길이로 분기
    # (개인 순매수 열이 있는 경우 포함)
    if raw.shape[1] >= 10:
        raw = raw.iloc[:, :10]
        raw.columns = [
            "날짜",
            "종가",
            "전일비",
            "등락률",
            "거래량",
            "개인순매매",
            "기관순매매",
            "외국인순매매",
            "외국인보유주수",
            "외국인보유율",
        ]
    else:
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
    for col in ("개인순매매", "기관순매매", "외국인순매매", "거래량"):
        if col not in df.columns:
            continue
        df[col] = df[col].map(_parse_number)
    df["등락률"] = df["등락률"].astype(str).str.replace("%", "", regex=False)
    df["등락률"] = pd.to_numeric(df["등락률"], errors="coerce").fillna(0.0)
    df = df.sort_values("날짜")
    cols = ["날짜", "종가", "등락률", "거래량", "기관순매매", "외국인순매매"]
    if "개인순매매" in df.columns:
        cols.insert(4, "개인순매매")
    return df[cols], page_session


def sum_flow(df: pd.DataFrame, days: int) -> tuple[float, float]:
    """최근 `days`거래일 기관·외국인 순매매 합(주)."""
    tail = df.tail(days)
    inst = float(tail["기관순매매"].sum())
    frgn = float(tail["외국인순매매"].sum())
    return inst, frgn


def flow_quality_metrics(df: pd.DataFrame, days: int) -> Dict[str, Any]:
    """
    최근 `days`일 구간 수급 질적 지표.
    - 외국인 순매수 양수인 날 수, 막 거래일 외국인/기관 순매수
    - 후반(최근 2일) vs 전반(그 앞 3일) 외국인 순매수 차이(모멘텀)
    - 일별 외국인 순매수 절댓값 기준 '한두 날 몰림' 정도(concentration)
    """
    tail = df.tail(days)
    r = (
        tail["개인순매매"].astype(float).values
        if "개인순매매" in tail.columns
        else [0.0] * len(tail)
    )
    f = tail["외국인순매매"].astype(float).values
    ins = tail["기관순매매"].astype(float).values
    n = len(f)
    foreign_pos_days = int((f > 0).sum())
    inst_pos_days = int((ins > 0).sum())
    both_pos_days = int(((f > 0) & (ins > 0)).sum())
    retail_pos_days = sum(1 for x in r if x > 0) if n else 0
    foreign_last = float(f[-1]) if n else 0.0
    inst_last = float(ins[-1]) if n else 0.0
    retail_last = float(r[-1]) if n else 0.0
    vol_last = float(tail["거래량"].astype(float).values[-1]) if n else 0.0
    retail_last_share = (retail_last / vol_last) if vol_last > 0 else 0.0
    foreign_last_share = (foreign_last / vol_last) if vol_last > 0 else 0.0
    abs_f = [abs(x) for x in f]
    total_abs = sum(abs_f)
    concentration = (max(abs_f) / total_abs) if total_abs > 0 else 0.0

    momentum = 0.0
    if n >= 5:
        last2 = float(f[-2] + f[-1])
        prev3 = float(f[-5] + f[-4] + f[-3])
        momentum = last2 - prev3

    # 막일 개인 매수세가 전일보다 약하고, 외인 매수세가 전일보다 강함 → 수급 전환(외인 주도) 힌트
    supply_handoff = bool(n >= 2 and r[-1] < r[-2] and f[-1] > f[-2])

    return {
        "foreign_positive_days": foreign_pos_days,
        "inst_positive_days": inst_pos_days,
        "both_positive_days": both_pos_days,
        "retail_positive_days": retail_pos_days,
        "foreign_last_day": foreign_last,
        "inst_last_day": inst_last,
        "retail_last_day": retail_last,
        "retail_last_share": float(retail_last_share),
        "foreign_last_share": float(foreign_last_share),
        "supply_handoff": supply_handoff,
        "foreign_momentum": momentum,
        "foreign_concentration": float(concentration),
    }


def close_window_pct(df: pd.DataFrame, days: int) -> float:
    """최근 `days`거래일 구간에서 첫 거래일 종가 대비 마지막 종가 등락률(%)."""
    t = df.tail(days)
    if len(t) < 2:
        return 0.0
    lo = float(t["종가"].iloc[0])
    hi = float(t["종가"].iloc[-1])
    if lo <= 0:
        return 0.0
    return (hi / lo - 1.0) * 100.0
