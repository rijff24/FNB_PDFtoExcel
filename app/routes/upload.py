import logging
import io
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pypdf import PdfReader
from uuid import uuid4

from app.services.auth import AuthorizedUser
from app.services.access_control import authenticate_request, authenticate_request_with_mode
from app.services.billing import DEFAULT_TIER_BRACKETS, DocumentCostBreakdown, billing_enabled, calculate_document_cost, evaluate_limits, tier_price_usd
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
from app.services.usage_store import get_month_rollup, get_user_billing_settings, record_usage_event
from app.services.preview_store import (
    get_preview_session,
    save_preview_session,
    update_preview_transactions,
)

router = APIRouter()

def _authenticate_mutating_request(request: Request, authorization: str | None, path: str) -> AuthorizedUser:
    auth_result = authenticate_request_with_mode(authorization, request=request, path=path)
    if should_enforce_csrf(request, auth_result.auth_mode):
        validate_double_submit_csrf(request)
    return auth_result.user


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
    g_usd = float(pricing.get("google_usd_per_classified_document", 0.75) or 0.0)
    m_usd = float(pricing.get("margin_per_document_usd", 0.05) or 0.0)
    fx = float(pricing.get("usd_to_zar", 18.5) or 0.0)
    infra = float(pricing.get("infra_monthly_usd", 9.30) or 0.0)
    brackets = pricing.get("tier_brackets") or DEFAULT_TIER_BRACKETS

    rollup = get_month_rollup(user.uid)
    settings = get_user_billing_settings(user.uid)
    ocr_docs = int(rollup.get("total_documents", 0) or rollup.get("total_statements", 0) or 0)
    non_ocr_docs = int(rollup.get("total_non_ocr_documents", 0) or 0)
    misc_billable = float(rollup.get("total_misc_billable", 0.0) or 0.0)
    current_vol = ocr_docs + non_ocr_docs

    if current_vol > 0:
        cur_tp = tier_price_usd(n=current_vol, brackets=brackets)
        current_total = round(
            cur_tp * fx * ocr_docs + max(0.0, cur_tp - g_usd) * fx * non_ocr_docs + misc_billable, 6
        )
    else:
        current_total = round(misc_billable, 6)

    new_vol = current_vol + 1
    new_tp = tier_price_usd(n=new_vol, brackets=brackets)
    if enable_ocr:
        proj_ocr, proj_non_ocr = ocr_docs + 1, non_ocr_docs
    else:
        proj_ocr, proj_non_ocr = ocr_docs, non_ocr_docs + 1
    projected_raw = round(
        new_tp * fx * proj_ocr + max(0.0, new_tp - g_usd) * fx * proj_non_ocr + misc_billable, 6
    )

    if enable_ocr:
        cost = calculate_document_cost(
            document_count=1, current_volume=new_vol,
            google_usd_per_document=g_usd, margin_per_document_usd=m_usd,
            usd_to_zar=fx, infra_monthly_usd=infra, brackets=brackets,
        )
    else:
        non_ocr_price = round(max(0.0, new_tp - g_usd), 6)
        infra_share = round(infra / max(new_vol, 10), 6)
        cost = DocumentCostBreakdown(
            documents=1, tier_price_usd=non_ocr_price,
            google_usd_per_document=0.0, margin_per_document_usd=m_usd,
            infra_share_usd=infra_share, usd_to_zar=fx,
            google_cost_total_zar=0.0,
            our_margin_amount_zar=round(m_usd * fx, 6),
            billable_total_zar=round(non_ocr_price * fx, 6),
            markup_pct=0.0, current_volume=new_vol,
        )

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
                "google_usd_per_document": g_usd,
                "margin_per_document_usd": m_usd,
                "tier_price_usd": cost.tier_price_usd,
                "usd_to_zar": fx,
                "google_cost_total": 0,
                "our_markup_pct": 0,
                "our_margin_amount": 0,
                "billable_total": 0,
                "credit_applied": 0,
                "timestamp": datetime.now(timezone.utc),
            },
            pricing_brackets=brackets,
            usd_to_zar=fx,
            google_usd_per_document=g_usd,
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
        "pricing_brackets": brackets,
        "usd_to_zar": fx,
        "google_usd_per_document": g_usd,
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
    brackets = billing_ctx.get("pricing_brackets")
    fx_rate = float(billing_ctx.get("usd_to_zar", 18.5) or 18.5)
    g_usd_rate = float(billing_ctx.get("google_usd_per_document", 0.75) or 0.75)
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
            "credit_applied": credit_applied if is_billable else 0,
            "billable_total": net_billable,
            "timestamp": datetime.now(timezone.utc),
        },
        pricing_brackets=brackets,
        usd_to_zar=fx_rate,
        google_usd_per_document=g_usd_rate,
    )


@router.get("/")
async def index(request: Request):
    templates = request.app.state.templates
    # Starlette's TemplateResponse signature is: (request, name, context)
    return templates.TemplateResponse(request, "index.html", {"request": request})


@router.post("/extract")
async def extract(
    request: Request,
    file: UploadFile = File(...),
    enable_ocr: bool = Form(False),
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    path = "/extract"

    # Authorization must be enforced before any PDF bytes are read or any OCR/cost work begins.
    user = _authenticate_mutating_request(request, authorization, path)

    log_event(
        logging.INFO,
        "auth_ok_request",
        path=path,
        user_email=user.email,
        uid=user.uid,
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
            text = extract_text_with_document_ai(pdf_bytes)
            transactions = parse_transactions_from_text(text)
        else:
            transactions = parse_transactions_from_pdf_bytes(pdf_bytes)
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
        raise HTTPException(
            status_code=422,
            detail="No transactions were extracted from this statement.",
        )

    excel_bytes = build_excel_bytes(transactions)
    output_filename = (file.filename or "statement").rsplit(".", 1)[0] + "_transactions.xlsx"

    log_event(
        logging.INFO,
        "extract_success",
        path=path,
        user_email=user.email,
        uid=user.uid,
        extra={"transaction_count": len(transactions)},
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
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    path = "/extract/preview"

    # Reuse the same authorization flow as the main extract endpoint.
    user = _authenticate_mutating_request(request, authorization, path)

    log_event(
        logging.INFO,
        "auth_ok_request",
        path=path,
        user_email=user.email,
        uid=user.uid,
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
            document = process_document_with_layout(pdf_bytes)
            transactions = parse_transactions_from_document(document)
            page_count = len(getattr(document, "pages", []) or [])
        else:
            transactions = parse_transactions_from_pdf_bytes_with_layout(pdf_bytes)
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
        raise HTTPException(
            status_code=422,
            detail="No transactions were extracted from this statement.",
        )

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
