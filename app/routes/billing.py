import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.services.access_control import authenticate_request, authenticate_request_with_mode
from app.services.billing import billing_enabled, default_billing_settings, generate_tier_table, tier_price_usd
from app.services.csrf import should_enforce_csrf, validate_double_submit_csrf
from app.services.logging_utils import log_event
from app.services.response_cache import get_cached, invalidate_prefix, set_cached
from app.services.usage_store import get_billing_report, get_user_billing_settings, update_user_billing_settings
from app.services.admin_store import get_billing_pricing_global

router = APIRouter()


def _app_limits_for_transparency() -> dict[str, Any]:
    """Env-based limits shown on billing page (same as quota enforcement when set)."""
    out: dict[str, Any] = {
        "max_file_size_mb": None,
        "max_pages_per_request": None,
        "max_requests_per_minute_per_user": None,
        "max_pages_per_day_per_user": None,
    }
    try:
        out["max_file_size_mb"] = int(os.getenv("MAX_FILE_SIZE_MB", "0") or 0) or None
        out["max_pages_per_request"] = int(os.getenv("MAX_PAGES_PER_REQUEST", "0") or 0) or None
        out["max_requests_per_minute_per_user"] = int(os.getenv("MAX_REQUESTS_PER_MINUTE_PER_USER", "0") or 0) or None
        out["max_pages_per_day_per_user"] = int(os.getenv("MAX_PAGES_PER_DAY_PER_USER", "0") or 0) or None
    except ValueError:
        pass
    return out


def _build_pricing_block(transparency: dict[str, Any], total_documents: int, total_billable: float) -> dict[str, Any]:
    """Build the pricing section of the billing data response."""
    g_usd = float(transparency.get("google_usd_per_classified_document", 0.75) or 0.0)
    m_usd = float(transparency.get("margin_per_document_usd", 0.05) or 0.0)
    fx = float(transparency.get("usd_to_zar", 18.5) or 0.0)
    infra = float(transparency.get("infra_monthly_usd", 9.30) or 0.0)
    brackets = transparency.get("tier_brackets") or None

    table = generate_tier_table(brackets=brackets, usd_to_zar=fx)

    vol = max(1, total_documents) if total_documents > 0 else 1
    current_price_usd = tier_price_usd(n=vol, brackets=brackets)
    effective_per_doc = round(current_price_usd * fx, 6) if total_documents > 0 else 0.0

    non_ocr_price_usd = round(max(0.0, current_price_usd - g_usd), 6)
    return {
        "google_usd_per_document": g_usd,
        "margin_per_document_usd": m_usd,
        "usd_to_zar": fx,
        "infra_monthly_usd": infra,
        "current_volume": total_documents,
        "current_tier_price_usd": current_price_usd,
        "current_tier_price_zar": round(current_price_usd * fx, 2),
        "non_ocr_tier_price_usd": non_ocr_price_usd,
        "non_ocr_tier_price_zar": round(non_ocr_price_usd * fx, 2),
        "effective_per_document_zar": effective_per_doc,
        "tier_table": table,
    }


@router.get("/billing")
async def billing_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "billing.html", {"request": request})


