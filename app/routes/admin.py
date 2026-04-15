from datetime import datetime, timezone
import logging
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from app.services.access_control import authenticate_request, authenticate_request_with_mode
from app.services.auth import is_admin_user
from app.services.csrf import should_enforce_csrf, validate_double_submit_csrf
from app.services.admin_store import (
    approve_signup_request,
    assign_user_to_org,
    build_csv,
    create_organization,
    get_admin_usage_report,
    get_billing_pricing_global,
    get_signup_request,
    get_user_billing_policy,
    grant_user_credits,
    set_user_billing_policy,
    list_app_errors,
    list_organizations,
    list_signup_requests,
    list_users,
    reject_signup_request,
    set_billing_pricing_global,
    set_user_status,
    update_organization,
)
from app.services.billing import month_key
from app.services.billing_finalize import finalize_pool_month
from app.services.usage_store import resolve_billing_pool
from app.services.firebase_auth_admin import create_or_get_user_by_email, generate_password_setup_link
from app.services.cost_reconciliation import get_reconciliation_history, run_reconciliation
from app.services.response_cache import get_cached, invalidate_prefix, set_cached
from app.services.usage_store import backfill_pool_rollups, get_admin_usage_summary
from app.services.logging_utils import log_event

router = APIRouter()


def _authenticate_admin_mutation(request: Request, authorization: str | None, path: str):
    result = authenticate_request_with_mode(authorization, request=request, path=path, require_admin=True)
    if should_enforce_csrf(request, result.auth_mode):
        validate_double_submit_csrf(request)
    return result.user


@router.get("/admin")
async def admin_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "admin.html", {"request": request})


