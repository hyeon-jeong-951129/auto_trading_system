#!/usr/bin/env python3
"""CLI: 유동성 상위 종목 수급·뉴스 스크리닝."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.recommend import (
    filter_for_telegram_by_score,
    format_report,
    format_telegram_summary,
    run_screen,
)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def main() -> None:
    p = argparse.ArgumentParser(
        description="거래대상(연구용): 외국인·기관 순매수 합 + 뉴스 키워드 + 네이버 개인 매매 위젯 교차."
    )
    p.add_argument(
        "--universe",
        type=int,
        default=80,
        help="거래량 상위 N종목만 스캔 (기본 80)",
    )
    p.add_argument(
        "--flow-days",
        type=int,
        default=5,
        help="순매수 합산 거래일 수 (기본 5)",
    )
    p.add_argument(
        "--top",
        type=int,
        default=20,
        help="출력 상위 개수",
    )
    p.add_argument(
        "--no-news",
        action="store_true",
        help="Google 뉴스 RSS 조회 생략 (속도 우선)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=8,
        help="동시 요청 수 (기본 8, 과도하면 차단될 수 있음)",
    )
    p.add_argument(
        "--csv",
        type=str,
        default="",
        help="전체 결과를 CSV로 저장할 경로 (선택)",
    )
    p.add_argument(
        "--telegram",
        action="store_true",
        help="요약을 텔레그램으로 전송 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)",
    )
    p.add_argument(
        "--telegram-test",
        action="store_true",
        help="스크리너 없이 '연결 테스트' 메시지만 텔레그램으로 보냄",
    )
    p.add_argument(
        "--test",
        action="store_true",
        help="--telegram-test 와 동일 (연결만 확인)",
    )
    p.add_argument(
        "--accumulation",
        action="store_true",
        help="외인·기관 동반 순매수만, 단기 급등/개인위젯 랭킹 종목 제외 (늦은 붙기 후보 축소)",
    )
    p.add_argument(
        "--max-rise",
        type=float,
        default=14.0,
        help="--accumulation 일 때 최근 flow-days 구간 종가 등락률(%%) 상한 (기본 14)",
    )
    p.add_argument(
        "--min-foreign-pos-days",
        type=int,
        default=3,
        help="--accumulation 일 때 최근 flow-days 중 외국인 순매수 양수인 날 최소 횟수 (기본 3)",
    )
    p.add_argument(
        "--max-foreign-concentration",
        type=float,
        default=0.82,
        help="--accumulation 일 때 외국인 일별 |순매수| 비중 상한(스파이크 필터, 기본 0.82)",
    )
    p.add_argument(
        "--min-both-positive-days",
        type=int,
        default=2,
        help="--accumulation 일 때 외국인·기관 동반 순매수(양수) 일 최소 횟수 (기본 2)",
    )
    p.add_argument(
        "--min-foreign-momentum",
        type=float,
        default=0.0,
        help="--accumulation 일 때 외인 모멘텀(최근2일 합 - 전반3일 합) 하한(주 단위, 기본 0)",
    )
    p.add_argument(
        "--min-rise-pct",
        type=float,
        default=0.0,
        help="--accumulation 일 때 최근 flow-days 종가 등락률 하한(% , 기본 0)",
    )
    p.add_argument(
        "--retail-crowded-share",
        type=float,
        default=0.28,
        help="--accumulation 일 때 막일 개인순매수/거래량이 이 비율 이상이면 '군중 매수' 검사 (기본 0.28)",
    )
    p.add_argument(
        "--min-foreign-last-share",
        type=float,
        default=0.012,
        help="--accumulation 일 때 군중 매수 구간 예외: 막일 외인순매수/거래량 ≥ 이 값 (기본 0.012=1.2%%)",
    )
    p.add_argument(
        "--min-foreign-vs-retail",
        type=float,
        default=0.35,
        help="--accumulation 일 때 군중 매수 구간 예외: 막일 외인순매수 ≥ 개인순매수×이 비율 (기본 0.35)",
    )
    p.add_argument(
        "--max-last-day-change",
        type=float,
        default=8.0,
        help="--accumulation 일 때 막일 종가 등락률(%%) 상한, 초과 시 제외(추격·과열, 기본 8)",
    )
    p.add_argument(
        "--min-last-day-change",
        type=float,
        default=-4.0,
        help="--accumulation 일 때 막일 종가 등락률(%%) 하한, 미만 시 제외(급락 약세, 기본 -4)",
    )
    p.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="--telegram 일 때만: 우선순위점수(priority_score) 절대 하한. 미지정이면 절대 컷 없음",
    )
    p.add_argument(
        "--min-score-ratio",
        type=float,
        default=None,
        help="--telegram 일 때만: 당일 결과 중 최고 우선순위점수(priority_score) 대비 비율(0~1). 예: 0.45면 1위 대비 45%% 미만 제외",
    )
    p.add_argument(
        "--sort-by",
        choices=("priority", "score"),
        default="priority",
        help="결과 정렬: priority=자동매매 우선순위점수(기본), score=내부 raw score",
    )
    args = p.parse_args()

    if args.min_score_ratio is not None and not (0 < args.min_score_ratio <= 1):
        p.error("--min-score-ratio 는 0 초과 1 이하여야 합니다.")
    if not 0.0 <= args.retail_crowded_share <= 1.0:
        p.error("--retail-crowded-share 는 0~1 사이여야 합니다.")
    if args.min_foreign_last_share < 0.0 or args.min_foreign_last_share > 1.0:
        p.error("--min-foreign-last-share 는 0~1 사이여야 합니다.")
    if args.min_foreign_vs_retail < 0.0:
        p.error("--min-foreign-vs-retail 은 0 이상이어야 합니다.")
    if args.min_last_day_change >= args.max_last_day_change:
        p.error("--min-last-day-change 는 --max-last-day-change 보다 작아야 합니다.")

    telegram_test = args.telegram_test or args.test

    if args.telegram or telegram_test:
        _load_dotenv()

    if telegram_test:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            print(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 필요합니다. "
                "프로젝트 루트에 .env 파일을 만들고 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 를 넣으세요.",
                file=sys.stderr,
            )
            sys.exit(1)
        from src.notify.telegram import send_telegram_chunks

        send_telegram_chunks(
            "🔔 연결 테스트\n\nauto_trading_system 봇이 정상적으로 메시지를 보냈습니다.",
            token,
            chat_id,
        )
        print("Telegram 테스트 전송 완료. 앱에서 메시지를 확인하세요.", file=sys.stderr)
        return

    df = run_screen(
        universe_size=args.universe,
        flow_days=args.flow_days,
        max_workers=args.workers,
        fetch_news=not args.no_news,
        accumulation=args.accumulation,
        max_rise_pct=args.max_rise,
        min_foreign_positive_days=args.min_foreign_pos_days,
        max_foreign_concentration=args.max_foreign_concentration,
        min_both_positive_days=args.min_both_positive_days,
        min_foreign_momentum=args.min_foreign_momentum,
        min_rise_pct=args.min_rise_pct,
        retail_crowded_share=args.retail_crowded_share,
        min_foreign_last_share=args.min_foreign_last_share,
        min_foreign_vs_retail=args.min_foreign_vs_retail,
        max_last_day_change_pct=args.max_last_day_change,
        min_last_day_change_pct=args.min_last_day_change,
        sort_by=args.sort_by,
    )
    if args.csv and not df.empty:
        # 리스트 컬럼은 CSV에 깨질 수 있어 제외
        out = df.drop(columns=["news_titles"], errors="ignore")
        out.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"CSV 저장: {args.csv}", file=sys.stderr)

    if args.telegram:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            print(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 필요합니다.",
                file=sys.stderr,
            )
            sys.exit(1)
        from src.notify.telegram import send_telegram_chunks

        df_tg, score_floor = filter_for_telegram_by_score(
            df,
            min_score=args.min_score,
            min_score_ratio=args.min_score_ratio,
        )
        msg = format_telegram_summary(
            df_tg,
            head=args.top,
            flow_days=args.flow_days,
            accumulation=args.accumulation,
            max_rise_pct=args.max_rise,
            min_foreign_positive_days=args.min_foreign_pos_days,
            max_foreign_concentration=args.max_foreign_concentration,
            min_both_positive_days=args.min_both_positive_days,
            min_foreign_momentum=args.min_foreign_momentum,
            min_rise_pct=args.min_rise_pct,
            max_last_day_change_pct=args.max_last_day_change,
            min_last_day_change_pct=args.min_last_day_change,
            score_floor=score_floor,
        )
        send_telegram_chunks(msg, token, chat_id)
        print("Telegram 전송 완료", file=sys.stderr)
        print(msg)
    else:
        print(format_report(df, head=args.top, flow_days=args.flow_days, accumulation=args.accumulation))


if __name__ == "__main__":
    main()
