import os
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.admin_store import _client


RECON_COLLECTION = os.getenv("RECONCILIATION_COLLECTION", "cost_reconciliation_daily").strip() or "cost_reconciliation_daily"
USAGE_COLLECTION = os.getenv("BILLING_USAGE_COLLECTION", "usage_events").strip() or "usage_events"


def _recon_tz():
    tz_name = os.getenv("RECONCILIATION_TZ", "Africa/Johannesburg").strip() or "Africa/Johannesburg"
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        return timezone.utc


def _alert_pct() -> float:
    raw = os.getenv("RECONCILIATION_ALERT_PCT", "15").strip()
    return max(0.0, float(raw or "15"))


def _billing_table() -> str:
    return os.getenv("BQ_BILLING_TABLE", "").strip()


def _safe_table_path(path: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-\.]+", path):
        raise RuntimeError("Invalid BQ_BILLING_TABLE path.")
    return path


def _fetch_app_usage_by_day(start_day: date, end_day: date) -> dict[str, dict[str, float]]:
    from google.cloud.firestore_v1.base_query import FieldFilter

    client = _client()
    out: dict[str, dict[str, float]] = defaultdict(lambda: {"app_estimate": 0.0, "ocr_docs": 0.0, "ocr_pages": 0.0})

    month_keys = {start_day.strftime("%Y%m"), end_day.strftime("%Y%m")}
    for ym in month_keys:
        query = (
            client.collection(USAGE_COLLECTION)
            .where(filter=FieldFilter("month", "==", ym))
            .where(filter=FieldFilter("enable_ocr", "==", True))
            .where(filter=FieldFilter("status", "==", "success"))
            .limit(20000)
        )
        for snap in query.stream():
            row = snap.to_dict() or {}
            day_str = str(row.get("day") or "")
            if day_str:
                try:
                    day = date.fromisoformat(day_str)
                except ValueError:
                    day = None
            else:
                ts = row.get("timestamp")
                day = ts.astimezone(_recon_tz()).date() if isinstance(ts, datetime) else None
            if day is None or day < start_day or day > end_day:
                continue
            key = day.isoformat()
            out[key]["app_estimate"] += float(row.get("billable_total", 0.0) or 0.0)
            out[key]["ocr_docs"] += 1.0
            out[key]["ocr_pages"] += float(row.get("page_count", 0) or 0)
    for k in out:
        out[k]["app_estimate"] = round(out[k]["app_estimate"], 6)
        out[k]["ocr_docs"] = round(out[k]["ocr_docs"], 0)
        out[k]["ocr_pages"] = round(out[k]["ocr_pages"], 0)
    return out


def _fetch_financial_truth_by_day(start_day: date, end_day: date) -> tuple[dict[str, float], bool]:
    table = _billing_table()
    if not table:
        return {}, False
    from google.cloud import bigquery

    client = bigquery.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None)
    safe_table = _safe_table_path(table)
    query = f"""
        SELECT
          DATE(usage_start_time) AS usage_date,
          SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS net_cost
        FROM `{safe_table}`
        WHERE DATE(usage_start_time) BETWEEN @start_date AND @end_date
          AND (
            LOWER(service.description) LIKE '%document ai%'
            OR LOWER(sku.description) LIKE '%bank statement parser%'
            OR LOWER(sku.description) LIKE '%document ai%'
          )
        GROUP BY usage_date
    """
    job = client.query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start_date", "DATE", start_day.isoformat()),
                bigquery.ScalarQueryParameter("end_date", "DATE", end_day.isoformat()),
            ]
        ),
    )
    rows = list(job.result())
    out: dict[str, float] = {}
    for r in rows:
        k = r["usage_date"].isoformat()
        out[k] = round(float(r["net_cost"] or 0.0), 6)
    return out, True


