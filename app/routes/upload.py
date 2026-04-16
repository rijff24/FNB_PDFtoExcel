import logging
import io
from datetime import datetime, timezone
from pathlib import Path
import html
import re

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pypdf import PdfReader
from uuid import uuid4

from app.services.auth import AuthorizedUser
from app.services.access_control import authenticate_request, authenticate_request_with_mode
from app.services.banks import get_enabled_bank_profile, list_bank_options
from app.services.billing import billing_enabled, build_billing_context, calculate_marginal_cost, evaluate_limits, month_key
from app.services.csrf import should_enforce_csrf, validate_double_submit_csrf
from app.services.admin_store import apply_user_credits, get_billing_pricing_global, get_user_profile
from app.services.document_ai import extract_text_with_document_ai, process_document_with_layout
from app.services.excel_export import build_excel_bytes
from app.services.logging_utils import log_event
from app.services.parser import (
    parse_transactions_from_document,
    parse_transactions_from_pdf_bytes,
    parse_transactions_from_pdf_bytes_with_layout,
    parse_transactions_from_text,
)
from app.services.quotas import (
    QuotaBackendUnavailable,
    QuotaError,
    build_quota_context,
    enforce_file_and_page_caps,
    enforce_redis_quotas,
    load_quota_config,
)
from app.services.usage_store import get_pool_rollup, get_user_billing_settings, record_usage_event, resolve_billing_pool
from app.services.preview_store import (
    get_preview_session,
    save_preview_session,
    update_preview_transactions,
)

router = APIRouter()
DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"
HELP_DOCS_DIR = DOCS_ROOT / "help"
HELP_DOCS: dict[str, dict[str, str]] = {
    "getting-started": {"title": "Getting Started", "file": "getting-started.md"},
    "review-and-export": {"title": "Review and Export", "file": "review-and-export.md"},
    "billing-and-limits": {"title": "Billing and Limits", "file": "billing-and-limits.md"},
    "known-limits": {"title": "Known Limits", "file": "known-limits.md"},
    "support": {"title": "Support", "file": "support.md"},
}

def _authenticate_mutating_request(request: Request, authorization: str | None, path: str) -> AuthorizedUser:
    auth_result = authenticate_request_with_mode(authorization, request=request, path=path)
    if should_enforce_csrf(request, auth_result.auth_mode):
        validate_double_submit_csrf(request)
    return auth_result.user


