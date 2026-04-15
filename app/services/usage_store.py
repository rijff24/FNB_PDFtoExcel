import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.services.billing import BillingSettings, as_settings_doc, default_billing_settings, month_key


def _collection(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


USAGE_EVENTS_COLLECTION = _collection("BILLING_USAGE_COLLECTION", "usage_events")
BILLING_SETTINGS_COLLECTION = _collection("BILLING_SETTINGS_COLLECTION", "billing_settings")
BILLING_ROLLUPS_COLLECTION = _collection("BILLING_ROLLUPS_COLLECTION", "billing_rollups")
POOL_ROLLUPS_COLLECTION = _collection("BILLING_POOL_ROLLUPS_COLLECTION", "billing_pool_rollups")
MEMBERSHIP_LOCK_COLLECTION = _collection("BILLING_MEMBERSHIP_LOCK_COLLECTION", "billing_membership_locks")
USERS_COLLECTION = _collection("ADMIN_USERS_COLLECTION", "users_profile")


def _client():
    try:
        from google.cloud import firestore as firestore_mod
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "google-cloud-firestore is not installed. Install dependencies from requirements.txt."
        ) from exc
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip()
    if database_id:
        return firestore_mod.Client(project=project_id, database=database_id)
    return firestore_mod.Client(project=project_id)


def _rollup_id(uid: str, ym: str) -> str:
    return f"{uid}_{ym}"


def _pool_rollup_id(pool_id: str, ym: str) -> str:
    return f"{pool_id}_{ym}"


def get_user_billing_settings(uid: str) -> BillingSettings:
    client = _client()
    ref = client.collection(BILLING_SETTINGS_COLLECTION).document(uid)
    snap = ref.get()
    if not snap.exists:
        defaults = default_billing_settings()
        ref.set(as_settings_doc(defaults), merge=True)
        return defaults
    data = snap.to_dict() or {}
    defaults = default_billing_settings()
    return BillingSettings(
        monthly_limit_amount=float(data.get("monthly_limit_amount", defaults.monthly_limit_amount)),
        warn_pct=float(data.get("warn_pct", defaults.warn_pct)),
        hard_stop_enabled=bool(data.get("hard_stop_enabled", defaults.hard_stop_enabled)),
    )


def update_user_billing_settings(uid: str, updates: dict[str, Any]) -> BillingSettings:
    client = _client()
    ref = client.collection(BILLING_SETTINGS_COLLECTION).document(uid)
    ref.set(updates, merge=True)
    return get_user_billing_settings(uid)


def get_month_rollup(uid: str, ym: str | None = None) -> dict[str, Any]:
    key = ym or month_key()
    client = _client()
    ref = client.collection(BILLING_ROLLUPS_COLLECTION).document(_rollup_id(uid, key))
    snap = ref.get()
    if not snap.exists:
        return {
            "uid": uid,
            "month": key,
            "total_pages": 0,
            "total_documents": 0,
            "total_non_ocr_documents": 0,
            "total_statements": 0,
            "total_billable": 0.0,
            "total_misc_billable": 0.0,
            "total_google_cost": 0.0,
            "total_margin": 0.0,
            "total_infra_share": 0.0,
            "event_count": 0,
            "warning_count": 0,
            "blocked_count": 0,
            "billing_scope": "user",
            "billing_pool_id": f"user:{uid}",
        }
    data = snap.to_dict() or {}
    data.setdefault("uid", uid)
    data.setdefault("month", key)
    data.setdefault("total_documents", int(data.get("total_statements", 0) or 0))
    data.setdefault("total_non_ocr_documents", 0)
    data.setdefault("total_statements", 0)
    data.setdefault("total_misc_billable", 0.0)
    data.setdefault("total_infra_share", 0.0)
    data.setdefault("billing_scope", "user")
    data.setdefault("billing_pool_id", f"user:{uid}")
    return data


