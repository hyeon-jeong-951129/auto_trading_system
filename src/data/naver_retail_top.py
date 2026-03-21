"""네이버 시세 — 개인 매매 상위(소형 위젯) 티커 집합."""

from __future__ import annotations

import re
from io import StringIO
from typing import Set

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


def fetch_individual_netbuy_codes() -> Set[str]:
    """
    `investor_gubun=8000` 페이지에 노출되는 소형 랭킹의 종목코드.
    전체 시장 개인 순매수 전종목이 아니라, 참고용 탑 리스트.
    """
    url = "https://finance.naver.com/sise/sise_deal_rank.naver?investor_gubun=8000"
    r = _SESSION.get(url, timeout=15)
    r.encoding = "euc-kr"
    if r.status_code != 200:
        return set()
    codes: Set[str] = set()
    for m in re.finditer(r"item/main\.naver\?code=(\d{6})", r.text):
        codes.add(m.group(1))
    try:
        dfs = pd.read_html(StringIO(r.text))
    except ValueError:
        return codes
    for d in dfs:
        for cell in d.astype(str).values.flatten():
            if isinstance(cell, str) and cell.isdigit() and len(cell) == 6:
                codes.add(cell)
    return codes
