from datetime import datetime, timezone
from typing import Any

from app.services.admin_store import BILLING_FINALIZED_COLLECTION
from app.services.usage_store import POOL_ROLLUPS_COLLECTION, _client, get_pool_rollup


def _finalized_doc_id(pool_id: str, ym: str) -> str:
    return f"{pool_id}_{ym}"


def get_finalized_statement(pool_id: str, ym: str) -> dict[str, Any] | None:
    client = _client()
    snap = client.collection(BILLING_FINALIZED_COLLECTION).document(_finalized_doc_id(pool_id, ym)).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data.setdefault("pool_id", pool_id)
    data.setdefault("month", ym)
    return data


def finalize_pool_month(*, pool_id: str, ym: str, actor_uid: str | None = None) -> dict[str, Any]:
    client = _client()
    rollup = get_pool_rollup(pool_id, ym)
    now = datetime.now(timezone.utc)
    payload = {
        "pool_id": pool_id,
        "month": ym,
        "scope": rollup.get("scope", "user"),
        "ocr_documents": int(rollup.get("total_documents", 0) or 0),
        "non_ocr_documents": int(rollup.get("total_non_ocr_documents", 0) or 0),
        "total_billable": float(rollup.get("total_billable", 0.0) or 0.0),
        "total_google_cost": float(rollup.get("total_google_cost", 0.0) or 0.0),
        "total_margin": float(rollup.get("total_margin", 0.0) or 0.0),
        "total_infra_share": float(rollup.get("total_infra_share", 0.0) or 0.0),
        "event_count": int(rollup.get("event_count", 0) or 0),
        "status": "finalized",
        "finalized_at": now,
        "finalized_by": actor_uid,
    }
    client.collection(BILLING_FINALIZED_COLLECTION).document(_finalized_doc_id(pool_id, ym)).set(payload, merge=True)
    client.collection(POOL_ROLLUPS_COLLECTION).document(f"{pool_id}_{ym}").set(
        {"is_finalized": True, "finalized_at": now, "finalized_statement_id": _finalized_doc_id(pool_id, ym)},
        merge=True,
    )
    return payload