def get_pool_rollup(pool_id: str, ym: str | None = None) -> dict[str, Any]:
    key = ym or month_key()
    client = _client()
    ref = client.collection(POOL_ROLLUPS_COLLECTION).document(_pool_rollup_id(pool_id, key))
    snap = ref.get()
    if not snap.exists:
        return {
            "pool_id": pool_id,
            "month": key,
            "total_documents": 0,
            "total_non_ocr_documents": 0,
            "total_billable": 0.0,
            "total_google_cost": 0.0,
            "total_margin": 0.0,
            "total_infra_share": 0.0,
            "event_count": 0,
            "warning_count": 0,
            "blocked_count": 0,
            "scope": "user",
            "is_finalized": False,
        }
    data = snap.to_dict() or {}
    data.setdefault("pool_id", pool_id)
    data.setdefault("month", key)
    data.setdefault("total_documents", 0)
    data.setdefault("total_non_ocr_documents", 0)
    data.setdefault("total_infra_share", 0.0)
    data.setdefault("scope", "user")
    data.setdefault("is_finalized", False)
    return data


def resolve_billing_pool(uid: str, *, email: str | None = None, ym: str | None = None, pricing: dict[str, Any] | None = None) -> dict[str, Any]:
    key = ym or month_key()
    client = _client()
    profile_ref = client.collection(USERS_COLLECTION).document(uid)
    profile_snap = profile_ref.get()
    profile = profile_snap.to_dict() if profile_snap.exists else {"uid": uid, "email": email or "", "org_id": None}
    defaults = pricing or {}
    default_scope = str(defaults.get("default_pool_scope", "user") or "user").strip().lower()
    default_scope = default_scope if default_scope in {"user", "organization"} else "user"
    default_unassigned = str(defaults.get("default_unassigned_pool_behavior", "per_user_fallback") or "per_user_fallback").strip().lower()
    if default_unassigned not in {"per_user_fallback", "global_unassigned_pool", "block_unassigned"}:
        default_unassigned = "per_user_fallback"
    desired_scope = str(profile.get("billing_scope") or default_scope).strip().lower()
    if desired_scope not in {"user", "organization"}:
        desired_scope = "user"
    unassigned_behavior = str(profile.get("billing_unassigned_behavior") or default_unassigned).strip().lower()
    if unassigned_behavior not in {"per_user_fallback", "global_unassigned_pool", "block_unassigned"}:
        unassigned_behavior = "per_user_fallback"
    lock_id = f"{uid}_{key}"
    lock_ref = client.collection(MEMBERSHIP_LOCK_COLLECTION).document(lock_id)
    lock_snap = lock_ref.get()
    if lock_snap.exists:
        return lock_snap.to_dict() or {}
    org_id = str(profile.get("org_id") or "").strip()
    scope = desired_scope
    pool_id = f"user:{uid}"
    if desired_scope == "organization":
        if org_id:
            pool_id = f"org:{org_id}"
        elif unassigned_behavior == "global_unassigned_pool":
            pool_id = "org:unassigned"
        elif unassigned_behavior == "block_unassigned":
            pool_id = ""
        else:
            scope = "user"
            pool_id = f"user:{uid}"
    lock_doc = {
        "uid": uid,
        "month": key,
        "scope": scope,
        "pool_id": pool_id,
        "org_id_at_lock": org_id or None,
        "desired_scope": desired_scope,
        "unassigned_behavior": unassigned_behavior,
        "created_at": datetime.now(timezone.utc),
    }
    lock_ref.set(lock_doc, merge=True)
    return lock_doc


