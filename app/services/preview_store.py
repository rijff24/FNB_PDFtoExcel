import base64
import json
import os
from typing import Any

_LOCAL_SESSIONS: dict[str, dict[str, Any]] = {}
_REDIS_CLIENT = None
_KEY_PREFIX = "preview:session:"


def _redis_client():
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        _REDIS_CLIENT = False
        return None
    try:
        import redis

        _REDIS_CLIENT = redis.Redis.from_url(redis_url, decode_responses=True)
        _REDIS_CLIENT.ping()
        return _REDIS_CLIENT
    except Exception:  # noqa: BLE001
        _REDIS_CLIENT = False
        return None


def _ttl_seconds() -> int:
    raw = os.getenv("PREVIEW_SESSION_TTL_SECONDS", "7200").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 7200
    return max(300, value)


def save_preview_session(session_id: str, pdf_bytes: bytes, transactions: list[dict[str, object]]) -> None:
    payload = {
        "pdf_b64": base64.b64encode(pdf_bytes).decode("ascii"),
        "transactions": transactions,
    }
    redis_client = _redis_client()
    if redis_client:
        redis_client.setex(f"{_KEY_PREFIX}{session_id}", _ttl_seconds(), json.dumps(payload))
        return
    _LOCAL_SESSIONS[session_id] = payload


def get_preview_session(session_id: str) -> dict[str, Any] | None:
    redis_client = _redis_client()
    if redis_client:
        raw = redis_client.get(f"{_KEY_PREFIX}{session_id}")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return {
                "pdf_bytes": base64.b64decode(payload.get("pdf_b64", "")),
                "transactions": payload.get("transactions") or [],
            }
        except Exception:  # noqa: BLE001
            return None
    payload = _LOCAL_SESSIONS.get(session_id)
    if not payload:
        return None
    return {
        "pdf_bytes": base64.b64decode(payload.get("pdf_b64", "")),
        "transactions": payload.get("transactions") or [],
    }


def update_preview_transactions(session_id: str, transactions: list[dict[str, object]]) -> bool:
    redis_client = _redis_client()
    if redis_client:
        key = f"{_KEY_PREFIX}{session_id}"
        raw = redis_client.get(key)
        if not raw:
            return False
        payload = json.loads(raw)
        payload["transactions"] = transactions
        redis_client.setex(key, _ttl_seconds(), json.dumps(payload))
        return True
    payload = _LOCAL_SESSIONS.get(session_id)
    if not payload:
        return False
    payload["transactions"] = transactions
    return True