def _build_rows(
    *,
    start_day: date,
    end_day: date,
    app_by_day: dict[str, dict[str, float]],
    truth_by_day: dict[str, float],
    truth_available: bool,
) -> list[dict[str, Any]]:
    alert = _alert_pct()
    rows: list[dict[str, Any]] = []
    day = start_day
    while day <= end_day:
        key = day.isoformat()
        app = app_by_day.get(key, {"app_estimate": 0.0, "ocr_docs": 0.0, "ocr_pages": 0.0})
        financial = truth_by_day.get(key) if truth_available else None
        app_estimate = float(app.get("app_estimate", 0.0) or 0.0)
        ocr_docs = int(app.get("ocr_docs", 0.0) or 0)
        ocr_pages = int(app.get("ocr_pages", 0.0) or 0)
        if financial is None:
            variance_amount = round(app_estimate, 6)
            variance_pct = 0.0
            status = "awaiting_data"
        else:
            variance_amount = round(app_estimate - float(financial), 6)
            if float(financial) > 0:
                variance_pct = round(abs(variance_amount) / float(financial) * 100.0, 3)
            else:
                variance_pct = 0.0 if app_estimate <= 0 else 100.0
            status = "warning" if variance_pct > alert else "ok"
        rows.append(
            {
                "day": key,
                "app_estimate": round(app_estimate, 6),
                "financial_truth": round(float(financial), 6) if financial is not None else None,
                "variance_amount": variance_amount,
                "variance_pct": variance_pct,
                "status": status,
                "ocr_docs": ocr_docs,
                "ocr_pages": ocr_pages,
            }
        )
        day += timedelta(days=1)
    rows.sort(key=lambda r: r["day"], reverse=True)
    return rows


def _persist_rows(rows: list[dict[str, Any]]) -> None:
    client = _client()
    now = datetime.now(UTC)
    for row in rows:
        key = str(row["day"])
        client.collection(RECON_COLLECTION).document(key).set({**row, "updated_at": now}, merge=True)


def _month_start(today: date) -> date:
    return date(today.year, today.month, 1)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "app_estimate_mtd": 0.0,
            "financial_truth_mtd": None,
            "variance_amount_mtd": None,
            "variance_pct_mtd": None,
            "status": "awaiting_data",
            "last_reconciled_at": None,
        }
    today = datetime.now(_recon_tz()).date()
    start = _month_start(today)
    mtd = [r for r in rows if start.isoformat() <= str(r["day"]) <= today.isoformat()]
    app_total = round(sum(float(r.get("app_estimate", 0.0) or 0.0) for r in mtd), 6)
    truth_values = [r.get("financial_truth") for r in mtd if r.get("financial_truth") is not None]
    truth_total = round(sum(float(v or 0.0) for v in truth_values), 6) if truth_values else None
    if truth_total is None:
        var_amt = None
        var_pct = None
        status = "awaiting_data"
    else:
        var_amt = round(app_total - truth_total, 6)
        if truth_total > 0:
            var_pct = round(abs(var_amt) / truth_total * 100.0, 3)
        else:
            var_pct = 0.0 if app_total <= 0 else 100.0
        status = "warning" if var_pct > _alert_pct() else "ok"
    return {
        "app_estimate_mtd": app_total,
        "financial_truth_mtd": truth_total,
        "variance_amount_mtd": var_amt,
        "variance_pct_mtd": var_pct,
        "status": status,
        "last_reconciled_at": datetime.now(UTC).isoformat(),
    }


def run_reconciliation(*, days: int = 35, persist: bool = True) -> dict[str, Any]:
    today = datetime.now(_recon_tz()).date()
    start = today - timedelta(days=max(1, int(days)) - 1)
    app = _fetch_app_usage_by_day(start, today)
    truth, truth_available = _fetch_financial_truth_by_day(start, today)
    rows = _build_rows(start_day=start, end_day=today, app_by_day=app, truth_by_day=truth, truth_available=truth_available)
    if persist:
        _persist_rows(rows)
    summary = _summary(rows)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": days,
        "alert_pct_threshold": _alert_pct(),
        "truth_available": truth_available,
        "summary": summary,
        "rows": rows,
    }


def get_reconciliation_history(*, days: int = 35) -> dict[str, Any]:
    from google.cloud.firestore_v1.base_query import FieldFilter

    client = _client()
    today = datetime.now(_recon_tz()).date()
    start = today - timedelta(days=max(1, int(days)) - 1)
    out: list[dict[str, Any]] = []
    query = (
        client.collection(RECON_COLLECTION)
        .where(filter=FieldFilter("day", ">=", start.isoformat()))
        .where(filter=FieldFilter("day", "<=", today.isoformat()))
        .limit(500)
    )
    for snap in query.stream():
        row = snap.to_dict() or {}
        day = str(row.get("day") or snap.id)
        row["day"] = day
        out.append(row)
    out.sort(key=lambda r: str(r.get("day")), reverse=True)
    summary = _summary(out)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": days,
        "alert_pct_threshold": _alert_pct(),
        "truth_available": any(r.get("financial_truth") is not None for r in out),
        "summary": summary,
        "rows": out,
    }