def record_usage_event(
    event: dict[str, Any],
) -> None:
    client = _client()
    now = datetime.now(timezone.utc)
    ym = month_key(now)
    uid = str(event["uid"])
    event_id = str(event.get("event_id") or uuid4().hex)
    event_doc = dict(event)
    event_doc["event_id"] = event_id
    event_doc["timestamp"] = event.get("timestamp") or now
    event_doc["month"] = ym
    event_doc["day"] = event_doc["timestamp"].astimezone(timezone.utc).strftime("%Y-%m-%d")

    rollup_ref = client.collection(BILLING_ROLLUPS_COLLECTION).document(_rollup_id(uid, ym))
    event_ref = client.collection(USAGE_EVENTS_COLLECTION).document(event_id)
    pool_id = str(event_doc.get("billing_pool_id") or f"user:{uid}")
    pool_ref = client.collection(POOL_ROLLUPS_COLLECTION).document(_pool_rollup_id(pool_id, ym))

    from google.cloud import firestore as firestore_mod

    @firestore_mod.transactional
    def _tx(transaction) -> None:
        rollup_snap = rollup_ref.get(transaction=transaction)
        existing = rollup_snap.to_dict() if rollup_snap.exists else {}
        pool_snap = pool_ref.get(transaction=transaction)
        pool_existing = pool_snap.to_dict() if pool_snap.exists else {}

        total_pages = int(existing.get("total_pages", 0)) + int(event_doc.get("page_count", 0) or 0)
        docs_billed = int(event_doc.get("documents_billed", 0) or event_doc.get("statements_billed", 0) or 0)
        non_ocr_billed = int(event_doc.get("non_ocr_documents_billed", 0) or 0)
        total_documents = int(existing.get("total_documents", 0) or existing.get("total_statements", 0) or 0) + docs_billed
        total_non_ocr_documents = int(existing.get("total_non_ocr_documents", 0)) + non_ocr_billed
        total_statements = int(existing.get("total_statements", 0)) + docs_billed
        total_google_cost = float(existing.get("total_google_cost", 0.0)) + float(
            event_doc.get("google_cost_total", 0.0) or 0.0
        )
        total_margin = float(existing.get("total_margin", 0.0)) + float(event_doc.get("our_margin_amount", 0.0) or 0.0)
        total_infra_share = float(existing.get("total_infra_share", 0.0)) + float(event_doc.get("infra_share_total", 0.0) or 0.0)
        event_count = int(existing.get("event_count", 0)) + 1
        warning_count = int(existing.get("warning_count", 0)) + (1 if event_doc.get("warning") else 0)
        blocked_count = int(existing.get("blocked_count", 0)) + (1 if event_doc.get("status") == "blocked" else 0)

        misc_billable = float(existing.get("total_misc_billable", 0.0))
        ev_billable = float(event_doc.get("billable_total", 0.0) or 0.0)
        if docs_billed == 0 and non_ocr_billed == 0 and ev_billable > 0:
            misc_billable = round(misc_billable + ev_billable, 6)

        total_billable = round(float(existing.get("total_billable", 0.0)) + ev_billable, 6)

        pool_docs = int(pool_existing.get("total_documents", 0) or 0) + docs_billed
        pool_non_ocr = int(pool_existing.get("total_non_ocr_documents", 0) or 0) + non_ocr_billed
        pool_billable = round(float(pool_existing.get("total_billable", 0.0)) + ev_billable, 6)
        pool_google = round(float(pool_existing.get("total_google_cost", 0.0)) + float(event_doc.get("google_cost_total", 0.0) or 0.0), 6)
        pool_margin = round(float(pool_existing.get("total_margin", 0.0)) + float(event_doc.get("our_margin_amount", 0.0) or 0.0), 6)
        pool_infra = round(float(pool_existing.get("total_infra_share", 0.0)) + float(event_doc.get("infra_share_total", 0.0) or 0.0), 6)
        pool_event_count = int(pool_existing.get("event_count", 0)) + 1
        pool_warning_count = int(pool_existing.get("warning_count", 0)) + (1 if event_doc.get("warning") else 0)
        pool_blocked_count = int(pool_existing.get("blocked_count", 0)) + (1 if event_doc.get("status") == "blocked" else 0)

        transaction.set(event_ref, event_doc)
        transaction.set(
            rollup_ref,
            {
                "uid": uid,
                "month": ym,
                "total_pages": total_pages,
                "total_documents": total_documents,
                "total_non_ocr_documents": total_non_ocr_documents,
                "total_statements": total_statements,
                "total_billable": total_billable,
                "total_misc_billable": misc_billable,
                "total_google_cost": round(total_google_cost, 6),
                "total_margin": round(total_margin, 6),
                "total_infra_share": round(total_infra_share, 6),
                "event_count": event_count,
                "warning_count": warning_count,
                "blocked_count": blocked_count,
                "billing_scope": str(event_doc.get("billing_scope") or "user"),
                "billing_pool_id": pool_id,
                "updated_at": now,
            },
            merge=True,
        )
        transaction.set(
            pool_ref,
            {
                "pool_id": pool_id,
                "month": ym,
                "scope": str(event_doc.get("billing_scope") or "user"),
                "total_documents": pool_docs,
                "total_non_ocr_documents": pool_non_ocr,
                "total_billable": pool_billable,
                "total_google_cost": pool_google,
                "total_margin": pool_margin,
                "total_infra_share": pool_infra,
                "event_count": pool_event_count,
                "warning_count": pool_warning_count,
                "blocked_count": pool_blocked_count,
                "is_finalized": bool(pool_existing.get("is_finalized", False)),
                "updated_at": now,
            },
            merge=True,
        )

    tx = client.transaction()
    _tx(tx)


