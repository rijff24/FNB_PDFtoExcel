import csv
import io
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _collection(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


USERS_COLLECTION = _collection("ADMIN_USERS_COLLECTION", "users_profile")
REQUESTS_COLLECTION = _collection("ADMIN_SIGNUP_REQUESTS_COLLECTION", "signup_requests")
ORGS_COLLECTION = _collection("ADMIN_ORGS_COLLECTION", "organizations")
BILLING_PRICING_COLLECTION = _collection("BILLING_PRICING_COLLECTION", "billing_pricing")
AUDIT_COLLECTION = _collection("ADMIN_AUDIT_COLLECTION", "admin_audit_events")
ERRORS_COLLECTION = _collection("ADMIN_ERRORS_COLLECTION", "app_errors")
USAGE_EVENTS_COLLECTION = _collection("BILLING_USAGE_COLLECTION", "usage_events")


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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_user_profile(uid: str, email: str | None = None) -> dict[str, Any]:
    client = _client()
    ref = client.collection(USERS_COLLECTION).document(uid)
    snap = ref.get()
    if not snap.exists:
        profile = {
            "uid": uid,
            "email": (email or "").strip().lower(),
            "status": "active",
            "org_id": None,
            "credits_balance": 0.0,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        ref.set(profile, merge=True)
        return profile
    data = snap.to_dict() or {}
    data.setdefault("uid", uid)
    if email and not data.get("email"):
        data["email"] = email.strip().lower()
    data.setdefault("status", "active")
    data.setdefault("credits_balance", 0.0)
    return data


def get_user_profile_if_exists(uid: str) -> dict[str, Any] | None:
    client = _client()
    ref = client.collection(USERS_COLLECTION).document(uid)
    snap = ref.get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data.setdefault("uid", uid)
    data.setdefault("status", "active")
    data.setdefault("credits_balance", 0.0)
    return data


def ensure_user_is_active(uid: str, email: str | None = None) -> dict[str, Any]:
    profile = get_user_profile(uid, email=email)
    status = str(profile.get("status", "active")).strip().lower()
    if status == "active":
        return profile
    if status == "pending":
        raise RuntimeError("Account pending approval.")
    raise RuntimeError("Account access revoked.")


def set_user_status(uid: str, status: str, *, actor_uid: str, actor_email: str, reason: str | None = None) -> None:
    normalized = status.strip().lower()
    if normalized not in {"pending", "active", "revoked"}:
        raise RuntimeError("Invalid account status.")
    client = _client()
    ref = client.collection(USERS_COLLECTION).document(uid)
    ref.set({"status": normalized, "updated_at": utcnow()}, merge=True)
    write_admin_audit_event(
        actor_uid=actor_uid,
        actor_email=actor_email,
        action="set_user_status",
        target={"uid": uid, "status": normalized},
        details={"reason": reason},
    )


def grant_user_credits(uid: str, amount: float, *, actor_uid: str, actor_email: str, reason: str | None = None) -> dict[str, Any]:
    credit_amount = round(float(amount), 6)
    if credit_amount == 0:
        raise RuntimeError("Credit amount must be non-zero.")
    client = _client()
    ref = client.collection(USERS_COLLECTION).document(uid)
    snap = ref.get()
    profile = snap.to_dict() if snap.exists else {"uid": uid, "status": "active", "credits_balance": 0.0}
    balance = float(profile.get("credits_balance", 0.0) or 0.0)
    updated = round(max(0.0, balance + credit_amount), 6)
    ref.set({"credits_balance": updated, "updated_at": utcnow()}, merge=True)
    write_admin_audit_event(
        actor_uid=actor_uid,
        actor_email=actor_email,
        action="grant_user_credits",
        target={"uid": uid},
        details={"amount": credit_amount, "reason": reason, "new_balance": updated},
    )
    profile["credits_balance"] = updated
    profile["uid"] = uid
    return profile


def apply_user_credits(uid: str, amount: float) -> float:
    needed = max(0.0, float(amount))
    if needed <= 0:
        return 0.0
    client = _client()
    ref = client.collection(USERS_COLLECTION).document(uid)
    snap = ref.get()
    profile = snap.to_dict() if snap.exists else {"uid": uid, "status": "active", "credits_balance": 0.0}
    balance = float(profile.get("credits_balance", 0.0) or 0.0)
    applied = round(min(balance, needed), 6)
    if applied <= 0:
        return 0.0
    updated = round(max(0.0, balance - applied), 6)
    ref.set({"credits_balance": updated, "updated_at": utcnow()}, merge=True)
    return applied


def _default_billing_pricing_global() -> dict[str, Any]:
    """Defaults for tiered per-document billing + transparency (merged with Firestore)."""
    return {
        "google_usd_per_classified_document": 0.75,
        "margin_per_document_usd": 0.05,
        "usd_to_zar": 18.5,
        "bill_currency": "ZAR",
        "infra_monthly_usd": 9.30,
        "tier_brackets": [
            {"min_docs": 1,    "max_docs": 5,    "price_usd": 3.12},
            {"min_docs": 6,    "max_docs": 10,   "price_usd": 2.35},
            {"min_docs": 11,   "max_docs": 20,   "price_usd": 1.65},
            {"min_docs": 21,   "max_docs": 30,   "price_usd": 1.24},
            {"min_docs": 31,   "max_docs": 50,   "price_usd": 1.10},
            {"min_docs": 51,   "max_docs": 100,  "price_usd": 0.98},
            {"min_docs": 101,  "max_docs": 500,  "price_usd": 0.89},
            {"min_docs": 501,  "max_docs": 1000, "price_usd": 0.82},
            {"min_docs": 1001, "max_docs": None,  "price_usd": 0.81},
        ],
        "pricing_transparency_effective_date": "2026-04-10",
        "transparency_estimate_scope_note": (
            "The approximately $9.30 USD per month total in the table below comes from a Google Cloud Pricing "
            "Calculator export (negotiated pricing for our billing account). It is not unlimited: each row lists "
            "the calculator assumptions (storage, requests, build minutes, etc.). Higher usage generally means "
            "higher Google charges. Per-document Document AI (Bank Statement Parser) usage is billed separately "
            "from that fixed infrastructure estimate. The ~$9.30/mo infrastructure cost is "
            "amortized across your monthly document volume in the tiered pricing. Actual "
            "invoices may differ (credits, promotions, rounding, timing)."
        ),
        "pricing_transparency_line_items": [
            {
                "service": "Document AI",
                "sku": "Bank Statement Parser (per-document API)",
                "quantity": 0.0,
                "quantity_unit": "usage-based",
                "region": "eu",
                "monthly_usd": 0.0,
                "notes": "Not included in the ~$9.30/mo infra subtotal; scales with OCR documents processed.",
                "doc_url": "https://cloud.google.com/document-ai/pricing",
                "estimate_limits": [
                    {
                        "label": "Scope",
                        "value": (
                            "OCR cost is per classified document, not part of the fixed "
                            "infrastructure calculator total below."
                        ),
                    },
                ],
            },
            {
                "service": "Artifact Registry",
                "sku": "Artifact Registry Storage",
                "quantity": 5.0,
                "quantity_unit": "GiB",
                "region": "global",
                "monthly_usd": 0.45,
                "notes": "",
                "doc_url": "https://cloud.google.com/artifact-registry/pricing",
                "estimate_limits": [
                    {"label": "Storage", "value": "5 GiB"},
                    {"label": "Transfer (source and destination)", "value": "Between locations in Europe"},
                    {"label": "Repository continent", "value": "Africa"},
                ],
            },
            {
                "service": "Secret Manager",
                "sku": "Secret access operations + version storage",
                "quantity": 10000.0,
                "quantity_unit": "access ops",
                "region": "global",
                "monthly_usd": 0.0,
                "notes": "",
                "doc_url": "https://cloud.google.com/secret-manager/pricing",
                "estimate_limits": [
                    {"label": "Access operations", "value": "10,000 / month"},
                    {"label": "Active secret versions (all locations)", "value": "6"},
                ],
            },
            {
                "service": "Cloud Run",
                "sku": "CPU, memory, requests (request-based billing)",
                "quantity": 10000000.0,
                "quantity_unit": "requests/mo",
                "region": "africa-south1",
                "monthly_usd": 8.25,
                "notes": "Public API / website profile; scales to zero when idle.",
                "doc_url": "https://cloud.google.com/run/pricing",
                "estimate_limits": [
                    {"label": "CPU per instance", "value": "1 vCPU"},
                    {"label": "Memory per instance", "value": "512 MiB"},
                    {"label": "Requests", "value": "10 million / month"},
                    {"label": "Region", "value": "africa-south1 (Johannesburg)"},
                    {"label": "Execution time per request", "value": "400 ms"},
                    {"label": "Concurrent requests per instance", "value": "20"},
                    {"label": "Minimum instances", "value": "0"},
                    {"label": "Internet egress (estimate)", "value": "0 B / month"},
                    {"label": "Billing model", "value": "Charged when processing requests; CPU limited outside requests"},
                ],
            },
            {
                "service": "BigQuery",
                "sku": "On-demand analysis + active logical storage",
                "quantity": 0.2,
                "quantity_unit": "TiB queried",
                "region": "us-central1",
                "monthly_usd": 0.0,
                "notes": "",
                "doc_url": "https://cloud.google.com/bigquery/pricing",
                "estimate_limits": [
                    {"label": "Amount of data queried", "value": "0.2 TiB / month"},
                    {"label": "Active logical storage", "value": "10 GiB"},
                    {"label": "Location", "value": "us-central1 (Iowa)"},
                ],
            },
            {
                "service": "Firestore",
                "sku": "Reads, writes, stored data",
                "quantity": 1.0,
                "quantity_unit": "GiB stored",
                "region": "africa-south1",
                "monthly_usd": 0.0,
                "notes": "",
                "doc_url": "https://cloud.google.com/firestore/pricing",
                "estimate_limits": [
                    {"label": "Document reads", "value": "50,000 / day"},
                    {"label": "Document writes", "value": "20,000 / day"},
                    {"label": "Total stored data", "value": "1 GiB"},
                    {"label": "Location", "value": "africa-south1 (Johannesburg)"},
                ],
            },
            {
                "service": "Cloud Build",
                "sku": "E2 build minutes",
                "quantity": 120.0,
                "quantity_unit": "min",
                "region": "africa-south1",
                "monthly_usd": 0.40,
                "notes": "",
                "doc_url": "https://cloud.google.com/build/pricing",
                "estimate_limits": [
                    {"label": "Build minutes", "value": "120 / month"},
                    {"label": "Machine type", "value": "e2-medium"},
                    {"label": "Pool type", "value": "Standard"},
                    {"label": "Scope", "value": "Regional"},
                    {"label": "Region", "value": "africa-south1 (Johannesburg)"},
                ],
            },
            {
                "service": "Cloud Storage",
                "sku": "Standard storage",
                "quantity": 10.0,
                "quantity_unit": "GiB",
                "region": "africa-south1",
                "monthly_usd": 0.2,
                "notes": "",
                "doc_url": "https://cloud.google.com/storage/pricing",
                "estimate_limits": [
                    {"label": "Total storage", "value": "10 GiB"},
                    {"label": "Storage class", "value": "Standard"},
                    {"label": "Location", "value": "africa-south1 (Johannesburg)"},
                ],
            },
        ],
        "notice_non_profit": (
            "We are not currently pricing for profit. What you pay covers Google's per-document "
            "API cost for document processing, infrastructure amortization, and a small fixed margin."
        ),
        "notice_early_product": (
            "This product is still in development. Pricing reflects pass-through of Google's "
            "document-processing cost plus a margin while we complete the app."
        ),
        "notice_future_pricing": (
            "Prices may increase in the future as we add features and sustainable operations. "
            "We will communicate material changes in advance where possible."
        ),
        "notice_temporary": (
            "This pricing approach is intended only until the app is fully finished and launch-ready; "
            "we may then adjust pricing."
        ),
        "notice_accuracy": (
            "Google list prices are in USD. Your charges are shown in ZAR using the USD→ZAR rate "
            "configured by us; exchange rates, taxes, and billing timing may differ from Google's invoice."
        ),
        "processor_limits_note": (
            "Document AI processor limits vary by processor type and sync vs batch. See Google's "
            "current limits for the Bank Statement Parser in the Document AI limits documentation."
        ),
    }


def get_billing_pricing_global() -> dict[str, Any]:
    """Merged global billing + transparency config for tiered per-document pricing."""
    out = dict(_default_billing_pricing_global())
    client = _client()
    snap = client.collection(BILLING_PRICING_COLLECTION).document("global").get()
    if snap.exists:
        data = snap.to_dict() or {}
        for k, v in data.items():
            if k in ("pricing_transparency_line_items", "tier_brackets") and isinstance(v, list):
                out[k] = v
            elif v is not None:
                out[k] = v
        # Fallback: read old field names from existing Firestore docs
        if "google_usd_per_classified_document" not in data and "google_usd_per_classified_bank_statement" in data:
            out["google_usd_per_classified_document"] = float(data["google_usd_per_classified_bank_statement"])
        if "margin_per_document_usd" not in data and "customer_margin_per_statement_usd" in data:
            out["margin_per_document_usd"] = float(data["customer_margin_per_statement_usd"])
    return out


_FLOAT_BILLING_KEYS = {
    "google_usd_per_classified_document",
    "margin_per_document_usd",
    "usd_to_zar",
    "infra_monthly_usd",
}

_LIST_BILLING_KEYS = {
    "pricing_transparency_line_items",
    "tier_brackets",
}


def set_billing_pricing_global(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge updates into billing_pricing/global (admin)."""
    allowed = {
        "google_usd_per_classified_document",
        "margin_per_document_usd",
        "usd_to_zar",
        "bill_currency",
        "infra_monthly_usd",
        "tier_brackets",
        "pricing_transparency_effective_date",
        "pricing_transparency_line_items",
        "notice_non_profit",
        "notice_early_product",
        "notice_future_pricing",
        "notice_temporary",
        "notice_accuracy",
        "processor_limits_note",
        "transparency_estimate_scope_note",
    }
    payload: dict[str, Any] = {"updated_at": utcnow()}
    for k, v in updates.items():
        if k not in allowed:
            continue
        if k in _LIST_BILLING_KEYS:
            if v is not None:
                payload[k] = v
            continue
        if k in _FLOAT_BILLING_KEYS:
            payload[k] = float(v)
        elif k == "bill_currency":
            payload[k] = str(v).strip()[:8] or "ZAR"
        elif isinstance(v, str):
            payload[k] = v
        else:
            payload[k] = v
    client = _client()
    client.collection(BILLING_PRICING_COLLECTION).document("global").set(payload, merge=True)
    return get_billing_pricing_global()


def create_organization(name: str, domains: list[str] | None = None) -> dict[str, Any]:
    client = _client()
    org_id = uuid4().hex
    payload = {
        "org_id": org_id,
        "name": name.strip(),
        "domains": sorted({d.strip().lower() for d in (domains or []) if d.strip()}),
        "active": True,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    client.collection(ORGS_COLLECTION).document(org_id).set(payload, merge=True)
    return payload


def list_organizations(limit: int = 200) -> list[dict[str, Any]]:
    client = _client()
    out: list[dict[str, Any]] = []
    for snap in client.collection(ORGS_COLLECTION).limit(limit).stream():
        row = snap.to_dict() or {}
        row.setdefault("org_id", snap.id)
        out.append(row)
    return out


def update_organization(org_id: str, *, name: str | None = None, domains: list[str] | None = None, active: bool | None = None) -> None:
    client = _client()
    payload: dict[str, Any] = {"updated_at": utcnow()}
    if name is not None:
        payload["name"] = name.strip()
    if domains is not None:
        payload["domains"] = sorted({d.strip().lower() for d in domains if d.strip()})
    if active is not None:
        payload["active"] = bool(active)
    client.collection(ORGS_COLLECTION).document(org_id).set(payload, merge=True)


def find_org_by_domain(domain: str) -> dict[str, Any] | None:
    needle = domain.strip().lower()
    if not needle:
        return None
    client = _client()
    query = client.collection(ORGS_COLLECTION).where("domains", "array_contains", needle).limit(1)
    for snap in query.stream():
        row = snap.to_dict() or {}
        row.setdefault("org_id", snap.id)
        return row
    return None


def assign_user_to_org(uid: str, org_id: str | None) -> None:
    client = _client()
    client.collection(USERS_COLLECTION).document(uid).set({"org_id": org_id, "updated_at": utcnow()}, merge=True)


def create_signup_request(
    email: str,
    requested_name: str | None = None,
    requested_organization: str | None = None,
    how_heard_about_us: str | None = None,
) -> dict[str, Any]:
    cleaned_email = email.strip().lower()
    domain = cleaned_email.split("@", 1)[1] if "@" in cleaned_email else ""
    suggested_org = find_org_by_domain(domain)
    request_id = uuid4().hex
    payload = {
        "request_id": request_id,
        "email": cleaned_email,
        "requested_name": (requested_name or "").strip(),
        "requested_organization": (requested_organization or "").strip(),
        "how_heard_about_us": (how_heard_about_us or "").strip(),
        "detected_domain": domain,
        "suggested_org_id": suggested_org.get("org_id") if suggested_org else None,
        "status": "pending",
        "created_at": utcnow(),
    }
    client = _client()
    client.collection(REQUESTS_COLLECTION).document(request_id).set(payload, merge=True)
    return payload


def get_signup_request(request_id: str) -> dict[str, Any] | None:
    client = _client()
    snap = client.collection(REQUESTS_COLLECTION).document(request_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data.setdefault("request_id", request_id)
    return data


def list_signup_requests(status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    from google.cloud.firestore_v1.base_query import FieldFilter

    client = _client()
    query = client.collection(REQUESTS_COLLECTION).limit(limit)
    # Avoid requiring composite indexes (status + created_at) by filtering/sorting in memory.
    if status:
        query = query.where(filter=FieldFilter("status", "==", status))
    out: list[dict[str, Any]] = []
    for snap in query.stream():
        row = snap.to_dict() or {}
        row.setdefault("request_id", snap.id)
        out.append(row)
    out.sort(key=lambda r: str(r.get("created_at") or ""))
    return out


def approve_signup_request(
    request_id: str,
    *,
    uid: str,
    email: str,
    actor_uid: str,
    actor_email: str,
    org_id: str | None = None,
) -> None:
    client = _client()
    client.collection(REQUESTS_COLLECTION).document(request_id).set(
        {"status": "approved", "approved_at": utcnow(), "approved_by": actor_uid},
        merge=True,
    )
    profile = get_user_profile(uid, email=email)
    updates = {
        "status": "active",
        "approved_at": utcnow(),
        "approved_by": actor_uid,
        "updated_at": utcnow(),
    }
    if org_id is not None:
        updates["org_id"] = org_id
    elif not profile.get("org_id"):
        suggested = str(profile.get("suggested_org_id") or "").strip()
        if suggested:
            updates["org_id"] = suggested
    client.collection(USERS_COLLECTION).document(uid).set(updates, merge=True)
    write_admin_audit_event(
        actor_uid=actor_uid,
        actor_email=actor_email,
        action="approve_signup_request",
        target={"request_id": request_id, "uid": uid},
        details={"org_id": updates.get("org_id")},
    )


def reject_signup_request(request_id: str, *, actor_uid: str, actor_email: str, reason: str | None = None) -> None:
    client = _client()
    client.collection(REQUESTS_COLLECTION).document(request_id).set(
        {"status": "rejected", "rejected_at": utcnow(), "rejected_by": actor_uid, "reason": reason or ""},
        merge=True,
    )
    write_admin_audit_event(
        actor_uid=actor_uid,
        actor_email=actor_email,
        action="reject_signup_request",
        target={"request_id": request_id},
        details={"reason": reason},
    )


def list_users(limit: int = 500) -> list[dict[str, Any]]:
    client = _client()
    out: list[dict[str, Any]] = []
    for snap in client.collection(USERS_COLLECTION).limit(limit).stream():
        row = snap.to_dict() or {}
        row.setdefault("uid", snap.id)
        out.append(row)
    return out


def write_admin_audit_event(*, actor_uid: str, actor_email: str, action: str, target: dict[str, Any], details: dict[str, Any] | None = None) -> None:
    client = _client()
    event_id = uuid4().hex
    client.collection(AUDIT_COLLECTION).document(event_id).set(
        {
            "event_id": event_id,
            "actor_uid": actor_uid,
            "actor_email": actor_email,
            "action": action,
            "target": target,
            "details": details or {},
            "timestamp": utcnow(),
        },
        merge=True,
    )


def record_app_error(payload: dict[str, Any]) -> None:
    client = _client()
    event_id = uuid4().hex
    row = dict(payload)
    row["error_id"] = event_id
    row["timestamp"] = payload.get("timestamp") or utcnow()
    client.collection(ERRORS_COLLECTION).document(event_id).set(row, merge=True)


def list_app_errors(limit: int = 200) -> list[dict[str, Any]]:
    client = _client()
    query = client.collection(ERRORS_COLLECTION).order_by("timestamp", direction="DESCENDING").limit(limit)
    out: list[dict[str, Any]] = []
    for snap in query.stream():
        row = snap.to_dict() or {}
        row.setdefault("error_id", snap.id)
        out.append(row)
    return out


def get_admin_usage_report(month: str | None = None, limit: int = 5000) -> dict[str, Any]:
    from google.cloud.firestore_v1.base_query import FieldFilter
    from app.services.usage_store import get_admin_usage_summary

    summary = get_admin_usage_summary(month, limit=2000)

    client = _client()
    key = month or datetime.now(timezone.utc).strftime("%Y%m")
    query = (
        client.collection(USAGE_EVENTS_COLLECTION)
        .where(filter=FieldFilter("month", "==", key))
        .order_by("timestamp")
        .limit(min(500, max(1, limit)))
    )
    events: list[dict[str, Any]] = []
    for snap in query.stream():
        ev = snap.to_dict() or {}
        ts = ev.get("timestamp")
        if isinstance(ts, datetime):
            ev["timestamp"] = ts.isoformat()
        events.append(ev)
    events.reverse()
    return {"by_user": summary.get("by_user", []), "by_org": summary.get("by_org", []), "events": events}


def build_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    headers = sorted({k for row in rows for k in row.keys()})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()