def _render_markdown_safe(md_text: str) -> str:
    """Render a limited safe subset of markdown to HTML."""
    lines = md_text.splitlines()
    html_parts: list[str] = []
    in_list = False
    in_code = False
    code_buffer: list[str] = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                html_parts.append("<pre><code>" + html.escape("\n".join(code_buffer)) + "</code></pre>")
                code_buffer = []
                in_code = False
            else:
                close_list()
                in_code = True
            continue
        if in_code:
            code_buffer.append(line)
            continue

        if not stripped:
            close_list()
            continue

        if stripped.startswith("#"):
            close_list()
            level = len(stripped) - len(stripped.lstrip("#"))
            level = min(max(level, 1), 4)
            text = stripped[level:].strip()
            html_parts.append(f"<h{level}>{html.escape(text)}</h{level}>")
            continue

        if stripped.startswith("- "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            item = html.escape(stripped[2:].strip())
            html_parts.append(f"<li>{item}</li>")
            continue

        close_list()
        safe = html.escape(stripped)
        safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
        safe = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', safe)
        html_parts.append(f"<p>{safe}</p>")

    close_list()
    if in_code:
        html_parts.append("<pre><code>" + html.escape("\n".join(code_buffer)) + "</code></pre>")
    return "\n".join(html_parts)


def _load_help_doc(slug: str) -> dict[str, str]:
    doc = HELP_DOCS.get(slug)
    if not doc:
        raise HTTPException(status_code=404, detail="Help document not found.")
    file_path = HELP_DOCS_DIR / doc["file"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Help document file missing.")
    raw = file_path.read_text(encoding="utf-8")
    return {
        "slug": slug,
        "title": doc["title"],
        "html": _render_markdown_safe(raw),
    }


def _resolve_help_slug(doc: str | None) -> str:
    default_slug = next(iter(HELP_DOCS.keys()))
    slug = (doc or default_slug).strip().lower()
    if slug not in HELP_DOCS:
        return default_slug
    return slug


def _enforce_ocr_quotas(path: str, user: AuthorizedUser, pdf_bytes: bytes) -> None:
    try:
        cfg = load_quota_config()
    except RuntimeError as exc:
        log_event(
            logging.ERROR,
            "quota_misconfigured",
            path=path,
            user_email=user.email,
            uid=user.uid,
            details=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        ctx = build_quota_context(user, pdf_bytes)
    except QuotaError as exc:
        log_event(
            logging.WARNING,
            "quota_reject_page_cap",
            path=path,
            user_email=user.email,
            uid=user.uid,
            details=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        enforce_file_and_page_caps(ctx, cfg)
    except QuotaError as exc:
        message = str(exc)
        event = "quota_reject_file_size" if "size" in message.lower() else "quota_reject_page_cap"
        log_event(
            logging.WARNING,
            event,
            path=path,
            user_email=user.email,
            uid=user.uid,
            details=message,
        )
        raise HTTPException(status_code=400, detail=message) from exc

    try:
        enforce_redis_quotas(ctx, cfg)
    except QuotaBackendUnavailable as exc:
        log_event(
            logging.ERROR,
            "quota_backend_unavailable",
            path=path,
            user_email=user.email,
            uid=user.uid,
            details=str(exc),
        )
        raise HTTPException(
            status_code=503,
            detail="Quota service temporarily unavailable. Please retry shortly.",
        ) from exc
    except QuotaError as exc:
        message = str(exc)
        event = "quota_reject_rate_limit" if "rate limit" in message.lower() else "quota_reject_daily_pages"
        log_event(
            logging.WARNING,
            event,
            path=path,
            user_email=user.email,
            uid=user.uid,
            details=message,
        )
        raise HTTPException(status_code=429, detail=message) from exc

    log_event(
        logging.INFO,
        "quota_check_pass",
        path=path,
        user_email=user.email,
        uid=user.uid,
        extra={"page_count": ctx.page_count, "file_size_bytes": ctx.file_size_bytes},
    )


def _count_pdf_pages(pdf_bytes: bytes) -> int:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return len(reader.pages)


def _ocr_recommended_detail(bank_id: str) -> dict[str, object]:
    return {
        "code": "ocr_recommended",
        "message": (
            "This statement could not be extracted without OCR. "
            "Please enable OCR and retry."
        ),
        "suggested_enable_ocr": True,
        "bank": bank_id,
    }


def _billing_precheck(
    *,
    path: str,
    user: AuthorizedUser,
    page_count: int,
    enable_ocr: bool,
) -> dict[str, object]:
    if not billing_enabled():
        return {
            "page_count": page_count,
            "cost": None,
            "decision": None,
            "credit_applied": 0.0,
        }

    pricing = get_billing_pricing_global()
    model = build_billing_context(pricing)
    ym = month_key()
    lock = resolve_billing_pool(user.uid, email=user.email, ym=ym, pricing=pricing)
    pool_id = str(lock.get("pool_id") or "")
    scope = str(lock.get("scope") or "user")
    if not pool_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "billing_org_required",
                "message": "Organization assignment is required for billing under current policy.",
            },
        )
    pool_rollup = get_pool_rollup(pool_id, ym)
    settings = get_user_billing_settings(user.uid)
    ocr_docs = int(pool_rollup.get("total_documents", 0) or 0)
    non_ocr_docs = int(pool_rollup.get("total_non_ocr_documents", 0) or 0)
    current_snapshot, projected_snapshot, cost = calculate_marginal_cost(
        enable_ocr=enable_ocr,
        current_ocr_documents=ocr_docs,
        current_non_ocr_documents=non_ocr_docs,
        scope=scope,
        pool_id=pool_id,
        month=ym,
        context=model,
    )
    current_total = round(current_snapshot.total_billable_zar, 6)
    projected_raw = round(projected_snapshot.total_billable_zar, 6)

    profile = get_user_profile(user.uid, user.email)
    available_credits = float(profile.get("credits_balance", 0.0) or 0.0)
    marginal_cost = max(0.0, projected_raw - current_total)
    credit_applied = round(min(available_credits, marginal_cost), 6)
    projected_total = projected_raw - credit_applied
    decision = evaluate_limits(
        settings=settings,
        current_total=current_total,
        projected_total=projected_total,
    )

    if decision.blocked:
        record_usage_event(
            {
                "uid": user.uid,
                "email": user.email,
                "endpoint": path,
                "enable_ocr": enable_ocr,
                "page_count": page_count,
                "documents_billed": 0,
                "statements_billed": 0,
                "status": "blocked",
                "error_code": "billing_limit_reached",
                "warning": decision.warning,
                "google_usd_per_document": model.google_usd_per_document,
                "margin_per_document_usd": model.margin_per_document_usd,
                "tier_price_usd": cost.tier_price_usd,
                "usd_to_zar": model.usd_to_zar,
                "billing_scope": scope,
                "billing_pool_id": pool_id,
                "locked_org_id_for_month": lock.get("org_id_at_lock"),
                "pool_month": ym,
                "google_cost_total": 0,
                "our_markup_pct": 0,
                "our_margin_amount": 0,
                "infra_share_total": 0,
                "billable_total": 0,
                "credit_applied": 0,
                "timestamp": datetime.now(timezone.utc),
            },
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "billing_limit_reached",
                "message": "Monthly billing limit reached.",
                "current_usage": current_total,
                "projected_total": projected_total,
                "limit": settings.monthly_limit_amount,
                "reset_period": datetime.now(timezone.utc).strftime("%Y-%m"),
            },
        )

    return {
        "page_count": page_count,
        "cost": cost,
        "decision": decision,
        "credit_applied": credit_applied,
        "usd_to_zar": model.usd_to_zar,
        "google_usd_per_document": model.google_usd_per_document,
        "billing_scope": scope,
        "billing_pool_id": pool_id,
        "billing_month": ym,
        "locked_org_id_for_month": lock.get("org_id_at_lock"),
        "current_pool_total": current_total,
        "projected_pool_total": projected_raw,
    }


def _record_billing_event(
    *,
    path: str,
    user: AuthorizedUser,
    enable_ocr: bool,
    page_count: int,
    billing_ctx: dict[str, object],
    status: str,
    error_code: str | None = None,
) -> None:
    if not billing_enabled():
        return
    cost = billing_ctx.get("cost")
    decision = billing_ctx.get("decision")
    credit_applied = float(billing_ctx.get("credit_applied", 0.0) or 0.0)
    if cost is None:
        return
    is_ocr_success = enable_ocr and status == "success"
    is_non_ocr_success = (not enable_ocr) and status == "success"
    is_billable = is_ocr_success or is_non_ocr_success
    documents_billed = 1 if is_ocr_success else 0
    non_ocr_documents_billed = 1 if is_non_ocr_success else 0
    net_billable = round(max(0.0, float(cost.billable_total_zar) - credit_applied), 6) if is_billable else 0.0
    if is_billable and credit_applied > 0:
        apply_user_credits(user.uid, credit_applied)
    billing_scope = str(billing_ctx.get("billing_scope") or "user")
    billing_pool_id = str(billing_ctx.get("billing_pool_id") or f"user:{user.uid}")
    billing_month = str(billing_ctx.get("billing_month") or month_key())
    infra_share_total = round(float(getattr(cost, "infra_share_usd", 0.0) or 0.0) * float(cost.usd_to_zar), 6) if is_billable else 0.0
    record_usage_event(
        {
            "uid": user.uid,
            "email": user.email,
            "endpoint": path,
            "enable_ocr": enable_ocr,
            "page_count": page_count,
            "documents_billed": documents_billed,
            "non_ocr_documents_billed": non_ocr_documents_billed,
            "statements_billed": documents_billed,
            "status": status,
            "error_code": error_code,
            "warning": getattr(decision, "warning", None),
            "google_usd_per_document": cost.google_usd_per_document,
            "margin_per_document_usd": cost.margin_per_document_usd,
            "tier_price_usd": cost.tier_price_usd,
            "usd_to_zar": cost.usd_to_zar,
            "google_cost_total": cost.google_cost_total_zar if is_billable else 0,
            "our_markup_pct": cost.markup_pct if is_billable else 0,
            "our_margin_amount": cost.our_margin_amount_zar if is_billable else 0,
            "infra_share_total": infra_share_total,
            "credit_applied": credit_applied if is_billable else 0,
            "billable_total": net_billable,
            "billing_scope": billing_scope,
            "billing_pool_id": billing_pool_id,
            "pool_month": billing_month,
            "locked_org_id_for_month": billing_ctx.get("locked_org_id_for_month"),
            "timestamp": datetime.now(timezone.utc),
        },
    )


@router.get("/")
async def index(request: Request):
    templates = request.app.state.templates
    # Starlette's TemplateResponse signature is: (request, name, context)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "bank_options": list_bank_options(),
        },
    )