def get_billing_report(uid: str, ym: str | None = None) -> dict[str, Any]:
    from google.cloud.firestore_v1.base_query import FieldFilter

    key = ym or month_key()
    client = _client()
    rollup = get_month_rollup(uid, key)
    pool_rollup = get_pool_rollup(str(rollup.get("billing_pool_id") or f"user:{uid}"), key)

    query = (
        client.collection(USAGE_EVENTS_COLLECTION)
        .where(filter=FieldFilter("uid", "==", uid))
        .where(filter=FieldFilter("month", "==", key))
        .order_by("timestamp")
        .limit(200)
    )
    events: list[dict[str, Any]] = []
    daily = defaultdict(lambda: {"pages": 0, "documents": 0, "billable_total": 0.0, "event_count": 0})
    for snap in query.stream():
        ev = snap.to_dict() or {}
        ts = ev.get("timestamp")
        day = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else str(ev.get("month", key))
        daily[day]["pages"] += int(ev.get("page_count", 0) or 0)
        daily[day]["documents"] += int(ev.get("documents_billed", 0) or ev.get("statements_billed", 0) or 0)
        daily[day]["billable_total"] += float(ev.get("billable_total", 0.0) or 0.0)
        daily[day]["event_count"] += 1
        if isinstance(ts, datetime):
            ev["timestamp"] = ts.isoformat()
        events.append(ev)

    daily_breakdown = [
        {
            "day": day,
            "pages": entry["pages"],
            "documents": entry["documents"],
            "statements": entry["documents"],
            "billable_total": round(entry["billable_total"], 6),
            "event_count": entry["event_count"],
        }
        for day, entry in sorted(daily.items())
    ]
    events = list(reversed(events))
    return {
        "month": key,
        "rollup": rollup,
        "pool_rollup": pool_rollup,
        "daily_breakdown": daily_breakdown,
        "recent_events": events[:50],
    }


