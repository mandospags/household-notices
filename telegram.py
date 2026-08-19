"""Send-only Telegram delivery for the daily digest, via the shared "Home"
bot (token + chat_id already provisioned in homelab-mcp; this repo only
ever calls sendMessage, never getUpdates, which is what makes holding the
same token safe - no listener, no command handling, no shared risk).

Plain text only (no parse_mode) - avoids the HTML-escaping trap where an
unescaped <, >, or & silently gets the whole message rejected by Telegram.

No length handling for the 4096-char cap: an oversized digest hits
Telegram's own 400 rejection, which surfaces as a raised exception (loud)
rather than silently truncating (quiet and wrong).

Failure is raised via the response body, not `raise_for_status()` - that
method's message embeds the request URL, which contains the bot token,
and digest.py prints exception text on failure.
"""

import os

import requests

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    resp = requests.post(
        TELEGRAM_API_URL.format(token=token),
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"telegram sendMessage failed: {resp.status_code} {resp.text[:200]}")
