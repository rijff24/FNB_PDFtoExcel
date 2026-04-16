import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.services.access_control import authenticate_request, authenticate_request_with_mode
from app.services.billing import billing_enabled, build_billing_context, calculate_pool_snapshot, default_billing_settings, month_key
from app.services.billing_finalize import get_finalized_statement
from app.services.csrf import should_enforce_csrf, validate_double_submit_csrf
from app.services.logging_utils import log_event
from app.services.response_cache import get_cached, invalidate_prefix, set_cached
from app.services.usage_store import get_billing_report, get_pool_rollup, get_user_billing_settings, resolve_billing_pool, update_user_billing_settings
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


def _build_pricing_block(
    transparency: dict[str, Any],
    pool_rollup: dict[str, Any],
    pool_id: str,
    scope: str,
    ym: str,
    *,
    include_finalized: bool = True,
) -> dict[str, Any]:
    model = build_billing_context(transparency)
    ocr_docs = int(pool_rollup.get("total_documents", 0) or 0)
    non_ocr_docs = int(pool_rollup.get("total_non_ocr_documents", 0) or 0)
    live = calculate_pool_snapshot(
        scope=scope,
        pool_id=pool_id,
        month=ym,
        ocr_documents=ocr_docs,
        non_ocr_documents=non_ocr_docs,
        context=model,
    )
    finalized = get_finalized_statement(pool_id, ym) if include_finalized else None
    return {
        "google_usd_per_document": model.google_usd_per_document,
        "margin_per_document_usd": model.margin_per_document_usd,
        "usd_to_zar": model.usd_to_zar,
        "infra_monthly_usd": model.infra_monthly_usd,
        "scope": scope,
        "pool_id": pool_id,
        "current_volume": live.total_documents,
        "shared_infra_per_document_usd": live.shared_infra_per_document_usd,
        "ocr_unit_usd": live.ocr_unit_usd,
        "non_ocr_unit_usd": live.non_ocr_unit_usd,
        "ocr_unit_zar": live.ocr_unit_zar,
        "non_ocr_unit_zar": live.non_ocr_unit_zar,
        "live_total_billable_zar": live.total_billable_zar,
        "mode": "finalized" if finalized else "live",
        "finalized": finalized,
        "pricing_model_version": "pooled_live_finalized_v1",
    }


def _pool_rollup_with_fallback(pool_rollup: dict[str, Any], user_rollup: dict[str, Any]) -> dict[str, Any]:
    """
    Keep billing display consistent during migration:
    if pool rollup has no usage yet but user rollup does, reuse user counts.
    """
    out = dict(pool_rollup or {})
    pool_total_docs = int(out.get("total_documents", 0) or 0) + int(out.get("total_non_ocr_documents", 0) or 0)
    user_ocr_docs = int(user_rollup.get("total_documents", 0) or user_rollup.get("total_statements", 0) or 0)
    user_non_ocr_docs = int(user_rollup.get("total_non_ocr_documents", 0) or 0)
    user_total_docs = user_ocr_docs + user_non_ocr_docs
    if pool_total_docs == 0 and user_total_docs > 0:
        out["total_documents"] = user_ocr_docs
        out["total_non_ocr_documents"] = user_non_ocr_docs
    return out


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
        ym = month_key()
        pricing = _build_pricing_block(
            transparency,
            pool_rollup={},
            pool_id=f"user:{user.uid}",
            scope="user",
            ym=ym,
            include_finalized=False,
        )
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
            "pricing_model": "pooled_live_finalized_v1",
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
    ym = report.get("month") or month_key()
    lock = resolve_billing_pool(user.uid, email=user.email, ym=ym, pricing=transparency)
    scope = str(lock.get("scope") or rollup.get("billing_scope") or "user")
    pool_id = str(lock.get("pool_id") or rollup.get("billing_pool_id") or f"user:{user.uid}")
    pool_rollup_raw = get_pool_rollup(pool_id, ym)
    pool_rollup = _pool_rollup_with_fallback(pool_rollup_raw, rollup)
    rollup["total_billable"] = round(float(rollup.get("total_billable", 0.0) or 0.0), 6)

    pricing = _build_pricing_block(transparency, pool_rollup=pool_rollup, pool_id=pool_id, scope=scope, ym=ym)

    payload = {
        "billing_enabled": True,
        "currency": "ZAR",
        "pricing_model": "pooled_live_finalized_v1",
        "settings": {
            "monthly_limit_amount": settings.monthly_limit_amount,
            "warn_pct": settings.warn_pct,
            "hard_stop_enabled": settings.hard_stop_enabled,
        },
        "pricing": pricing,
        "transparency": transparency,
        "app_limits": _app_limits_for_transparency(),
        "report": report,
        "pool_rollup": pool_rollup,
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
