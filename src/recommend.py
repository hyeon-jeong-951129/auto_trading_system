"""유동성 상위 종목에 대해 수급·뉴스 신호를 합쳐 순위를 매깁니다."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Set

import FinanceDataReader as fdr
import pandas as pd

from src.data.naver_investors import close_window_pct, fetch_investor_daily, sum_flow
from src.data.naver_retail_top import fetch_individual_netbuy_codes
from src.data.news_signals import score_stock_news

_ETF_PATTERN = re.compile(
    r"KODEX|TIGER|ARIRANG|ACE|HANARO|KBSTAR|\bSOL\b|TIMEFOLIO|WOORI|KoAct|TREX|"
    r"RISE|PLUS|KTOP|파워|인버스|레버리지|ETN|ETF",
    re.I,
)
_EXCLUDE_NAME = re.compile(r"스팩|리츠|우\s*$|1우|2우|3우", re.I)


@dataclass
class Row:
    code: str
    name: str
    market: str
    foreign_net: float
    inst_net: float
    flow_combined: float
    rise_pct_window: float
    in_retail_top_widget: bool
    news_pos: int
    news_neg: int
    news_titles: List[str]
    score: float


def _load_universe(top_n: int) -> pd.DataFrame:
    df = fdr.StockListing("KRX")
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ"])].copy()
    df = df[~df["Name"].astype(str).str.contains(_ETF_PATTERN, na=False)]
    df = df[~df["Name"].astype(str).str.contains(_EXCLUDE_NAME, na=False)]
    df = df.sort_values("Volume", ascending=False).head(top_n)
    return df[["Code", "Name", "Market", "Volume"]]


def _one_ticker(
    code: str,
    name: str,
    market: str,
    flow_days: int,
    retail_codes: Set[str],
    fetch_news: bool,
    accumulation: bool,
    max_rise_pct: float,
) -> Optional[Row]:
    daily = fetch_investor_daily(code)
    if daily is None or len(daily) < flow_days:
        return None
    inst, frgn = sum_flow(daily, flow_days)
    combined = frgn + inst
    rise_pct = close_window_pct(daily, flow_days)
    in_retail = code in retail_codes
    if fetch_news:
        ns = score_stock_news(name)
        npos, nneg, ntitles = ns.pos_hits, ns.neg_hits, ns.sample_titles
    else:
        npos = nneg = 0
        ntitles = []

    if accumulation:
        # 외인·기관 둘 다 순매수, 단기 급등·개인 위젯 랭킹 제외 (늦은 붙기 후보 축소)
        if frgn <= 0 or inst <= 0:
            return None
        if rise_pct > max_rise_pct:
            return None
        if in_retail:
            return None
        news_adj = (npos - nneg) * 50_000
        # 같은 수급이면 최근 덜 오른 쪽을 약간 선호
        score = combined + news_adj - 80_000 * max(0.0, rise_pct - 3.0)
    else:
        news_adj = (npos - nneg) * 50_000
        retail_adj = 200_000 if in_retail else 0
        score = combined + news_adj + retail_adj

    return Row(
        code=code,
        name=name,
        market=market,
        foreign_net=frgn,
        inst_net=inst,
        flow_combined=combined,
        rise_pct_window=rise_pct,
        in_retail_top_widget=in_retail,
        news_pos=npos,
        news_neg=nneg,
        news_titles=ntitles,
        score=score,
    )


def run_screen(
    universe_size: int = 100,
    flow_days: int = 5,
    max_workers: int = 8,
    fetch_news: bool = True,
    accumulation: bool = False,
    max_rise_pct: float = 14.0,
) -> pd.DataFrame:
    universe = _load_universe(universe_size)
    retail_codes = fetch_individual_netbuy_codes()
    rows: List[Row] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {
            ex.submit(
                _one_ticker,
                str(r.Code).zfill(6),
                str(r.Name),
                str(r.Market),
                flow_days,
                retail_codes,
                fetch_news,
                accumulation,
                max_rise_pct,
            ): r
            for _, r in universe.iterrows()
        }
        for fut in as_completed(futs):
            try:
                row = fut.result()
                if row:
                    rows.append(row)
            except Exception:
                pass
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([row.__dict__ for row in rows])
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    return df


def _mkt_short(m: str) -> str:
    if m == "KOSPI":
        return "KOSPI"
    if m == "KOSDAQ":
        return "KOSDAQ"
    return (m or "")[:6]


def format_report(df: pd.DataFrame, head: int = 15, flow_days: int = 5) -> str:
    if df.empty:
        return "조회된 종목이 없습니다. 네트워크 또는 파싱 오류일 수 있습니다."
    lines = [
        "순위 | 코드 | 시장 | 종목명 | "
        f"{flow_days}일 외국인(주) | {flow_days}일 기관(주) | 합계 | "
        f"{flow_days}일종가% | 개인위젯 | 뉴스(+/-) | 요약 제목",
        "-" * 108,
    ]
    for rank, (_, r) in enumerate(df.head(head).iterrows(), start=1):
        t0 = (r["news_titles"][0][:40] + "…") if r["news_titles"] else "-"
        rp = r.get("rise_pct_window", 0.0)
        lines.append(
            f"{rank:2} | {r['code']} | {_mkt_short(r['market']):<6} | {r['name'][:10]:<10} | "
            f"{r['foreign_net']:>12,.0f} | {r['inst_net']:>12,.0f} | {r['flow_combined']:>12,.0f} | "
            f"{rp:>8.1f}% | {'Y' if r['in_retail_top_widget'] else ' '} | "
            f"{r['news_pos']}/{r['news_neg']} | {t0}"
        )
    return "\n".join(lines)


def format_telegram_summary(
    df: pd.DataFrame,
    head: int = 12,
    flow_days: int = 5,
    accumulation: bool = False,
    max_rise_pct: float = 14.0,
) -> str:
    """모바일용 짧은 요약 (투자 권유 아님 안내 포함)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if df.empty:
        return f"📊 수급·뉴스 스크리너\n{now}\n\n데이터 없음 (네트워크/파싱 오류 가능)"
    mode = (
        f"누적매집: 외·기 동반순매수, {flow_days}일 종가 ≤{max_rise_pct}%, 개인위젯 제외"
        if accumulation
        else f"{flow_days}일 외인+기관 순매수 합 상위"
    )
    lines = [
        "📊 수급·뉴스 스크리너 (연구용, 투자권유 아님)",
        now,
        mode + f" {head}종목",
        "",
    ]
    for rank, (_, r) in enumerate(df.head(head).iterrows(), start=1):
        nm = str(r["name"])[:9]
        fn = r["foreign_net"] / 10_000
        inn = r["inst_net"] / 10_000
        sm = r["flow_combined"] / 10_000
        ret = "개인위젯" if r["in_retail_top_widget"] else ""
        rp = float(r.get("rise_pct_window", 0.0))
        lines.append(
            f"{rank}. {r['code']} {nm}\n"
            f"   합 {sm:,.0f}만주 (외 {fn:,.0f} / 기 {inn:,.0f}) "
            f"{flow_days}일종가 {rp:+.1f}% 뉴스±{r['news_pos']}/{r['news_neg']} {ret}"
        )
    lines.append("")
    lines.append("출처: 네이버 투자자동향, Google News RSS")
    return "\n".join(lines)
