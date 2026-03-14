"""Monitoring helpers for API handlers (Sentry + Telegram alerts)."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

try:
    import sentry_sdk  # type: ignore
except Exception:  # pragma: no cover
    sentry_sdk = None


SENTRY_DSN = (os.getenv("SENTRY_DSN", "") or "").strip()
ALERT_BOT_TOKEN = (os.getenv("ALERT_BOT_TOKEN", "") or "").strip()
ALERT_CHAT_ID = (os.getenv("ALERT_CHAT_ID", "") or "").strip()
_last_alert_at = {}

if sentry_sdk and SENTRY_DSN:
    try:
        sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1)
    except Exception:
        pass


def capture(exc: Exception, where: str, extra: dict | None = None):
    if sentry_sdk:
        try:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("component", where)
                if extra:
                    for k, v in extra.items():
                        scope.set_extra(str(k), v)
                sentry_sdk.capture_exception(exc)
        except Exception:
            pass
    alert(where, str(exc))


def alert(where: str, message: str, min_interval_sec: int = 120):
    if not ALERT_BOT_TOKEN or not ALERT_CHAT_ID:
        return
    now = int(time.time())
    k = f"{where}:{message[:120]}"
    prev = _last_alert_at.get(k, 0)
    if now - prev < min_interval_sec:
        return
    _last_alert_at[k] = now
    text = f"⚠️ API error [{where}]\n{message[:2000]}"
    url = f"https://api.telegram.org/bot{ALERT_BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": ALERT_CHAT_ID,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass
