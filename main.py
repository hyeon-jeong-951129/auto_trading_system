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

from src.recommend import format_report, format_telegram_summary, run_screen


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
    args = p.parse_args()

    if args.telegram:
        try:
            from dotenv import load_dotenv
        except ImportError:
            load_dotenv = None  # type: ignore
        if load_dotenv:
            load_dotenv(ROOT / ".env")

    df = run_screen(
        universe_size=args.universe,
        flow_days=args.flow_days,
        max_workers=args.workers,
        fetch_news=not args.no_news,
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

        msg = format_telegram_summary(df, head=args.top, flow_days=args.flow_days)
        send_telegram_chunks(msg, token, chat_id)
        print("Telegram 전송 완료", file=sys.stderr)
        print(msg)
    else:
        print(format_report(df, head=args.top, flow_days=args.flow_days))


if __name__ == "__main__":
    main()