def get_admin_usage_summary(ym: str | None = None, limit: int = 2000) -> dict[str, Any]:
    from google.cloud.firestore_v1.base_query import FieldFilter

    key = ym or month_key()
    client = _client()

    users_by_uid: dict[str, dict[str, Any]] = {}
    for snap in client.collection(USERS_COLLECTION).limit(limit).stream():
        row = snap.to_dict() or {}
        row.setdefault("uid", snap.id)
        users_by_uid[str(row.get("uid") or snap.id)] = row

    query = (
        client.collection(BILLING_ROLLUPS_COLLECTION)
        .where(filter=FieldFilter("month", "==", key))
        .limit(limit)
    )

    by_user: list[dict[str, Any]] = []
    by_org: dict[str, dict[str, Any]] = {}
    totals = {"total_pages": 0, "total_billable": 0.0, "event_count": 0}

    for snap in query.stream():
        row = snap.to_dict() or {}
        uid = str(row.get("uid") or "")
        if not uid:
            continue
        profile = users_by_uid.get(uid, {})
        org_id = str(profile.get("org_id") or "unassigned")
        pages = int(row.get("total_pages", 0) or 0)
        billable = float(row.get("total_billable", 0.0) or 0.0)
        event_count = int(row.get("event_count", 0) or 0)

        by_user.append(
            {
                "uid": uid,
                "email": profile.get("email", ""),
                "org_id": org_id,
                "total_pages": pages,
                "total_billable": round(billable, 6),
                "event_count": event_count,
            }
        )

        org_bucket = by_org.setdefault(
            org_id,
            {"org_id": org_id, "total_pages": 0, "total_billable": 0.0, "event_count": 0},
        )
        org_bucket["total_pages"] += pages
        org_bucket["total_billable"] = round(float(org_bucket["total_billable"]) + billable, 6)
        org_bucket["event_count"] += event_count

        totals["total_pages"] += pages
        totals["total_billable"] = round(float(totals["total_billable"]) + billable, 6)
        totals["event_count"] += event_count

    return {
        "month": key,
        "totals": totals,
        "by_user": by_user,
        "by_org": list(by_org.values()),
    }


def backfill_pool_rollups(ym: str | None = None, *, limit: int = 5000) -> dict[str, Any]:
    """
    Rebuild pool rollups from user rollups for a month.
    Useful during migration where pool rollups may be missing.
    """
    from google.cloud.firestore_v1.base_query import FieldFilter

    key = ym or month_key()
    client = _client()
    query = (
        client.collection(BILLING_ROLLUPS_COLLECTION)
        .where(filter=FieldFilter("month", "==", key))
        .limit(max(1, limit))
    )
    buckets: dict[str, dict[str, Any]] = {}
    user_rows = 0
    for snap in query.stream():
        row = snap.to_dict() or {}
        uid = str(row.get("uid") or "").strip()
        if not uid:
            continue
        user_rows += 1
        pool_id = str(row.get("billing_pool_id") or f"user:{uid}")
        scope = str(row.get("billing_scope") or ("organization" if pool_id.startswith("org:") else "user"))
        b = buckets.setdefault(
            pool_id,
            {
                "pool_id": pool_id,
                "month": key,
                "scope": scope,
                "total_documents": 0,
                "total_non_ocr_documents": 0,
                "total_billable": 0.0,
                "total_google_cost": 0.0,
                "total_margin": 0.0,
                "total_infra_share": 0.0,
                "event_count": 0,
                "warning_count": 0,
                "blocked_count": 0,
                "is_finalized": False,
            },
        )
        b["total_documents"] += int(row.get("total_documents", 0) or row.get("total_statements", 0) or 0)
        b["total_non_ocr_documents"] += int(row.get("total_non_ocr_documents", 0) or 0)
        b["total_billable"] = round(float(b["total_billable"]) + float(row.get("total_billable", 0.0) or 0.0), 6)
        b["total_google_cost"] = round(float(b["total_google_cost"]) + float(row.get("total_google_cost", 0.0) or 0.0), 6)
        b["total_margin"] = round(float(b["total_margin"]) + float(row.get("total_margin", 0.0) or 0.0), 6)
        b["total_infra_share"] = round(float(b["total_infra_share"]) + float(row.get("total_infra_share", 0.0) or 0.0), 6)
        b["event_count"] += int(row.get("event_count", 0) or 0)
        b["warning_count"] += int(row.get("warning_count", 0) or 0)
        b["blocked_count"] += int(row.get("blocked_count", 0) or 0)

    now = datetime.now(timezone.utc)
    written = 0
    for pool_id, payload in buckets.items():
        payload["updated_at"] = now
        payload["backfilled_at"] = now
        client.collection(POOL_ROLLUPS_COLLECTION).document(_pool_rollup_id(pool_id, key)).set(payload, merge=True)
        written += 1
    return {
        "month": key,
        "user_rollups_scanned": user_rows,
        "pool_rollups_written": written,
        "pool_ids": sorted(buckets.keys()),
    }
