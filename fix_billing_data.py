"""
One-time fix script: zero out blocked events' billable_total and
recalculate the rollup from all events using volume-tiered pricing.

Run inside the app container or with GOOGLE_CLOUD_PROJECT set:
    python fix_billing_data.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from collections import defaultdict
from app.services.billing import DEFAULT_TIER_BRACKETS, tier_price_usd
from app.services.admin_store import get_billing_pricing_global


def main():
    from google.cloud import firestore as firestore_mod

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip()
    if database_id:
        client = firestore_mod.Client(project=project_id, database=database_id)
    else:
        client = firestore_mod.Client(project=project_id)

    pricing = get_billing_pricing_global()
    g_usd = float(pricing.get("google_usd_per_classified_document", 0.75) or 0.0)
    fx = float(pricing.get("usd_to_zar", 18.5) or 0.0)
    brackets = pricing.get("tier_brackets") or DEFAULT_TIER_BRACKETS

    events_coll = client.collection("usage_events")
    rollups_coll = client.collection("billing_rollups")

    # Read ALL events, group by uid+month
    print("Reading all usage events...")
    events_by_key: dict[str, list[dict]] = defaultdict(list)
    fixed_blocked = 0
    for snap in events_coll.stream():
        ev = snap.to_dict() or {}
        ev_id = snap.id

        # Fix blocked events: zero out billable_total
        if ev.get("status") == "blocked" and float(ev.get("billable_total", 0) or 0) > 0:
            print(f"  Fixing blocked event {ev_id}: billable_total {ev.get('billable_total')} -> 0")
            events_coll.document(ev_id).update({
                "billable_total": 0,
                "google_cost_total": 0,
                "our_margin_amount": 0,
                "our_markup_pct": 0,
                "credit_applied": 0,
            })
            ev["billable_total"] = 0
            fixed_blocked += 1

        uid = str(ev.get("uid", ""))
        month = str(ev.get("month", ""))
        if uid and month:
            events_by_key[f"{uid}_{month}"].append(ev)

    print(f"Fixed {fixed_blocked} blocked events.")
    print(f"Found {len(events_by_key)} uid+month groups to recalculate.")

    # Recalculate each rollup from events
    for key, events in events_by_key.items():
        uid, month = key.split("_", 1)
        total_pages = 0
        total_documents = 0
        total_non_ocr_documents = 0
        total_misc_billable = 0.0
        total_google_cost = 0.0
        total_margin = 0.0
        event_count = len(events)
        warning_count = 0
        blocked_count = 0

        for ev in events:
            total_pages += int(ev.get("page_count", 0) or 0)
            docs = int(ev.get("documents_billed", 0) or ev.get("statements_billed", 0) or 0)
            non_ocr = int(ev.get("non_ocr_documents_billed", 0) or 0)
            total_documents += docs
            total_non_ocr_documents += non_ocr
            total_google_cost += float(ev.get("google_cost_total", 0) or 0)
            total_margin += float(ev.get("our_margin_amount", 0) or 0)
            if ev.get("warning"):
                warning_count += 1
            if ev.get("status") == "blocked":
                blocked_count += 1
            # Legacy events: billed but no docs counted
            ev_billable = float(ev.get("billable_total", 0) or 0)
            if docs == 0 and non_ocr == 0 and ev_billable > 0:
                total_misc_billable += ev_billable

        total_volume = total_documents + total_non_ocr_documents
        if total_volume > 0:
            tp = tier_price_usd(n=total_volume, brackets=brackets)
            ocr_billable = round(tp * fx * total_documents, 6)
            non_ocr_price = max(0.0, tp - g_usd)
            non_ocr_billable = round(non_ocr_price * fx * total_non_ocr_documents, 6)
        else:
            ocr_billable = 0.0
            non_ocr_billable = 0.0
        total_misc_billable = round(total_misc_billable, 6)
        total_billable = round(ocr_billable + non_ocr_billable + total_misc_billable, 6)

        print(f"  {key}: {total_documents} OCR + {total_non_ocr_documents} non-OCR docs, "
              f"misc={total_misc_billable}, total=R {total_billable:.2f}")

        rollups_coll.document(key).set({
            "uid": uid,
            "month": month,
            "total_pages": total_pages,
            "total_documents": total_documents,
            "total_non_ocr_documents": total_non_ocr_documents,
            "total_statements": total_documents,
            "total_billable": total_billable,
            "total_misc_billable": total_misc_billable,
            "total_google_cost": round(total_google_cost, 6),
            "total_margin": round(total_margin, 6),
            "event_count": event_count,
            "warning_count": warning_count,
            "blocked_count": blocked_count,
        }, merge=True)

    print("Done. Rollups recalculated.")


if __name__ == "__main__":
    main()
