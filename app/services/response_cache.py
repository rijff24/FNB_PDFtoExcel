import json
import os
import time
from typing import Any

_CACHE: dict[str, tuple[float, Any]] = {}
_REDIS_CLIENT = None


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


def get_cached(key: str, ttl_seconds: int) -> Any | None:
    redis_client = _redis_client()
    if redis_client:
        try:
            raw = redis_client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None

    item = _CACHE.get(key)
    if not item:
        return None
    created_at, value = item
    if (time.time() - created_at) > max(0, int(ttl_seconds)):
        _CACHE.pop(key, None)
        return None
    return value


def set_cached(key: str, value: Any, ttl_seconds: int = 3600) -> None:
    redis_client = _redis_client()
    if redis_client:
        try:
            redis_client.setex(key, max(1, int(ttl_seconds)), json.dumps(value, default=str))
            return
        except Exception:  # noqa: BLE001
            pass
    _CACHE[key] = (time.time(), value)


def invalidate_prefix(prefix: str) -> None:
    redis_client = _redis_client()
    if redis_client:
        try:
            cursor = 0
            while True:
                cursor, keys = redis_client.scan(cursor=cursor, match=f"{prefix}*", count=200)
                if keys:
                    redis_client.delete(*keys)
                if cursor == 0:
                    break
        except Exception:  # noqa: BLE001
            pass
    for key in list(_CACHE.keys()):
        if key.startswith(prefix):
            _CACHE.pop(key, None)
