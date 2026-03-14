"""Shared KV helpers with Redis support for API handlers."""
from __future__ import annotations

import json
import os
from typing import Any

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None


REDIS_URL = (os.getenv("REDIS_URL", "") or "").strip()
_redis_client = None


def _get_client():
    global _redis_client
    if not REDIS_URL or redis is None:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        _redis_client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=True,
        )
        _redis_client.ping()
    except Exception:
        _redis_client = None
    return _redis_client


def redis_available() -> bool:
    return _get_client() is not None


def get_json(key: str) -> Any:
    c = _get_client()
    if not c:
        return None
    try:
        raw = c.get(key)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def set_json(key: str, value: Any, ttl_sec: int | None = None) -> bool:
    c = _get_client()
    if not c:
        return False
    try:
        raw = json.dumps(value, ensure_ascii=False)
        if ttl_sec and ttl_sec > 0:
            c.setex(key, int(ttl_sec), raw)
        else:
            c.set(key, raw)
        return True
    except Exception:
        return False


def delete_key(key: str) -> bool:
    c = _get_client()
    if not c:
        return False
    try:
        c.delete(key)
        return True
    except Exception:
        return False