@router.get("/billing/data")
async def billing_data(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
    started = time.perf_counter()
    user = authenticate_request(authorization, request=request, path="/billing/data")
    cache_key = f"billing:data:{user.uid}"
    cached = get_cached(cache_key, ttl_seconds=20)
    if cached is not None:
        return JSONResponse(jsonable_encoder(cached))

    transparency = get_billing_pricing_global()

    if not billing_enabled():
        defaults = default_billing_settings()
        pricing = _build_pricing_block(transparency, total_documents=0, total_billable=0.0)
        payload = {
            "billing_enabled": False,
            "settings": {
                "monthly_limit_amount": defaults.monthly_limit_amount,
                "warn_pct": defaults.warn_pct,
                "hard_stop_enabled": defaults.hard_stop_enabled,
            },
            "report": {
                "month": datetime.now(timezone.utc).strftime("%Y%m"),
                "rollup": {
                    "total_pages": 0,
                    "total_documents": 0,
                    "total_statements": 0,
                    "total_billable": 0.0,
                    "event_count": 0,
                },
                "daily_breakdown": [],
                "recent_events": [],
            },
            "pricing_model": "per_document_tiered",
            "pricing": pricing,
            "transparency": transparency,
            "app_limits": _app_limits_for_transparency(),
        }
        set_cached(cache_key, payload, ttl_seconds=20)
        log_event(logging.INFO, "billing_data_latency", path="/billing/data", extra={"elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
        return JSONResponse(payload)

    settings = get_user_billing_settings(user.uid)
    report = get_billing_report(user.uid)
    rollup = report.get("rollup", {})
    ocr_docs = int(rollup.get("total_documents", 0) or rollup.get("total_statements", 0) or 0)
    non_ocr_docs = int(rollup.get("total_non_ocr_documents", 0) or 0)
    misc_billable = float(rollup.get("total_misc_billable", 0.0) or 0.0)

    g_usd = float(transparency.get("google_usd_per_classified_document", 0.75) or 0.0)
    fx = float(transparency.get("usd_to_zar", 18.5) or 0.0)
    brackets = transparency.get("tier_brackets") or None
    total_volume = ocr_docs + non_ocr_docs
    if total_volume > 0:
        tp = tier_price_usd(n=total_volume, brackets=brackets)
        ocr_billable = round(tp * fx * ocr_docs, 6)
        non_ocr_billable = round(max(0.0, tp - g_usd) * fx * non_ocr_docs, 6)
    else:
        ocr_billable = 0.0
        non_ocr_billable = 0.0
    total_billable = round(ocr_billable + non_ocr_billable + misc_billable, 6)
    rollup["total_billable"] = total_billable

    events = report.get("recent_events", [])
    if total_volume > 0:
        ocr_per_doc_zar = round(tp * fx, 6)
        non_ocr_per_doc_zar = round(max(0.0, tp - g_usd) * fx, 6)
        for ev in events:
            d = int(ev.get("documents_billed", 0) or 0)
            nd = int(ev.get("non_ocr_documents_billed", 0) or 0)
            if d > 0:
                ev["billable_total"] = round(ocr_per_doc_zar * d, 6)
            elif nd > 0:
                ev["billable_total"] = round(non_ocr_per_doc_zar * nd, 6)
            elif ev.get("status") == "blocked":
                ev["billable_total"] = 0

    pricing = _build_pricing_block(transparency, total_documents=total_volume, total_billable=total_billable)

    payload = {
        "billing_enabled": True,
        "currency": "ZAR",
        "pricing_model": "per_document_tiered",
        "settings": {
            "monthly_limit_amount": settings.monthly_limit_amount,
            "warn_pct": settings.warn_pct,
            "hard_stop_enabled": settings.hard_stop_enabled,
        },
        "pricing": pricing,
        "transparency": transparency,
        "app_limits": _app_limits_for_transparency(),
        "report": report,
    }
    set_cached(cache_key, payload, ttl_seconds=20)
    log_event(logging.INFO, "billing_data_latency", path="/billing/data", extra={"elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
    return JSONResponse(jsonable_encoder(payload))


@router.put("/billing/limits")
async def billing_limits_update(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    auth_result = authenticate_request_with_mode(authorization, request=request, path="/billing/limits")
    if should_enforce_csrf(request, auth_result.auth_mode):
        validate_double_submit_csrf(request)
    user = auth_result.user
    if not billing_enabled():
        raise HTTPException(status_code=400, detail="Billing is disabled.")

    updates: dict[str, Any] = {}
    if "monthly_limit_amount" in payload:
        updates["monthly_limit_amount"] = max(0.0, float(payload["monthly_limit_amount"]))
    if "warn_pct" in payload:
        updates["warn_pct"] = min(99.0, max(1.0, float(payload["warn_pct"])))
    if "hard_stop_enabled" in payload:
        updates["hard_stop_enabled"] = bool(payload["hard_stop_enabled"])
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields provided.")

    settings = update_user_billing_settings(user.uid, updates)
    invalidate_prefix(f"billing:data:{user.uid}")
    return JSONResponse(
        {
            "settings": {
                "monthly_limit_amount": settings.monthly_limit_amount,
                "warn_pct": settings.warn_pct,
                "hard_stop_enabled": settings.hard_stop_enabled,
            }
        }
    )
