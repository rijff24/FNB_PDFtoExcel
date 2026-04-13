import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.services.billing import BillingSettings, DEFAULT_TIER_BRACKETS, as_settings_doc, default_billing_settings, month_key, tier_price_usd


def _collection(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


USAGE_EVENTS_COLLECTION = _collection("BILLING_USAGE_COLLECTION", "usage_events")
BILLING_SETTINGS_COLLECTION = _collection("BILLING_SETTINGS_COLLECTION", "billing_settings")
BILLING_ROLLUPS_COLLECTION = _collection("BILLING_ROLLUPS_COLLECTION", "billing_rollups")
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
            "event_count": 0,
            "warning_count": 0,
            "blocked_count": 0,
        }
    data = snap.to_dict() or {}
    data.setdefault("uid", uid)
    data.setdefault("month", key)
    data.setdefault("total_documents", int(data.get("total_statements", 0) or 0))
    data.setdefault("total_non_ocr_documents", 0)
    data.setdefault("total_statements", 0)
    data.setdefault("total_misc_billable", 0.0)
    return data


def record_usage_event(
    event: dict[str, Any],
    *,
    pricing_brackets: list[dict[str, Any]] | None = None,
    usd_to_zar: float | None = None,
    google_usd_per_document: float | None = None,
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

    brackets = pricing_brackets or DEFAULT_TIER_BRACKETS
    fx = float(usd_to_zar if usd_to_zar is not None else event_doc.get("usd_to_zar", 18.5) or 18.5)
    g_usd = float(
        google_usd_per_document if google_usd_per_document is not None
        else event_doc.get("google_usd_per_document", 0.75) or 0.75
    )

    from google.cloud import firestore as firestore_mod

    @firestore_mod.transactional
    def _tx(transaction) -> None:
        rollup_snap = rollup_ref.get(transaction=transaction)
        existing = rollup_snap.to_dict() if rollup_snap.exists else {}

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
        event_count = int(existing.get("event_count", 0)) + 1
        warning_count = int(existing.get("warning_count", 0)) + (1 if event_doc.get("warning") else 0)
        blocked_count = int(existing.get("blocked_count", 0)) + (1 if event_doc.get("status") == "blocked" else 0)

        misc_billable = float(existing.get("total_misc_billable", 0.0))
        ev_billable = float(event_doc.get("billable_total", 0.0) or 0.0)
        if docs_billed == 0 and non_ocr_billed == 0 and ev_billable > 0:
            misc_billable = round(misc_billable + ev_billable, 6)

        total_volume = total_documents + total_non_ocr_documents
        if total_volume > 0:
            tp = tier_price_usd(n=total_volume, brackets=brackets)
            ocr_billable = round(tp * fx * total_documents, 6)
            non_ocr_price = max(0.0, tp - g_usd)
            non_ocr_billable = round(non_ocr_price * fx * total_non_ocr_documents, 6)
        else:
            ocr_billable = 0.0
            non_ocr_billable = 0.0
        total_billable = round(ocr_billable + non_ocr_billable + misc_billable, 6)

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
                "event_count": event_count,
                "warning_count": warning_count,
                "blocked_count": blocked_count,
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
