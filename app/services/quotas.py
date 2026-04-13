import io
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pypdf import PdfReader
from redis import Redis
from redis.exceptions import RedisError

from app.services.auth import AuthorizedUser


@dataclass(frozen=True)
class QuotaConfig:
    max_file_size_mb: int
    max_pages_per_request: int
    max_requests_per_minute_per_user: int
    max_pages_per_day_per_user: int
    redis_url: str

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@dataclass(frozen=True)
class QuotaContext:
    user_key: str
    user_email: str
    uid: str
    page_count: int
    file_size_bytes: int


class QuotaError(RuntimeError):
    pass


class QuotaBackendUnavailable(QuotaError):
    pass


def _require_int_env(name: str) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        raise RuntimeError(f"Missing required environment variable: {name}")
    value = int(raw)
    if value <= 0:
        raise RuntimeError(f"Environment variable must be > 0: {name}")
    return value


def _require_str_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_quota_config() -> QuotaConfig:
    return QuotaConfig(
        max_file_size_mb=_require_int_env("MAX_FILE_SIZE_MB"),
        max_pages_per_request=_require_int_env("MAX_PAGES_PER_REQUEST"),
        max_requests_per_minute_per_user=_require_int_env("MAX_REQUESTS_PER_MINUTE_PER_USER"),
        max_pages_per_day_per_user=_require_int_env("MAX_PAGES_PER_DAY_PER_USER"),
        redis_url=_require_str_env("REDIS_URL"),
    )


def count_pdf_pages(pdf_bytes: bytes) -> int:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        raise QuotaError("Unable to read PDF page count.") from exc


def build_quota_context(user: AuthorizedUser, pdf_bytes: bytes) -> QuotaContext:
    user_key = user.uid.strip() or user.email.strip().lower()
    return QuotaContext(
        user_key=user_key,
        user_email=user.email,
        uid=user.uid,
        page_count=count_pdf_pages(pdf_bytes),
        file_size_bytes=len(pdf_bytes),
    )


def enforce_file_and_page_caps(ctx: QuotaContext, cfg: QuotaConfig) -> None:
    if ctx.file_size_bytes > cfg.max_file_size_bytes:
        raise QuotaError(
            f"File exceeds max size of {cfg.max_file_size_mb} MB."
        )
    if ctx.page_count > cfg.max_pages_per_request:
        raise QuotaError(
            f"PDF exceeds max pages per request ({cfg.max_pages_per_request})."
        )


def _minute_ttl_seconds(now: datetime) -> int:
    next_minute = (now.replace(second=0, microsecond=0) + timedelta(minutes=1))
    return max(1, int((next_minute - now).total_seconds()))


def _day_ttl_seconds(now: datetime) -> int:
    next_day = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    return max(1, int((next_day - now).total_seconds()))


def _minute_key(user_key: str, now: datetime) -> str:
    return f"quota:req:{user_key}:{now.strftime('%Y%m%d%H%M')}"


def _day_key(user_key: str, now: datetime) -> str:
    return f"quota:pages:{user_key}:{now.strftime('%Y%m%d')}"


def _incr_with_expiry(redis_client: Redis, key: str, amount: int, ttl_seconds: int) -> int:
    pipe = redis_client.pipeline()
    pipe.incrby(key, amount)
    pipe.expire(key, ttl_seconds, nx=True)
    result = pipe.execute()
    return int(result[0])


def enforce_redis_quotas(ctx: QuotaContext, cfg: QuotaConfig, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    try:
        redis_client = Redis.from_url(cfg.redis_url, decode_responses=True)
        req_count = _incr_with_expiry(
            redis_client,
            _minute_key(ctx.user_key, now),
            1,
            _minute_ttl_seconds(now),
        )
        if req_count > cfg.max_requests_per_minute_per_user:
            raise QuotaError("Rate limit exceeded. Try again in a minute.")

        page_count = _incr_with_expiry(
            redis_client,
            _day_key(ctx.user_key, now),
            ctx.page_count,
            _day_ttl_seconds(now),
        )
        if page_count > cfg.max_pages_per_day_per_user:
            raise QuotaError("Daily page quota exceeded. Try again tomorrow.")
    except QuotaError:
        raise
    except RedisError as exc:
        raise QuotaBackendUnavailable("Quota backend unavailable.") from exc

