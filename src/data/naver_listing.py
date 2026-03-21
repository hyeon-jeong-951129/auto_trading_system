"""KRX JSON이 막힐 때(해외 IP·일시 장애) 네이버 거래량 순위로 유니버스 구성."""

from __future__ import annotations

import re
from typing import List

import pandas as pd
import requests
from bs4 import BeautifulSoup

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
    }
)


def _parse_int(s: str) -> int:
    s = re.sub(r"[^\d]", "", str(s))
    return int(s) if s else 0


def fetch_volume_ranking_naver(sosok: int, market: str) -> pd.DataFrame:
    """
    sosok=0: KOSPI, sosok=1: KOSDAQ 거래량 상위 페이지.
    """
    url = f"https://finance.naver.com/sise/sise_quant.naver?sosok={sosok}"
    r = _SESSION.get(url, timeout=25)
    r.encoding = "euc-kr"
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    tb = soup.select_one("table.type_2")
    if tb is None:
        return pd.DataFrame(columns=["Code", "Name", "Market", "Volume"])
    rows: List[dict] = []
    for tr in tb.select("tr"):
        a = tr.select_one('a[href*="/item/main.naver?code="]')
        if not a or "href" not in a.attrs:
            continue
        m = re.search(r"code=(\d{6})", a["href"])
        if not m:
            continue
        code = m.group(1)
        name = a.get_text(strip=True)
        tds = tr.select("td")
        vol = 0
        if len(tds) >= 6:
            vol = _parse_int(tds[5].get_text())
        rows.append({"Code": code, "Name": name, "Market": market, "Volume": vol})
    if not rows:
        return pd.DataFrame(columns=["Code", "Name", "Market", "Volume"])
    return pd.DataFrame(rows)
