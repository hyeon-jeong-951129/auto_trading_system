"""Telegram Bot API sendMessage (텍스트 분할 전송)."""

from __future__ import annotations

from typing import List

import requests

TELEGRAM_MAX = 4096


def chunk_text(text: str, limit: int = TELEGRAM_MAX - 64) -> List[str]:
    """길이 제한 내에서 가능하면 줄바꿈에서 잘라 여러 메시지로 보냅니다."""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            br = text.rfind("\n", start, end)
            if br > start:
                end = br + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def send_telegram_chunks(text: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = (text or "").strip()
    if not body:
        body = "📊 스크리너: 전송할 본문이 비어 있습니다."
    for part in chunk_text(body):
        part = part.strip()
        if not part:
            continue
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": part, "disable_web_page_preview": True},
            timeout=60,
        )
        if r.status_code == 401:
            raise RuntimeError(
                "Telegram API 401 (Unauthorized): 봇 토큰이 잘못됐거나 예시 값입니다. "
                ".env 의 TELEGRAM_BOT_TOKEN 을 BotFather가 준 전체 문자열로 바꾸세요(따옴표·공백 없이)."
            )
        if r.status_code == 400:
            try:
                desc = (r.json().get("description") or "").lower()
            except Exception:
                desc = ""
            if "chat not found" in desc:
                raise RuntimeError(
                    "Telegram 400 chat not found: TELEGRAM_CHAT_ID 가 틀렸거나 봇이 당신과 대화한 적이 없습니다. "
                    "본인이 만든 봇에게 /start 보낸 뒤, @userinfobot 의 Id 숫자만 .env 에 넣으세요."
                )
        if not r.ok:
            raise RuntimeError(f"Telegram API {r.status_code}: {r.text[:500]}")
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API 오류: {data}")