@router.get("/admin/me")
async def admin_me(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
    user = authenticate_request(authorization, request=request, path="/admin/me", require_admin=False)
    return JSONResponse({"is_admin": is_admin_user(user), "email": user.email, "uid": user.uid})


@router.get("/admin/data")
async def admin_data(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
    started = time.perf_counter()
    user = authenticate_request(authorization, request=request, path="/admin/data", require_admin=True)
    cache_key = f"admin:data:v2:{user.uid}"
    cached = get_cached(cache_key, ttl_seconds=20)
    if cached is not None:
        return JSONResponse(jsonable_encoder(cached))

    overview = await _admin_overview_payload(request=request, authorization=authorization, user=user)
    users_payload = await _admin_users_payload(request=request, authorization=authorization, user=user, limit=300, offset=0)
    requests_payload = await _admin_requests_payload(
        request=request, authorization=authorization, user=user, status="pending", limit=200, offset=0
    )
    errors_payload = await _admin_errors_payload(request=request, authorization=authorization, user=user, limit=80, offset=0)
    usage_summary_payload = await _admin_usage_summary_payload(request=request, authorization=authorization, user=user)

    payload = {
        "generated_at": overview["generated_at"],
        "users": users_payload["items"],
        "organizations": overview["organizations"],
        "billing_pricing": overview["billing_pricing"],
        "pending_requests": requests_payload["items"],
        "report": {
            "by_user": usage_summary_payload["summary"]["by_user"],
            "by_org": usage_summary_payload["summary"]["by_org"],
            "events": [],
            "totals": usage_summary_payload["summary"]["totals"],
        },
        "errors": errors_payload["items"],
        "reconciliation": overview["reconciliation"],
    }
    set_cached(cache_key, payload, ttl_seconds=20)
    log_event(logging.INFO, "admin_data_latency", path="/admin/data", extra={"elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
    return JSONResponse(jsonable_encoder(payload))


async def _admin_overview_payload(
    *,
    request: Request,
    authorization: str | None,
    user: Any | None = None,
) -> dict[str, Any]:
    admin_user = user or authenticate_request(authorization, request=request, path="/admin/overview", require_admin=True)
    cache_key = f"admin:overview:{admin_user.uid}"
    cached = get_cached(cache_key, ttl_seconds=20)
    if cached is not None:
        return cached
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "billing_pricing": get_billing_pricing_global(),
        "organizations": list_organizations(),
        "reconciliation": get_reconciliation_history(days=35),
    }
    set_cached(cache_key, payload, ttl_seconds=20)
    return payload


@router.get("/admin/overview")
async def admin_overview(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
    started = time.perf_counter()
    payload = await _admin_overview_payload(request=request, authorization=authorization)
    log_event(logging.INFO, "admin_overview_latency", path="/admin/overview", extra={"elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
    return JSONResponse(jsonable_encoder(payload))


async def _admin_users_payload(
    *,
    request: Request,
    authorization: str | None,
    user: Any | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    admin_user = user or authenticate_request(authorization, request=request, path="/admin/users", require_admin=True)
    cache_key = f"admin:users:{admin_user.uid}:{limit}:{offset}"
    cached = get_cached(cache_key, ttl_seconds=20)
    if cached is not None:
        return cached
    all_items = list_users(limit=max(500, limit + offset))
    items = all_items[offset : offset + limit]
    payload = {"items": items, "total": len(all_items), "limit": limit, "offset": offset}
    set_cached(cache_key, payload, ttl_seconds=20)
    return payload


@router.get("/admin/users")
async def admin_users(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = 100,
    offset: int = 0,
) -> JSONResponse:
    started = time.perf_counter()
    payload = await _admin_users_payload(request=request, authorization=authorization, limit=max(1, min(limit, 500)), offset=max(0, offset))
    log_event(logging.INFO, "admin_users_latency", path="/admin/users", extra={"elapsed_ms": round((time.perf_counter() - started) * 1000, 2), "limit": limit, "offset": offset})
    return JSONResponse(jsonable_encoder(payload))


async def _admin_requests_payload(
    *,
    request: Request,
    authorization: str | None,
    user: Any | None = None,
    status: str | None = "pending",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    admin_user = user or authenticate_request(authorization, request=request, path="/admin/requests", require_admin=True)
    cache_key = f"admin:requests:{admin_user.uid}:{status or 'all'}:{limit}:{offset}"
    cached = get_cached(cache_key, ttl_seconds=20)
    if cached is not None:
        return cached
    all_items = list_signup_requests(status=status, limit=max(500, limit + offset))
    items = all_items[offset : offset + limit]
    payload = {"items": items, "total": len(all_items), "limit": limit, "offset": offset, "status": status}
    set_cached(cache_key, payload, ttl_seconds=20)
    return payload


@router.get("/admin/requests")
async def admin_requests(
    request: Request,
    authorization: str | None = Header(default=None),
    status: str | None = "pending",
    limit: int = 100,
    offset: int = 0,
) -> JSONResponse:
    started = time.perf_counter()
    payload = await _admin_requests_payload(
        request=request,
        authorization=authorization,
        status=status,
        limit=max(1, min(limit, 500)),
        offset=max(0, offset),
    )
    log_event(logging.INFO, "admin_requests_latency", path="/admin/requests", extra={"elapsed_ms": round((time.perf_counter() - started) * 1000, 2), "limit": limit, "offset": offset})
    return JSONResponse(jsonable_encoder(payload))


async def _admin_errors_payload(
    *,
    request: Request,
    authorization: str | None,
    user: Any | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    admin_user = user or authenticate_request(authorization, request=request, path="/admin/errors", require_admin=True)
    cache_key = f"admin:errors:{admin_user.uid}:{limit}:{offset}"
    cached = get_cached(cache_key, ttl_seconds=20)
    if cached is not None:
        return cached
    all_items = list_app_errors(limit=max(500, limit + offset))
    items = all_items[offset : offset + limit]
    payload = {"items": items, "total": len(all_items), "limit": limit, "offset": offset}
    set_cached(cache_key, payload, ttl_seconds=20)
    return payload


@router.get("/admin/errors")
async def admin_errors(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = 100,
    offset: int = 0,
) -> JSONResponse:
    started = time.perf_counter()
    payload = await _admin_errors_payload(request=request, authorization=authorization, limit=max(1, min(limit, 500)), offset=max(0, offset))
    log_event(logging.INFO, "admin_errors_latency", path="/admin/errors", extra={"elapsed_ms": round((time.perf_counter() - started) * 1000, 2), "limit": limit, "offset": offset})
    return JSONResponse(jsonable_encoder(payload))


async def _admin_usage_summary_payload(
    *,
    request: Request,
    authorization: str | None,
    user: Any | None = None,
    month: str | None = None,
) -> dict[str, Any]:
    admin_user = user or authenticate_request(authorization, request=request, path="/admin/usage/summary", require_admin=True)
    month_key = month or datetime.now(timezone.utc).strftime("%Y%m")
    cache_key = f"admin:usage_summary:{admin_user.uid}:{month_key}"
    cached = get_cached(cache_key, ttl_seconds=20)
    if cached is not None:
        return cached
    summary = get_admin_usage_summary(month_key)
    payload = {"month": month_key, "summary": summary}
    set_cached(cache_key, payload, ttl_seconds=20)
    return payload


@router.get("/admin/usage/summary")
async def admin_usage_summary(
    request: Request,
    authorization: str | None = Header(default=None),
    month: str | None = None,
) -> JSONResponse:
    started = time.perf_counter()
    payload = await _admin_usage_summary_payload(request=request, authorization=authorization, month=month)
    log_event(logging.INFO, "admin_usage_summary_latency", path="/admin/usage/summary", extra={"elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
    return JSONResponse(jsonable_encoder(payload))


@router.get("/admin/reconciliation")
async def admin_reconciliation(
    request: Request,
    authorization: str | None = Header(default=None),
    days: int = 35,
) -> JSONResponse:
    _ = authenticate_request(authorization, request=request, path="/admin/reconciliation", require_admin=True)
    return JSONResponse(jsonable_encoder(get_reconciliation_history(days=days)))


@router.post("/admin/reconciliation/run")
async def admin_reconciliation_run(
    request: Request,
    authorization: str | None = Header(default=None),
    days: int = 35,
) -> JSONResponse:
    _ = _authenticate_admin_mutation(request, authorization, "/admin/reconciliation/run")
    result = run_reconciliation(days=days, persist=True)
    return JSONResponse(jsonable_encoder(result))


@router.get("/admin/export")
async def admin_export(kind: str, request: Request, authorization: str | None = Header(default=None)) -> Response:
    _ = authenticate_request(authorization, request=request, path="/admin/export", require_admin=True)
    report = get_admin_usage_report()
    if kind == "users":
        csv_text = build_csv(report["by_user"])
    elif kind == "orgs":
        csv_text = build_csv(report["by_org"])
    elif kind == "events":
        csv_text = build_csv(report["events"])
    else:
        raise HTTPException(status_code=400, detail="kind must be one of: users, orgs, events")
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="admin_{kind}_report.csv"'},
    )


@router.post("/admin/orgs")
async def admin_create_org(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    user = _authenticate_admin_mutation(request, authorization, "/admin/orgs")
    name = str(payload.get("name") or "").strip()
    domains = payload.get("domains") or []
    if not name:
        raise HTTPException(status_code=400, detail="Organization name is required.")
    row = create_organization(name=name, domains=domains if isinstance(domains, list) else [])
    invalidate_prefix("admin:")
    return JSONResponse(jsonable_encoder({"organization": row, "by": user.email}))


@router.put("/admin/orgs/{org_id}")
async def admin_update_org(
    org_id: str,
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _ = _authenticate_admin_mutation(request, authorization, "/admin/orgs/{org_id}")
    update_organization(
        org_id,
        name=payload.get("name"),
        domains=payload.get("domains") if isinstance(payload.get("domains"), list) else None,
        active=payload.get("active") if isinstance(payload.get("active"), bool) else None,
    )
    invalidate_prefix("admin:")
    return JSONResponse({"ok": True})


@router.get("/admin/billing-pricing")
async def admin_billing_pricing_get(
    request: Request, authorization: str | None = Header(default=None)
) -> JSONResponse:
    _ = authenticate_request(authorization, request=request, path="/admin/billing-pricing", require_admin=True)
    return JSONResponse(jsonable_encoder(get_billing_pricing_global()))


@router.put("/admin/billing-pricing")
async def admin_billing_pricing_put(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _ = _authenticate_admin_mutation(request, authorization, "/admin/billing-pricing")
    updated = set_billing_pricing_global(payload)
    invalidate_prefix("admin:")
    invalidate_prefix("billing:data:")
    return JSONResponse(jsonable_encoder({"ok": True, "billing_pricing": updated}))


@router.put("/admin/users/{uid}/status")
async def admin_set_status(
    uid: str,
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    admin_user = _authenticate_admin_mutation(request, authorization, "/admin/users/{uid}/status")
    set_user_status(
        uid,
        status=str(payload.get("status") or ""),
        actor_uid=admin_user.uid,
        actor_email=admin_user.email,
        reason=str(payload.get("reason") or "").strip() or None,
    )
    invalidate_prefix("admin:")
    return JSONResponse({"ok": True})


@router.put("/admin/users/{uid}/credits")
async def admin_set_credits(
    uid: str,
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    admin_user = _authenticate_admin_mutation(request, authorization, "/admin/users/{uid}/credits")
    profile = grant_user_credits(
        uid,
        amount=float(payload.get("amount")),
        actor_uid=admin_user.uid,
        actor_email=admin_user.email,
        reason=str(payload.get("reason") or "").strip() or None,
    )
    invalidate_prefix("admin:")
    return JSONResponse(jsonable_encoder({"ok": True, "profile": profile}))


@router.put("/admin/users/{uid}/org")
async def admin_assign_org(
    uid: str,
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _ = _authenticate_admin_mutation(request, authorization, "/admin/users/{uid}/org")
    org_id = payload.get("org_id")
    assign_user_to_org(uid, str(org_id).strip() if org_id else None)
    invalidate_prefix("admin:")
    return JSONResponse({"ok": True})


@router.put("/admin/users/{uid}/billing-policy")
async def admin_set_user_billing_policy(
    uid: str,
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _ = _authenticate_admin_mutation(request, authorization, "/admin/users/{uid}/billing-policy")
    policy = set_user_billing_policy(
        uid,
        billing_scope=payload.get("billing_scope"),
        billing_unassigned_behavior=payload.get("billing_unassigned_behavior"),
    )
    invalidate_prefix("admin:")
    invalidate_prefix("billing:data:")
    return JSONResponse({"ok": True, "policy": policy})


@router.post("/admin/billing/finalize")
async def admin_finalize_pool_month(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    admin_user = _authenticate_admin_mutation(request, authorization, "/admin/billing/finalize")
    month = str(payload.get("month") or month_key()).strip()
    pool_id = str(payload.get("pool_id") or "").strip()
    uid = str(payload.get("uid") or "").strip()
    if not pool_id:
        if not uid:
            raise HTTPException(status_code=400, detail="pool_id or uid is required")
        pricing = get_billing_pricing_global()
        lock = resolve_billing_pool(uid, ym=month, pricing=pricing)
        pool_id = str(lock.get("pool_id") or "")
    if not pool_id:
        raise HTTPException(status_code=400, detail="Could not resolve billing pool")
    result = finalize_pool_month(pool_id=pool_id, ym=month, actor_uid=admin_user.uid)
    invalidate_prefix("billing:data:")
    invalidate_prefix("admin:")
    return JSONResponse(jsonable_encoder({"ok": True, "finalized": result}))


@router.post("/admin/billing/backfill-pools")
async def admin_backfill_pool_rollups(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _ = _authenticate_admin_mutation(request, authorization, "/admin/billing/backfill-pools")
    month = str(payload.get("month") or month_key()).strip()
    limit = int(payload.get("limit") or 5000)
    result = backfill_pool_rollups(month, limit=max(1, min(limit, 50000)))
    invalidate_prefix("billing:data:")
    invalidate_prefix("admin:")
    return JSONResponse(jsonable_encoder({"ok": True, "result": result}))


@router.post("/admin/requests/{request_id}/approve")
async def admin_approve_request(
    request_id: str,
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    admin_user = _authenticate_admin_mutation(request, authorization, "/admin/requests/{request_id}/approve")
    request_row = get_signup_request(request_id)
    if not request_row:
        raise HTTPException(status_code=404, detail="Signup request not found.")
    email = str(payload.get("email") or request_row.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email is required.")
    firebase_user = create_or_get_user_by_email(email)
    uid = str(firebase_user["uid"])
    setup_link = generate_password_setup_link(email)
    approve_signup_request(
        request_id,
        uid=uid,
        email=email,
        org_id=(str(payload.get("org_id")).strip() if payload.get("org_id") else None),
        actor_uid=admin_user.uid,
        actor_email=admin_user.email,
    )
    invalidate_prefix("admin:")
    return JSONResponse(
        {
            "ok": True,
            "uid": uid,
            "email": email,
            "password_setup_link": setup_link,
        }
    )


@router.post("/admin/requests/{request_id}/reject")
async def admin_reject_request(
    request_id: str,
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    admin_user = _authenticate_admin_mutation(request, authorization, "/admin/requests/{request_id}/reject")
    reject_signup_request(
        request_id,
        actor_uid=admin_user.uid,
        actor_email=admin_user.email,
        reason=str(payload.get("reason") or "").strip() or None,
    )
    invalidate_prefix("admin:")
    return JSONResponse({"ok": True})
