"""유동성 상위 종목에 대해 수급·뉴스 신호를 합쳐 순위를 매깁니다."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Set

import FinanceDataReader as fdr
import pandas as pd

from src.data.naver_investors import (
    close_window_pct,
    fetch_investor_daily,
    flow_quality_metrics,
    sum_flow,
)
from src.data.naver_listing import fetch_volume_ranking_naver
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
    foreign_positive_days: int = 0
    both_positive_days: int = 0
    foreign_last_day: float = 0.0
    inst_last_day: float = 0.0
    foreign_momentum: float = 0.0
    foreign_concentration: float = 0.0
    retail_last_day: float = 0.0
    retail_last_share: float = 0.0
    foreign_last_share: float = 0.0
    supply_handoff: bool = False
    flow_window_volume: float = 0.0
    priority_score: float = 0.0
    priority_tier: str = "-"


def _priority_meta(
    accumulation: bool,
    score: float,
    flow_days: int,
    foreign_net: float,
    inst_net: float,
    both_positive_days: int,
    foreign_positive_days: int,
    rise_pct: float,
    max_rise_pct: float,
    flow_window_volume: float,
    foreign_concentration: float,
    retail_last_share: float,
    supply_handoff: bool,
) -> tuple[float, str]:
    """
    자동매매·알림 우선순위용 점수(정렬·텔레그램 컷).
    - 유동성(구간 거래량): 체결·슬리피지 완화
    - 지속성: 동반·외인 양수일 비율
    - 균형: 외인·기관 규모가 한쪽으로만 쏠리지 않을수록 가산
    - 급등 천장과 거리: 단기 과열에 가까울수록 순위 후퇴
    - 집중도: 스파이크성 외인 매수는 순위에서 추가 감점
    """
    liq = 2_500.0 * math.log1p(max(flow_window_volume, 0.0))
    fd = max(int(flow_days), 1)

    if not accumulation:
        p = score + liq
        if supply_handoff:
            p += 180_000.0
        return p, "-"

    persist = 450_000.0 * (both_positive_days / fd) + 180_000.0 * (foreign_positive_days / fd)

    if foreign_net > 0 and inst_net > 0:
        bal = min(foreign_net, inst_net) / max(foreign_net, inst_net)
    else:
        bal = 0.0
    balance_bonus = 400_000.0 * bal

    room = max(0.0, (max_rise_pct - rise_pct) / max(max_rise_pct, 1e-6))
    chase_penalty_relief = 120_000.0 * room

    conc_pen = 150_000.0 * max(0.0, foreign_concentration - 0.45)
    # 개인 막일 매수 비중이 클수록(=crowded) 우선순위에서 추가 감점 (수급전환 신호면 완화)
    retail_pen = 420_000.0 * max(0.0, retail_last_share - 0.18)
    if supply_handoff:
        retail_pen *= 0.35
    handoff_bonus = 280_000.0 if supply_handoff else 0.0

    p = (
        score
        + liq
        + persist
        + balance_bonus
        + chase_penalty_relief
        + handoff_bonus
        - conc_pen
        - retail_pen
    )

    if both_positive_days >= fd - 1 and foreign_positive_days >= fd - 1 and bal >= 0.12:
        tier = "S"
    elif both_positive_days >= max(2, fd - 2) and foreign_positive_days >= 3:
        tier = "A"
    else:
        tier = "B"

    return p, tier


def _filter_universe_df(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if df.empty:
        return df
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ"])].copy()
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    df = df[~df["Name"].astype(str).str.contains(_ETF_PATTERN, na=False)]
    df = df[~df["Name"].astype(str).str.contains(_EXCLUDE_NAME, na=False)]
    df = df.sort_values("Volume", ascending=False).head(top_n)
    return df[["Code", "Name", "Market", "Volume"]]


def _load_universe(top_n: int) -> pd.DataFrame:
    """
    기본: FinanceDataReader(KRX). KRX가 JSON/HTML 오류(해외 IP·장애)면 네이버 거래량 순위로 대체.
    """
    df: Optional[pd.DataFrame] = None
    try:
        df = fdr.StockListing("KRX")
    except Exception:
        df = None
    if df is None or len(df) == 0:
        k0 = fetch_volume_ranking_naver(0, "KOSPI")
        k1 = fetch_volume_ranking_naver(1, "KOSDAQ")
        df = pd.concat([k0, k1], ignore_index=True)
        if df.empty:
            return df
        df = df.sort_values("Volume", ascending=False)
        df = df.drop_duplicates(subset=["Code"], keep="first")
    return _filter_universe_df(df, top_n)


def _one_ticker(
    code: str,
    name: str,
    market: str,
    flow_days: int,
    retail_codes: Set[str],
    fetch_news: bool,
    accumulation: bool,
    max_rise_pct: float,
    min_foreign_positive_days: int,
    max_foreign_concentration: float,
    min_both_positive_days: int,
    min_foreign_momentum: float,
    min_rise_pct: float,
    retail_crowded_share: float,
    min_foreign_last_share: float,
    min_foreign_vs_retail: float,
) -> Optional[Row]:
    daily = fetch_investor_daily(code)
    if daily is None or len(daily) < flow_days:
        return None
    inst, frgn = sum_flow(daily, flow_days)
    combined = frgn + inst
    rise_pct = close_window_pct(daily, flow_days)
    fq = flow_quality_metrics(daily, flow_days)
    fp_days = int(fq["foreign_positive_days"])
    bp_days = int(fq["both_positive_days"])
    f_last = float(fq["foreign_last_day"])
    i_last = float(fq["inst_last_day"])
    r_last = float(fq.get("retail_last_day", 0.0))
    r_share = float(fq.get("retail_last_share", 0.0))
    f_share_last = float(fq.get("foreign_last_share", 0.0))
    supply_handoff = bool(fq.get("supply_handoff", False))
    f_mom = float(fq["foreign_momentum"])
    f_conc = float(fq["foreign_concentration"])
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
        if rise_pct < min_rise_pct:
            return None
        if in_retail:
            return None
        # 막 거래일 외인 순매수 양수, N일 중 외인 순매수 양수인 날이 일정 횟수 이상
        if f_last <= 0:
            return None
        if fp_days < min_foreign_positive_days:
            return None
        # 외인-기관 동반 양수 흐름이 너무 약하면 제외(보수 필터)
        if bp_days < min_both_positive_days:
            return None
        # 막일 기관 순매수도 양수여야 "지속 매수"로 판단
        if i_last <= 0:
            return None
        # 막일 개인 순매수가 크고(거래량 대비), 외인 막일 매수가 충분히 크지 않으면 제외
        # 단, 외인이 거래량 대비 일정 비율 이상 매수이거나 개인 매수 규모 대비 외인이 충분하면 예외
        if r_last > 0 and r_share >= retail_crowded_share:
            strong_foreign = f_share_last >= min_foreign_last_share or (
                f_last >= min_foreign_vs_retail * r_last
            )
            if not strong_foreign:
                return None
        # 최근 매수 모멘텀이 꺾였으면 제외
        if f_mom < min_foreign_momentum:
            return None
        # 한두 날에 외인 매수가 몰린 뒤 소진되는 패턴 완화 제외
        if f_conc > max_foreign_concentration and fp_days < 4:
            return None
        news_adj = (npos - nneg) * 50_000
        # 같은 수급이면 최근 덜 오른 쪽을 약간 선호
        score = combined + news_adj - 80_000 * max(0.0, rise_pct - 3.0)
        # 후반(최근 2일) 외인 순매수가 전반(그 앞 3일)보다 강할수록 가산
        mom_clamped = max(min(f_mom, 5_000_000.0), -2_500_000.0)
        score += 0.08 * mom_clamped
        # 일별 외인 매수가 특정 하루에 과도하게 몰릴수록 감점 (스파이크성)
        score -= 180_000.0 * max(0.0, f_conc - 0.52)
        if supply_handoff:
            score += 140_000.0
    else:
        news_adj = (npos - nneg) * 50_000
        retail_adj = 200_000 if in_retail else 0
        score = combined + news_adj + retail_adj

    vol_sum = float(daily.tail(flow_days)["거래량"].fillna(0).sum())
    pri, tier = _priority_meta(
        accumulation,
        score,
        flow_days,
        frgn,
        inst,
        bp_days,
        fp_days,
        rise_pct,
        max_rise_pct,
        vol_sum,
        f_conc,
        r_share,
        supply_handoff,
    )

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
        foreign_positive_days=fp_days,
        both_positive_days=bp_days,
        foreign_last_day=f_last,
        inst_last_day=i_last,
        foreign_momentum=f_mom,
        foreign_concentration=f_conc,
        retail_last_day=r_last,
        retail_last_share=r_share,
        foreign_last_share=f_share_last,
        supply_handoff=supply_handoff,
        flow_window_volume=vol_sum,
        priority_score=pri,
        priority_tier=tier,
    )


def run_screen(
    universe_size: int = 100,
    flow_days: int = 5,
    max_workers: int = 8,
    fetch_news: bool = True,
    accumulation: bool = False,
    max_rise_pct: float = 14.0,
    min_foreign_positive_days: int = 3,
    max_foreign_concentration: float = 0.82,
    min_both_positive_days: int = 2,
    min_foreign_momentum: float = 0.0,
    min_rise_pct: float = 0.0,
    retail_crowded_share: float = 0.28,
    min_foreign_last_share: float = 0.012,
    min_foreign_vs_retail: float = 0.35,
    sort_by: str = "priority",
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
                min_foreign_positive_days,
                max_foreign_concentration,
                min_both_positive_days,
                min_foreign_momentum,
                min_rise_pct,
                retail_crowded_share,
                min_foreign_last_share,
                min_foreign_vs_retail,
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
    sort_key = "score" if sort_by.strip().lower() == "score" else "priority_score"
    df = df.sort_values(sort_key, ascending=False).reset_index(drop=True)
    return df


def filter_for_telegram_by_score(
    df: pd.DataFrame,
    min_score: Optional[float] = None,
    min_score_ratio: Optional[float] = None,
    rank_column: str = "priority_score",
) -> tuple[pd.DataFrame, Optional[float]]:
    """
    텔레그램 전송 전 순위용 점수 기준 필터(콘솔·CSV 원본은 그대로).
    기본은 `priority_score`(자동매매 우선순위). 없으면 `score` 사용.
    임계값은 절대 하한(`min_score`)과 당일 최고점 대비 비율(`min_score_ratio`)을
    둘 다 쓰면 더 높은 쪽(보수적)으로 맞춤.
    """
    if df.empty or (min_score is None and min_score_ratio is None):
        return df.copy(), None
    col = rank_column if rank_column in df.columns else "score"
    mx = float(df[col].max())
    parts: List[float] = []
    if min_score_ratio is not None:
        parts.append(mx * float(min_score_ratio))
    if min_score is not None:
        parts.append(float(min_score))
    if not parts:
        return df.copy(), None
    thr = max(parts)
    out = df[df[col] >= thr].copy().reset_index(drop=True)
    return out, thr


def _mkt_short(m: str) -> str:
    if m == "KOSPI":
        return "KOSPI"
    if m == "KOSDAQ":
        return "KOSDAQ"
    return (m or "")[:6]


def format_report(
    df: pd.DataFrame,
    head: int = 15,
    flow_days: int = 5,
    accumulation: bool = False,
) -> str:
    if df.empty:
        return "조회된 종목이 없습니다. 네트워크 또는 파싱 오류일 수 있습니다."
    if accumulation:
        lines = [
            "순위 | 코드 | 시장 | 종목명 | "
            f"{flow_days}일 외국인(주) | {flow_days}일 기관(주) | 합계 | "
            f"{flow_days}일종가% | 티어 | 외인양수일 | 동반양수일 | 막일외인 | 막일기관 | 집중도 | 개인위젯 | 뉴스(+/-) | 요약 제목",
            "-" * 160,
        ]
    else:
        lines = [
            "순위 | 코드 | 시장 | 종목명 | "
            f"{flow_days}일 외국인(주) | {flow_days}일 기관(주) | 합계 | "
            f"{flow_days}일종가% | 개인위젯 | 뉴스(+/-) | 요약 제목",
            "-" * 108,
        ]
    for rank, (_, r) in enumerate(df.head(head).iterrows(), start=1):
        t0 = (r["news_titles"][0][:40] + "…") if r["news_titles"] else "-"
        rp = r.get("rise_pct_window", 0.0)
        if accumulation:
            fpd = int(r.get("foreign_positive_days", 0))
            bpd = int(r.get("both_positive_days", 0))
            fl = float(r.get("foreign_last_day", 0.0))
            il = float(r.get("inst_last_day", 0.0))
            fc = float(r.get("foreign_concentration", 0.0))
            pt = str(r.get("priority_tier", "-"))[:1]
            lines.append(
                f"{rank:2} | {r['code']} | {_mkt_short(r['market']):<6} | {r['name'][:10]:<10} | "
                f"{r['foreign_net']:>12,.0f} | {r['inst_net']:>12,.0f} | {r['flow_combined']:>12,.0f} | "
                f"{rp:>8.1f}% | {pt:>3} | {fpd:>8} | {bpd:>8} | {fl:>10,.0f} | {il:>10,.0f} | {fc:>6.2f} | "
                f"{'Y' if r['in_retail_top_widget'] else ' '} | "
                f"{r['news_pos']}/{r['news_neg']} | {t0}"
            )
        else:
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
    min_foreign_positive_days: int = 3,
    max_foreign_concentration: float = 0.82,
    min_both_positive_days: int = 2,
    min_foreign_momentum: float = 0.0,
    min_rise_pct: float = 0.0,
    score_floor: Optional[float] = None,
) -> str:
    """모바일용 짧은 요약 (투자 권유 아님 안내 포함)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if df.empty:
        cut = f"\n(우선순위점수≥{score_floor:,.0f} 미만 제외됨)" if score_floor is not None else ""
        if accumulation:
            return (
                f"📊 수급·뉴스 스크리너\n{now}\n\n"
                f"조건 충족 종목 없음 (네트워크/파싱 오류 가능){cut}"
            )
        return f"📊 수급·뉴스 스크리너\n{now}\n\n데이터 없음 (네트워크/파싱 오류 가능){cut}"
    score_note = f", 우선순위≥{score_floor:,.0f}" if score_floor is not None else ""
    mode = (
        f"누적매집: 외·기 동반순매수, 막일외인+, 외인양수≥{min_foreign_positive_days}일/{flow_days}일, "
        f"동반양수≥{min_both_positive_days}일, 모멘텀≥{min_foreign_momentum:,.0f}, "
        f"막일기관+, 종가 {min_rise_pct:+.1f}~{max_rise_pct:.1f}%, "
        f"집중도>{max_foreign_concentration:.0%}·양수일<4 제외, 개인위젯 제외, 정렬=우선순위{score_note}"
        if accumulation
        else f"{flow_days}일 외인+기관 순매수 합 상위, 정렬=우선순위{score_note}"
    )
    lines = [
        "📊 수급·뉴스 스크리너 (연구용, 투자권유 아님)",
        now,
        mode + f" {head}종목",
        "",
    ]
    for rank, (_, r) in enumerate(df.head(head).iterrows(), start=1):
        nm = str(r["name"])[:9]
        pt = str(r.get("priority_tier", "-"))[:1]
        fn = r["foreign_net"] / 10_000
        inn = r["inst_net"] / 10_000
        sm = r["flow_combined"] / 10_000
        ret = "개인위젯" if r["in_retail_top_widget"] else ""
        rp = float(r.get("rise_pct_window", 0.0))
        if accumulation:
            fpd = int(r.get("foreign_positive_days", 0))
            fc = float(r.get("foreign_concentration", 0.0))
            fm = float(r.get("foreign_momentum", 0.0)) / 10_000.0
            fq_note = f" 외인양수{fpd}일 집중{fc:.2f} 모멘텀{fm:+,.0f}만"
            if bool(r.get("supply_handoff", False)):
                fq_note += " 수급전환"
        else:
            fq_note = ""
        lines.append(
            f"{rank}. [{pt}] {r['code']} {nm}\n"
            f"   합 {sm:,.0f}만주 (외 {fn:,.0f} / 기 {inn:,.0f}) "
            f"{flow_days}일종가 {rp:+.1f}% 뉴스±{r['news_pos']}/{r['news_neg']} {ret}{fq_note}"
        )
    lines.append("")
    lines.append("출처: 네이버 투자자동향, Google News RSS")
    return "\n".join(lines)
