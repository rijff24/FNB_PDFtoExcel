from datetime import datetime, timezone

import pytest
from pypdf import PdfWriter

from app.services.auth import AuthorizedUser
from app.services.quotas import (
    QuotaBackendUnavailable,
    QuotaConfig,
    QuotaError,
    _day_key,
    _minute_key,
    build_quota_context,
    enforce_file_and_page_caps,
    enforce_redis_quotas,
)


class _FakePipeline:
    def __init__(self, store: dict[str, int], expiries: dict[str, int]) -> None:
        self._store = store
        self._expiries = expiries
        self._commands: list[tuple[str, str, int, bool | None]] = []

    def incrby(self, key: str, amount: int):
        self._commands.append(("incrby", key, amount, None))
        return self

    def expire(self, key: str, ttl: int, nx: bool = False):
        self._commands.append(("expire", key, ttl, nx))
        return self

    def execute(self):
        result: list[int | bool] = []
        for op, key, value, nx in self._commands:
            if op == "incrby":
                self._store[key] = self._store.get(key, 0) + value
                result.append(self._store[key])
            elif op == "expire":
                if nx:
                    self._expiries.setdefault(key, value)
                else:
                    self._expiries[key] = value
                result.append(True)
        return result


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    def pipeline(self):
        return _FakePipeline(self.store, self.expiries)


def _small_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    import io

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_enforce_file_size_cap_rejects() -> None:
    ctx = build_quota_context(
        AuthorizedUser(email="a@b.com", uid="u1"),
        _small_pdf_bytes(),
    )
    cfg = QuotaConfig(
        max_file_size_mb=0,
        max_pages_per_request=5,
        max_requests_per_minute_per_user=10,
        max_pages_per_day_per_user=100,
        redis_url="redis://localhost:6379/0",
    )
    with pytest.raises(QuotaError):
        enforce_file_and_page_caps(ctx, cfg)


def test_enforce_redis_quota_accepts_under_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.services.quotas.Redis.from_url", lambda *_args, **_kwargs: fake_redis)

    ctx = build_quota_context(AuthorizedUser(email="user@example.com", uid="uid-1"), _small_pdf_bytes())
    cfg = QuotaConfig(
        max_file_size_mb=10,
        max_pages_per_request=10,
        max_requests_per_minute_per_user=2,
        max_pages_per_day_per_user=10,
        redis_url="redis://fake",
    )
    now = datetime(2026, 4, 8, 10, 20, 12, tzinfo=timezone.utc)
    enforce_redis_quotas(ctx, cfg, now=now)

    assert fake_redis.store[_minute_key(ctx.user_key, now)] == 1
    assert fake_redis.store[_day_key(ctx.user_key, now)] == ctx.page_count


def test_enforce_redis_quota_rate_limit_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.services.quotas.Redis.from_url", lambda *_args, **_kwargs: fake_redis)

    ctx = build_quota_context(AuthorizedUser(email="user@example.com", uid="uid-2"), _small_pdf_bytes())
    cfg = QuotaConfig(
        max_file_size_mb=10,
        max_pages_per_request=10,
        max_requests_per_minute_per_user=1,
        max_pages_per_day_per_user=100,
        redis_url="redis://fake",
    )
    now = datetime(2026, 4, 8, 10, 20, 12, tzinfo=timezone.utc)
    enforce_redis_quotas(ctx, cfg, now=now)
    with pytest.raises(QuotaError):
        enforce_redis_quotas(ctx, cfg, now=now)


def test_enforce_redis_quota_daily_pages_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.services.quotas.Redis.from_url", lambda *_args, **_kwargs: fake_redis)
    monkeypatch.setattr("app.services.quotas.count_pdf_pages", lambda _pdf: 3)

    ctx = build_quota_context(AuthorizedUser(email="user@example.com", uid="uid-3"), _small_pdf_bytes())
    cfg = QuotaConfig(
        max_file_size_mb=10,
        max_pages_per_request=10,
        max_requests_per_minute_per_user=10,
        max_pages_per_day_per_user=5,
        redis_url="redis://fake",
    )
    now = datetime(2026, 4, 8, 10, 20, 12, tzinfo=timezone.utc)
    enforce_redis_quotas(ctx, cfg, now=now)
    with pytest.raises(QuotaError):
        enforce_redis_quotas(ctx, cfg, now=now)


def test_enforce_redis_quota_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from redis.exceptions import RedisError

    class _BrokenRedis:
        @staticmethod
        def from_url(*_args, **_kwargs):
            raise RedisError("boom")

    monkeypatch.setattr("app.services.quotas.Redis", _BrokenRedis)

    ctx = build_quota_context(AuthorizedUser(email="user@example.com", uid="uid-4"), _small_pdf_bytes())
    cfg = QuotaConfig(
        max_file_size_mb=10,
        max_pages_per_request=10,
        max_requests_per_minute_per_user=10,
        max_pages_per_day_per_user=10,
        redis_url="redis://fake",
    )
    with pytest.raises(QuotaBackendUnavailable):
        enforce_redis_quotas(ctx, cfg)