@router.get("/help")
async def help_page(request: Request, doc: str | None = None, authorization: str | None = Header(default=None)):
    user = authenticate_request(authorization, request=request, path="/help")
    templates = request.app.state.templates
    slug = _resolve_help_slug(doc)
    selected = _load_help_doc(slug)
    docs_nav = [{"slug": k, "title": v["title"]} for k, v in HELP_DOCS.items()]
    return templates.TemplateResponse(
        request,
        "help.html",
        {
            "request": request,
            "user_email": user.email,
            "docs_nav": docs_nav,
            "selected_doc": selected,
        },
    )


@router.get("/help/data")
async def help_data(request: Request, doc: str | None = None, authorization: str | None = Header(default=None)) -> JSONResponse:
    _ = authenticate_request(authorization, request=request, path="/help/data")
    slug = _resolve_help_slug(doc)
    selected = _load_help_doc(slug)
    docs_nav = [{"slug": key, "title": meta["title"]} for key, meta in HELP_DOCS.items()]
    return JSONResponse(
        {
            "selected_doc": selected,
            "docs_nav": docs_nav,
        },
    )


@router.post("/extract")
async def extract(
    request: Request,
    file: UploadFile = File(...),
    enable_ocr: bool = Form(False),
    bank: str = Form("fnb"),
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    path = "/extract"

    # Authorization must be enforced before any PDF bytes are read or any OCR/cost work begins.
    user = _authenticate_mutating_request(request, authorization, path)

    selected_bank_profile = get_enabled_bank_profile(bank)
    selected_bank = selected_bank_profile.id

    log_event(
        logging.INFO,
        "auth_ok_request",
        path=path,
        user_email=user.email,
        uid=user.uid,
        extra={"bank": selected_bank},
    )

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    page_count = _count_pdf_pages(pdf_bytes)
    billing_ctx = _billing_precheck(path=path, user=user, page_count=page_count, enable_ocr=enable_ocr)

    try:
        if enable_ocr:
            _enforce_ocr_quotas(path, user, pdf_bytes)
            text = extract_text_with_document_ai(pdf_bytes, bank_id=selected_bank)
            transactions = parse_transactions_from_text(text, bank_id=selected_bank)
        else:
            transactions = parse_transactions_from_pdf_bytes(pdf_bytes, bank_id=selected_bank)
    except RuntimeError as exc:
        _record_billing_event(
            path=path,
            user=user,
            enable_ocr=enable_ocr,
            page_count=page_count,
            billing_ctx=billing_ctx,
            status="error",
            error_code="extract_failed_upstream",
        )
        log_event(
            logging.ERROR,
            "extract_failed_upstream",
            path=path,
            user_email=user.email,
            uid=user.uid,
            details=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not transactions:
        _record_billing_event(
            path=path,
            user=user,
            enable_ocr=enable_ocr,
            page_count=page_count,
            billing_ctx=billing_ctx,
            status="no_transactions",
            error_code="no_transactions",
        )
        log_event(
            logging.WARNING,
            "extract_no_transactions",
            path=path,
            user_email=user.email,
            uid=user.uid,
            details="No transactions were extracted from this statement.",
        )
        if not enable_ocr and selected_bank_profile.recommend_ocr_when_empty:
            raise HTTPException(status_code=422, detail=_ocr_recommended_detail(selected_bank))
        raise HTTPException(status_code=422, detail="No transactions were extracted from this statement.")

    excel_bytes = build_excel_bytes(transactions)
    output_filename = (file.filename or "statement").rsplit(".", 1)[0] + "_transactions.xlsx"

    log_event(
        logging.INFO,
        "extract_success",
        path=path,
        user_email=user.email,
        uid=user.uid,
        extra={"transaction_count": len(transactions), "bank": selected_bank},
    )
    _record_billing_event(
        path=path,
        user=user,
        enable_ocr=enable_ocr,
        page_count=page_count,
        billing_ctx=billing_ctx,
        status="success",
    )

    return StreamingResponse(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{output_filename}"'},
    )


@router.post("/extract/preview")
async def extract_preview(
    request: Request,
    file: UploadFile = File(...),
    enable_ocr: bool = Form(False),
    bank: str = Form("fnb"),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    path = "/extract/preview"

    # Reuse the same authorization flow as the main extract endpoint.
    user = _authenticate_mutating_request(request, authorization, path)

    selected_bank_profile = get_enabled_bank_profile(bank)
    selected_bank = selected_bank_profile.id

    log_event(
        logging.INFO,
        "auth_ok_request",
        path=path,
        user_email=user.email,
        uid=user.uid,
        extra={"bank": selected_bank},
    )

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    page_count = _count_pdf_pages(pdf_bytes)
    billing_ctx = _billing_precheck(path=path, user=user, page_count=page_count, enable_ocr=enable_ocr)

    try:
        if enable_ocr:
            _enforce_ocr_quotas(path, user, pdf_bytes)
            document = process_document_with_layout(pdf_bytes, bank_id=selected_bank)
            transactions = parse_transactions_from_document(document, bank_id=selected_bank)
            page_count = len(getattr(document, "pages", []) or [])
        else:
            transactions = parse_transactions_from_pdf_bytes_with_layout(pdf_bytes, bank_id=selected_bank)
            page_count = max(
                [int(tx.get("page_index", 0)) for tx in transactions if isinstance(tx, dict)] + [-1]
            ) + 1
    except RuntimeError as exc:
        _record_billing_event(
            path=path,
            user=user,
            enable_ocr=enable_ocr,
            page_count=page_count,
            billing_ctx=billing_ctx,
            status="error",
            error_code="extract_preview_failed_upstream",
        )
        log_event(
            logging.ERROR,
            "extract_preview_failed_upstream",
            path=path,
            user_email=user.email,
            uid=user.uid,
            details=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not transactions:
        _record_billing_event(
            path=path,
            user=user,
            enable_ocr=enable_ocr,
            page_count=page_count,
            billing_ctx=billing_ctx,
            status="no_transactions",
            error_code="no_transactions",
        )
        log_event(
            logging.WARNING,
            "extract_preview_no_transactions",
            path=path,
            user_email=user.email,
            uid=user.uid,
            details="No transactions were extracted from this statement.",
        )
        if not enable_ocr and selected_bank_profile.recommend_ocr_when_empty:
            raise HTTPException(status_code=422, detail=_ocr_recommended_detail(selected_bank))
        raise HTTPException(status_code=422, detail="No transactions were extracted from this statement.")

    # Attach a stable per-session ID for the review UI and store PDF bytes for rendering.
    session_id = uuid4().hex

    # Ensure each transaction has an ID, needs_review flag, and backwards-compatible
    # bbox alias (equal to bbox_row) for the frontend.
    enriched_transactions: list[dict[str, object]] = []
    for idx, tx in enumerate(transactions, start=1):
        tx_with_meta: dict[str, object] = dict(tx)  # type: ignore[arg-type]
        if "id" not in tx_with_meta:
            tx_with_meta["id"] = f"tx_{idx:04d}"
        if "needs_review" not in tx_with_meta:
            tx_with_meta["needs_review"] = False
        if "bbox_row" in tx_with_meta and "bbox" not in tx_with_meta:
            tx_with_meta["bbox"] = tx_with_meta["bbox_row"]
        if "bank_id" not in tx_with_meta:
            tx_with_meta["bank_id"] = selected_bank
        enriched_transactions.append(tx_with_meta)

    save_preview_session(session_id, pdf_bytes, enriched_transactions)

    # Normalized page metadata (0-1 coordinates align to PDF.js overlay).
    pages = [
        {"page_index": i, "width": 1.0, "height": 1.0}
        for i in range(max(0, page_count))
    ]

    log_event(
        logging.INFO,
        "extract_preview_success",
        path=path,
        user_email=user.email,
        uid=user.uid,
        extra={
            "transaction_count": len(enriched_transactions),
            "session_id": session_id,
            "bank": selected_bank,
        },
    )
    _record_billing_event(
        path=path,
        user=user,
        enable_ocr=enable_ocr,
        page_count=page_count,
        billing_ctx=billing_ctx,
        status="success",
    )

    decision = billing_ctx.get("decision")
    warning = getattr(decision, "warning", None)
    projected_total = getattr(decision, "projected_total", None)
    limit_remaining = getattr(decision, "limit_remaining", None)

    return JSONResponse(
        {
            "session_id": session_id,
            "transactions": enriched_transactions,
            "pages": pages,
            "billing_warning": warning,
            "billing_projected_total": projected_total,
            "billing_limit_remaining": limit_remaining,
        },
    )


@router.get("/preview/data/{session_id}")
async def preview_data(session_id: str, request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
    _ = authenticate_request(authorization, request=request, path=f"/preview/data/{session_id}")
    session = get_preview_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Preview session not found.")

    return JSONResponse(
        {
            "session_id": session_id,
            "transactions": session.get("transactions") or [],
        },
    )


@router.put("/preview/data/{session_id}")
async def preview_data_update(
    session_id: str,
    request: Request,
    payload: dict,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """
    Overwrite the stored transactions for a preview session.

    This is used by the review UI to persist inline edits made by the user.
    """
    _ = _authenticate_mutating_request(request, authorization, path=f"/preview/data/{session_id}")

    transactions = payload.get("transactions")
    if not isinstance(transactions, list):
        raise HTTPException(status_code=400, detail="Invalid payload: 'transactions' must be a list.")

    updated = update_preview_transactions(session_id, transactions)  # type: ignore[arg-type]
    if not updated:
        raise HTTPException(status_code=404, detail="Preview session not found.")

    return JSONResponse(
        {
            "session_id": session_id,
            "transactions": transactions,
        },
    )


@router.get("/preview/pdf/{session_id}")
async def preview_pdf(session_id: str, request: Request, authorization: str | None = Header(default=None)) -> Response:
    _ = authenticate_request(authorization, request=request, path=f"/preview/pdf/{session_id}")
    session = get_preview_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Preview session not found.")

    pdf_bytes = session.get("pdf_bytes") or b""
    # Return the PDF as a simple byte response to avoid StreamingResponse
    # accidentally iterating over the bytes object as ints.
    return Response(content=pdf_bytes, media_type="application/pdf")  # type: ignore[arg-type]


@router.get("/review")
async def review(request: Request, session_id: str) -> JSONResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "review.html",
        {"request": request, "session_id": session_id},
    )


@router.get("/preview/download/{session_id}")
async def preview_download(
    session_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """
    Download an Excel file built from the (possibly edited) preview transactions.
    """
    _ = authenticate_request(authorization, request=request, path=f"/preview/download/{session_id}")
    session = get_preview_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Preview session not found.")

    transactions = (session or {}).get("transactions") or []
    if not isinstance(transactions, list):
        raise HTTPException(status_code=500, detail="Preview session is corrupted.")

    excel_bytes = build_excel_bytes(transactions)  # type: ignore[arg-type]

    return StreamingResponse(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="statement_preview_edited.xlsx"'},
    )
