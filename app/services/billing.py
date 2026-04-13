import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


DEFAULT_TIER_BRACKETS: list[dict[str, Any]] = [
    {"min_docs": 1,    "max_docs": 5,    "price_usd": 3.12},
    {"min_docs": 6,    "max_docs": 10,   "price_usd": 2.35},
    {"min_docs": 11,   "max_docs": 20,   "price_usd": 1.65},
    {"min_docs": 21,   "max_docs": 30,   "price_usd": 1.24},
    {"min_docs": 31,   "max_docs": 50,   "price_usd": 1.10},
    {"min_docs": 51,   "max_docs": 100,  "price_usd": 0.98},
    {"min_docs": 101,  "max_docs": 500,  "price_usd": 0.89},
    {"min_docs": 501,  "max_docs": 1000, "price_usd": 0.82},
    {"min_docs": 1001, "max_docs": None, "price_usd": 0.81},
]


@dataclass(frozen=True)
class BillingSettings:
    monthly_limit_amount: float
    warn_pct: float
    hard_stop_enabled: bool


@dataclass(frozen=True)
class BillingDecision:
    blocked: bool
    warning: str | None
    projected_total: float
    current_total: float
    limit_remaining: float | None


@dataclass(frozen=True)
class DocumentCostBreakdown:
    """Per classified document (OCR / Document AI) charge in ZAR, bracket-tiered."""

    documents: int
    tier_price_usd: float
    google_usd_per_document: float
    margin_per_document_usd: float
    infra_share_usd: float
    usd_to_zar: float
    google_cost_total_zar: float
    our_margin_amount_zar: float
    billable_total_zar: float
    markup_pct: float
    current_volume: int


StatementCostBreakdown = DocumentCostBreakdown


def billing_enabled() -> bool:
    return os.getenv("BILLING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _default_limit() -> float:
    return _float_env("DEFAULT_MONTHLY_LIMIT", 100.0)


def _default_warn_pct() -> float:
    return _float_env("DEFAULT_WARN_PCT", 80.0)


def default_billing_settings() -> BillingSettings:
    warn_pct = min(99.0, max(1.0, _default_warn_pct()))
    return BillingSettings(
        monthly_limit_amount=max(0.0, _default_limit()),
        warn_pct=warn_pct,
        hard_stop_enabled=True,
    )


def tier_price_usd(
    *,
    n: int,
    brackets: list[dict[str, Any]] | None = None,
) -> float:
    """
    Look up the per-document price (USD) from the tier bracket table.

    Each bracket: {"min_docs": int, "max_docs": int|None, "price_usd": float}
    The last bracket (max_docs=None) covers all volumes above its min_docs.
    """
    n = max(1, int(n))
    tiers = brackets or DEFAULT_TIER_BRACKETS
    for bracket in tiers:
        lo = int(bracket.get("min_docs", 1))
        hi = bracket.get("max_docs")
        if n >= lo and (hi is None or n <= int(hi)):
            return round(float(bracket["price_usd"]), 6)
    return round(float(tiers[-1]["price_usd"]), 6) if tiers else 0.0


def generate_tier_table(
    *,
    brackets: list[dict[str, Any]] | None = None,
    usd_to_zar: float,
) -> list[dict[str, Any]]:
    """Format the bracket table for display on the billing page."""
    tiers = brackets or DEFAULT_TIER_BRACKETS
    fx = max(0.0, float(usd_to_zar))
    table: list[dict[str, Any]] = []
    for b in tiers:
        price = round(float(b["price_usd"]), 6)
        lo = int(b.get("min_docs", 1))
        hi = b.get("max_docs")
        if hi is None:
            label = f"{lo}+"
        elif lo == hi:
            label = str(lo)
        else:
            label = f"{lo}\u2013{hi}"
        table.append({
            "label": label,
            "min_docs": lo,
            "max_docs": hi,
            "price_usd": price,
            "price_zar": round(price * fx, 2),
        })
    return table


def calculate_document_cost(
    *,
    document_count: int,
    current_volume: int,
    google_usd_per_document: float,
    margin_per_document_usd: float,
    usd_to_zar: float,
    infra_monthly_usd: float,
    brackets: list[dict[str, Any]] | None = None,
) -> DocumentCostBreakdown:
    """
    Bill per classified document using bracket-tiered pricing.
    The price is looked up from the tier table using the user's current monthly volume.
    """
    n = max(0, int(document_count))
    vol = max(1, int(current_volume))
    g = max(0.0, float(google_usd_per_document))
    m = max(0.0, float(margin_per_document_usd))
    fx = max(0.0, float(usd_to_zar))

    price = tier_price_usd(n=vol, brackets=brackets)
    infra_share = round(max(0.0, float(infra_monthly_usd)) / max(vol, 10), 6)
    google_cost_total_zar = round(g * fx * n, 6)
    our_margin_amount_zar = round(m * fx * n, 6)
    billable_total_zar = round(price * fx * n, 6)
    markup_pct = 0.0
    if google_cost_total_zar > 0:
        markup_pct = round((our_margin_amount_zar / google_cost_total_zar) * 100.0, 3)
    return DocumentCostBreakdown(
        documents=n,
        tier_price_usd=price,
        google_usd_per_document=g,
        margin_per_document_usd=m,
        infra_share_usd=infra_share,
        usd_to_zar=fx,
        google_cost_total_zar=google_cost_total_zar,
        our_margin_amount_zar=our_margin_amount_zar,
        billable_total_zar=billable_total_zar,
        markup_pct=markup_pct,
        current_volume=vol,
    )


def evaluate_limits(
    *,
    settings: BillingSettings,
    current_total: float,
    projected_total: float,
) -> BillingDecision:
    limit = settings.monthly_limit_amount
    warning = None

    if limit <= 0:
        return BillingDecision(
            blocked=False,
            warning=None,
            projected_total=projected_total,
            current_total=current_total,
            limit_remaining=None,
        )

    warn_at = limit * (settings.warn_pct / 100.0)
    if projected_total >= warn_at:
        warning = (
            f"You are nearing your monthly billing limit: "
            f"{projected_total:.2f} / {limit:.2f}."
        )

    blocked = bool(settings.hard_stop_enabled and projected_total >= limit)
    return BillingDecision(
        blocked=blocked,
        warning=warning,
        projected_total=projected_total,
        current_total=current_total,
        limit_remaining=round(max(0.0, limit - projected_total), 6),
    )


def month_key(dt: datetime | None = None) -> str:
    now = dt or datetime.now(timezone.utc)
    return now.strftime("%Y%m")


def as_settings_doc(settings: BillingSettings) -> dict[str, Any]:
    return {
        "monthly_limit_amount": float(settings.monthly_limit_amount),
        "warn_pct": float(settings.warn_pct),
        "hard_stop_enabled": bool(settings.hard_stop_enabled),
    }
