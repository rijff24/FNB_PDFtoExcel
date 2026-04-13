import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional

import pdfplumber

try:  # Document AI is optional at import time
    from google.cloud.documentai import Document
except Exception:  # noqa: BLE001
    Document = Any  # type: ignore[assignment]

from app.services.banks import BankParserProfile, get_enabled_bank_profile
from app.services.logging_utils import log_event


DATE_AT_START_RE = re.compile(
    r"^\s*(?P<date>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2})\b",
)
DATE_DAY_MON_RE = re.compile(r"^\s*(?P<date>\d{1,2}\s+[A-Za-z]{3})\b", re.IGNORECASE)
DATE_DAY_MON_YEAR_RE = re.compile(
    r"^\s*(?P<date>\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b", re.IGNORECASE,
)
NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?")
MONEY_RE = re.compile(r"\(?\d{1,3}(?:,\d{3})*\.\d{2}\)?|\(?\d+\.\d{2}\)?")


@dataclass
class ParsedToken:
    text: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass
class ParsedLine:
    page_index: int
    text: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    tokens: list[ParsedToken] = field(default_factory=list)


def parse_transactions_from_pdf_bytes(
    pdf_bytes: bytes, forced_year: int | None = None, bank_id: str = "fnb",
) -> list[dict[str, Any]]:
    """Non-OCR path: keep existing pdfplumber + text-only parser."""
    all_text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            all_text_parts.append(page.extract_text() or "")
    return parse_transactions_from_text(
        "\n".join(all_text_parts), forced_year=forced_year, bank_id=bank_id,
    )


def parse_transactions_from_pdf_bytes_with_layout(
    pdf_bytes: bytes, forced_year: int | None = None, bank_id: str = "fnb",
) -> list[dict[str, Any]]:
    """
    Non-OCR parser with layout metadata for review/highlighting.

    Uses pdfplumber word coordinates to build line-level normalized bboxes and
    emits transactions compatible with the review UI contract.
    """
    rows: list[dict[str, Any]] = []
    profile = get_enabled_bank_profile(bank_id)
    if profile.id != "fnb":
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_index, page in enumerate(pdf.pages):
                line_entries = _extract_text_line_entries(page, page_index)
                if not line_entries:
                    line_entries = _build_fallback_line_entries_from_text(
                        page.extract_text() or "", page_index,
                    )
                rows.extend(
                    _parse_non_fnb_line_entries_with_layout(
                        line_entries, profile=profile, forced_year=forced_year,
                    )
                )
        return rows
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_index, page in enumerate(pdf.pages):
            line_entries = _extract_pdfplumber_lines(page, page_index)
            prev_date: str | None = None
            for entry in line_entries:
                tx = _parse_pdfplumber_line(
                    entry, forced_year=forced_year, prev_date=prev_date, profile=profile,
                )
                if tx is None:
                    continue
                rows.append(tx)
                if tx.get("date"):
                    prev_date = str(tx["date"])
    return rows


def _parse_non_fnb_line_entries_with_layout(
    line_entries: list[dict[str, Any]],
    profile: BankParserProfile,
    forced_year: int | None,
) -> list[dict[str, Any]]:
    if profile.id == "capitec":
        return _parse_capitec_business_line_entries(line_entries, profile=profile, forced_year=forced_year)
    if profile.id == "capitec_personal":
        return _parse_capitec_personal_line_entries(line_entries, profile=profile, forced_year=forced_year)
    if profile.id == "standard_bank":
        return _parse_standard_bank_line_entries(line_entries, profile=profile, forced_year=forced_year)

    out: list[dict[str, Any]] = []
    prev_date: str | None = None
    for entry in line_entries:
        tx = _parse_pdfplumber_line(entry, forced_year=forced_year, prev_date=prev_date, profile=profile)
        if tx is None:
            continue
        out.append(tx)
        if tx.get("date"):
            prev_date = str(tx["date"])
    return out


def parse_transactions_from_text(
    text: str, forced_year: int | None = None, bank_id: str = "fnb",
) -> list[dict[str, Any]]:
    profile = get_enabled_bank_profile(bank_id)
    if profile.text_rule_set == "capitec_personal":
        return _parse_capitec_personal_transactions(text, forced_year=forced_year)
    if profile.text_rule_set == "capitec":
        return _parse_capitec_transactions(text, forced_year=forced_year)
    if profile.text_rule_set == "standard_bank":
        return _parse_standard_bank_transactions(text, forced_year=forced_year)
    return _parse_fnb_transactions(text, forced_year=forced_year)


def _parse_fnb_transactions(text: str, forced_year: int | None = None) -> list[dict[str, Any]]:
    return _parse_transactions_from_text_generic(text, forced_year=forced_year)


def _parse_capitec_transactions(text: str, forced_year: int | None = None) -> list[dict[str, Any]]:
    prepared = _preprocess_bank_text(text)
    rows = _parse_capitec_business_text(prepared, forced_year=forced_year)
    if rows:
        return rows
    return _parse_transactions_from_text_generic(prepared, forced_year=forced_year)


def _parse_capitec_personal_transactions(
    text: str, forced_year: int | None = None,
) -> list[dict[str, Any]]:
    prepared = _preprocess_bank_text(text)
    rows = _parse_capitec_personal_text(prepared, forced_year=forced_year)
    if rows:
        return rows
    return _parse_transactions_from_text_generic(prepared, forced_year=forced_year)


def _parse_standard_bank_transactions(
    text: str, forced_year: int | None = None,
) -> list[dict[str, Any]]:
    prepared = _preprocess_bank_text(text)
    rows = _parse_standard_bank_text(prepared, forced_year=forced_year)
    if rows:
        return rows
    return _parse_transactions_from_text_generic(prepared, forced_year=forced_year)


def _parse_capitec_business_text(text: str, forced_year: int | None = None) -> list[dict[str, Any]]:
    date_line_re = re.compile(
        r"^(?P<post>\d{2}/\d{2}/\d{2,4})\s+(?P<trans>\d{2}/\d{2}/\d{2,4})\s+(?P<body>.+)$",
    )
    skip_prefixes = (
        "interest rate",
        "statement no.",
        "post trans.",
        "date date",
        "no limit",
        "the prime lending rate",
        "statements are accepted",
        "24hr business banking",
        "capitec bank",
        "business account statement",
        "fee total",
        "vat @",
        "vat total",
        "all fees charged",
    )
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush_current() -> None:
        nonlocal current
        if not current:
            return
        description = str(current.get("description") or "").strip()
        reference = str(current.get("reference") or "").strip()
        row = {
            "date": current.get("date"),
            "description": description,
            "amount": current.get("amount"),
            "balance": current.get("balance"),
            "charges": current.get("charges"),
            "transaction_date": current.get("transaction_date"),
            "post_date": current.get("post_date"),
            "reference": reference or None,
        }
        if row["date"] and row["amount"] is not None and row["balance"] is not None and row["description"]:
            rows.append(row)
        current = None

    for raw in text.splitlines():
        line = " ".join(raw.strip().split())
        if not line:
            continue
        line_lower = line.lower()
        m_opening = re.match(
            r"^balance brought forward\s+(?P<balance>[+-]?\d{1,3}(?:[ ,]\d{3})*\.\d{2}[+-]?)$",
            line_lower,
        )
        if m_opening:
            flush_current()
            opening_balance = _parse_signed_money_token(m_opening.group("balance"))
            if opening_balance is not None:
                rows.append(
                    {
                        "date": None,
                        "post_date": None,
                        "transaction_date": None,
                        "description": "Balance brought forward",
                        "reference": None,
                        "charges": None,
                        "amount": None,
                        "balance": opening_balance,
                    }
                )
            continue
        if line_lower.startswith(skip_prefixes):
            flush_current()
            continue

        m = date_line_re.match(line)
        if not m:
            if current:
                if not re.match(r"^page[:\s]+\d+", line_lower):
                    ref = str(current.get("reference") or "")
                    current["reference"] = f"{ref} {line}".strip() if ref else line
            continue

        flush_current()
        body = m.group("body")
        money_tokens = list(re.finditer(r"[+-]?\d{1,3}(?:[ ,]\d{3})*\.\d{2}[+-]?", body))
        if len(money_tokens) < 2:
            continue
        amount_match = money_tokens[-2]
        balance_match = money_tokens[-1]
        amount = _parse_signed_money_token(amount_match.group(0))
        balance = _parse_signed_money_token(balance_match.group(0))
        if amount is None or balance is None:
            continue
        fees: float | None = None
        text_cutoff = amount_match.start()
        if len(money_tokens) >= 3:
            fees = _parse_signed_money_token(money_tokens[-3].group(0))
            text_cutoff = money_tokens[-3].start()
        text_before_money = body[:text_cutoff].strip()
        description, reference = _split_capitec_business_desc_ref(text_before_money)
        if not description:
            continue
        post_date = _normalize_date(m.group("post"), forced_year=forced_year)
        trans_date = _normalize_date(m.group("trans"), forced_year=forced_year)
        current = {
            "date": post_date,
            "post_date": post_date,
            "transaction_date": trans_date,
            "description": description,
            "reference": reference,
            "charges": fees,
            "amount": amount,
            "balance": balance,
        }
    flush_current()
    return rows


def _parse_capitec_business_line_entries(
    line_entries: list[dict[str, Any]],
    profile: BankParserProfile,
    forced_year: int | None = None,
) -> list[dict[str, Any]]:
    date_line_re = re.compile(
        r"^(?P<post>\d{2}/\d{2}/\d{2,4})\s+(?P<trans>\d{2}/\d{2}/\d{2,4})\s+(?P<body>.+)$",
    )
    skip_prefixes = (
        "interest rate",
        "statement no.",
        "post trans.",
        "date date",
        "no limit",
        "the prime lending rate",
        "statements are accepted",
        "24hr business banking",
        "capitec bank",
        "business account statement",
        "fee total",
        "vat @",
        "vat total",
        "all fees charged",
    )
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_boxes: list[dict[str, float]] = []

    def flush_current() -> None:
        nonlocal current, current_boxes
        if not current:
            return
        merged_bbox = _merge_bboxes(current_boxes) or _default_bbox()
        cell_boxes = _capitec_business_cell_bboxes(merged_bbox)
        description = str(current.get("description") or "").strip()
        reference = str(current.get("reference") or "").strip()
        row = {
            "date": current.get("date"),
            "post_date": current.get("post_date"),
            "transaction_date": current.get("transaction_date"),
            "description": description,
            "reference": reference or None,
            "amount": current.get("amount"),
            "balance": current.get("balance"),
            "charges": current.get("charges"),
            "page_index": int(current.get("page_index", 0)),
            "needs_review": current.get("amount") is None or current.get("balance") is None,
            "bbox": merged_bbox,
            "bbox_row": merged_bbox,
            "bbox_date": cell_boxes["post_date"],
            "bbox_post_date": cell_boxes["post_date"],
            "bbox_transaction_date": cell_boxes["transaction_date"],
            "bbox_description": cell_boxes["description"],
            "bbox_reference": cell_boxes["reference"],
            "bbox_amount": cell_boxes["amount"],
            "bbox_balance": cell_boxes["balance"],
            "bbox_charges": cell_boxes["charges"],
            "bank_id": profile.id,
        }
        if row["description"] and row["balance"] is not None:
            rows.append(row)
        current = None
        current_boxes = []

    for entry in line_entries:
        line = _preprocess_bank_text(str(entry.get("text", "")).strip())
        bbox = entry.get("bbox_row") or _default_bbox()
        page_index = int(entry.get("page_index", 0))
        if not line:
            continue
        line_lower = line.lower()
        m_opening = re.match(
            r"^balance brought forward\s+(?P<balance>[+-]?\d{1,3}(?:[ ,]\d{3})*\.\d{2}[+-]?)$",
            line_lower,
        )
        if m_opening:
            flush_current()
            opening_balance = _parse_signed_money_token(m_opening.group("balance"))
            if opening_balance is not None:
                row_bbox = bbox
                cell_boxes = _capitec_business_cell_bboxes(row_bbox)
                rows.append(
                    {
                        "date": None,
                        "post_date": None,
                        "transaction_date": None,
                        "description": "Balance brought forward",
                        "reference": None,
                        "amount": None,
                        "balance": opening_balance,
                        "charges": None,
                        "page_index": page_index,
                        "needs_review": True,
                        "bbox": row_bbox,
                        "bbox_row": row_bbox,
                        "bbox_date": cell_boxes["post_date"],
                        "bbox_post_date": cell_boxes["post_date"],
                        "bbox_transaction_date": cell_boxes["transaction_date"],
                        "bbox_description": cell_boxes["description"],
                        "bbox_reference": cell_boxes["reference"],
                        "bbox_amount": cell_boxes["amount"],
                        "bbox_balance": cell_boxes["balance"],
                        "bbox_charges": cell_boxes["charges"],
                        "bank_id": profile.id,
                    }
                )
            continue
        if line_lower.startswith(skip_prefixes):
            flush_current()
            continue

        entry_words = entry.get("words") or []
        col_bounds = _COLUMN_BOUNDARIES["capitec"]
        desc_ref_boundary = col_bounds["desc_ref_split"]
        charges_x_start = col_bounds["charges_x_start"]

        m = date_line_re.match(line)
        if not m:
            if current:
                if not re.match(r"^page[:\s]+\d+", line_lower):
                    if entry_words:
                        cont_words = [w for w in entry_words if w["x_min"] < charges_x_start]
                        _, cont_ref = _split_words_by_x(cont_words, desc_ref_boundary)
                        if not cont_ref:
                            cont_ref = line
                    else:
                        cont_ref = line
                    ref = str(current.get("reference") or "")
                    current["reference"] = f"{ref} {cont_ref}".strip() if ref else cont_ref
                    current_boxes.append(bbox)
            continue

        flush_current()
        body = m.group("body")
        money_tokens = list(re.finditer(r"[+-]?\d{1,3}(?:[ ,]\d{3})*\.\d{2}[+-]?", body))
        if len(money_tokens) < 2:
            continue

        if entry_words:
            money_vals = _extract_money_by_position(entry_words, col_bounds)
            fees = money_vals["charges"]
            amount = money_vals["amount"]
            balance = money_vals["balance"]
            if balance is None:
                continue

            text_words = [w for w in entry_words if w["x_min"] < charges_x_start]
            desc_col_start = 0.189
            text_words = [w for w in text_words if w["x_min"] >= desc_col_start]
            description, reference = _split_words_by_x(text_words, desc_ref_boundary)
        else:
            amount_match = money_tokens[-2]
            balance_match = money_tokens[-1]
            amount = _parse_signed_money_token(amount_match.group(0))
            balance = _parse_signed_money_token(balance_match.group(0))
            if amount is None or balance is None:
                continue
            fees: float | None = None
            if len(money_tokens) >= 3:
                fees = _parse_signed_money_token(money_tokens[-3].group(0))
            text_cutoff = amount_match.start()
            if len(money_tokens) >= 3:
                text_cutoff = money_tokens[-3].start()
            text_before_money = body[:text_cutoff].strip()
            description, reference = _split_capitec_business_desc_ref(text_before_money)

        if not description:
            continue
        post_date = _normalize_date(m.group("post"), forced_year=forced_year)
        trans_date = _normalize_date(m.group("trans"), forced_year=forced_year)
        current = {
            "date": post_date,
            "post_date": post_date,
            "transaction_date": trans_date,
            "description": description,
            "reference": reference,
            "charges": fees,
            "amount": amount,
            "balance": balance,
            "page_index": page_index,
        }
        current_boxes = [bbox]

    flush_current()
    return rows


def _parse_capitec_personal_line_entries(
    line_entries: list[dict[str, Any]],
    profile: BankParserProfile,
    forced_year: int | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in line_entries:
        line = _preprocess_bank_text(str(entry.get("text", "")).strip())
        m = re.match(r"^(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<body>.+)$", line)
        if not m:
            continue
        body = m.group("body")
        money_tokens = list(re.finditer(r"[+-]?\d{1,3}(?:[ ,]\d{3})*\.\d{2}[+-]?", body))
        if len(money_tokens) < 2:
            continue
        amount_match = money_tokens[-2]
        balance_match = money_tokens[-1]
        amount = _parse_signed_money_token(amount_match.group(0))
        balance = _parse_signed_money_token(balance_match.group(0))
        if amount is None or balance is None:
            continue
        description = body[:amount_match.start()].strip()
        if not description:
            continue
        row_bbox = entry.get("bbox_row") or _default_bbox()
        cell_boxes = _capitec_personal_cell_bboxes(row_bbox)
        rows.append(
            {
                "date": _normalize_date(m.group("date"), forced_year=forced_year),
                "post_date": _normalize_date(m.group("date"), forced_year=forced_year),
                "transaction_date": None,
                "description": description,
                "reference": None,
                "amount": amount,
                "balance": balance,
                "charges": None,
                "page_index": int(entry.get("page_index", 0)),
                "needs_review": False,
                "bbox": row_bbox,
                "bbox_row": row_bbox,
                "bbox_date": cell_boxes["date"],
                "bbox_description": cell_boxes["description"],
                "bbox_amount": cell_boxes["amount"],
                "bbox_balance": cell_boxes["balance"],
                "bbox_charges": cell_boxes["charges"],
                "bank_id": profile.id,
            }
        )
    return rows


def _parse_standard_bank_line_entries(
    line_entries: list[dict[str, Any]],
    profile: BankParserProfile,
    forced_year: int | None = None,
) -> list[dict[str, Any]]:
    full_text = "\n".join(_preprocess_bank_text(str(entry.get("text", ""))) for entry in line_entries)
    year_match = re.search(r"\b(20\d{2})\b", full_text)
    if forced_year is not None:
        inferred_year = forced_year
    elif year_match:
        inferred_year = int(year_match.group(1))
    else:
        inferred_year = datetime.now().year

    rows: list[dict[str, Any]] = []
    for entry in line_entries:
        line = _preprocess_bank_text(str(entry.get("text", "")).strip())
        m = re.match(
            (
                r"^(?P<desc>.+?)\s+"
                r"(?P<amount>[+-]?\d{1,3}(?:,\d{3})*\.\d{2}[+-]?)\s+"
                r"(?P<month>\d{2})\s+(?P<day>\d{2})\s+"
                r"(?P<balance>[+-]?\d{1,3}(?:,\d{3})*\.\d{2}[+-]?)$"
            ),
            line,
        )
        if not m:
            continue
        amount = _parse_signed_money_token(m.group("amount"))
        balance = _parse_signed_money_token(m.group("balance"))
        if amount is None or balance is None:
            continue
        date_guess = f"{int(m.group('day'))}/{int(m.group('month'))}/{inferred_year}"
        row_bbox = entry.get("bbox_row") or _default_bbox()
        cell_boxes = _standard_bank_cell_bboxes(row_bbox)
        rows.append(
            {
                "date": _normalize_date(date_guess, forced_year=inferred_year),
                "post_date": _normalize_date(date_guess, forced_year=inferred_year),
                "transaction_date": None,
                "description": m.group("desc").strip(),
                "reference": None,
                "amount": amount,
                "balance": balance,
                "charges": None,
                "page_index": int(entry.get("page_index", 0)),
                "needs_review": False,
                "bbox": row_bbox,
                "bbox_row": row_bbox,
                "bbox_date": cell_boxes["date"],
                "bbox_description": cell_boxes["description"],
                "bbox_amount": cell_boxes["amount"],
                "bbox_balance": cell_boxes["balance"],
                "bbox_charges": cell_boxes["charges"],
                "bank_id": profile.id,
            }
        )
    return rows


def _parse_capitec_personal_text(text: str, forced_year: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = " ".join(raw.strip().split())
        m = re.match(r"^(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<body>.+)$", line)
        if not m:
            continue
        body = m.group("body")
        money_tokens = list(re.finditer(r"[+-]?\d{1,3}(?:[ ,]\d{3})*\.\d{2}[+-]?", body))
        if len(money_tokens) < 2:
            continue
        amount_match = money_tokens[-2]
        balance_match = money_tokens[-1]
        amount = _parse_signed_money_token(amount_match.group(0))
        balance = _parse_signed_money_token(balance_match.group(0))
        if amount is None or balance is None:
            continue
        description = body[:amount_match.start()].strip()
        if not description:
            continue
        rows.append(
            {
                "date": _normalize_date(m.group("date"), forced_year=forced_year),
                "description": description,
                "amount": amount,
                "balance": balance,
                "charges": None,
            }
        )
    return rows


def _parse_standard_bank_text(text: str, forced_year: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    year_match = re.search(r"\b(20\d{2})\b", text)
    if forced_year is not None:
        inferred_year = forced_year
    elif year_match:
        inferred_year = int(year_match.group(1))
    else:
        inferred_year = datetime.now().year
    for raw in text.splitlines():
        line = " ".join(raw.strip().split())
        if not line:
            continue
        m = re.match(
            (
                r"^(?P<desc>.+?)\s+"
                r"(?P<amount>[+-]?\d{1,3}(?:,\d{3})*\.\d{2}[+-]?)\s+"
                r"(?P<month>\d{2})\s+(?P<day>\d{2})\s+"
                r"(?P<balance>[+-]?\d{1,3}(?:,\d{3})*\.\d{2}[+-]?)$"
            ),
            line,
        )
        if not m:
            continue
        amount = _parse_signed_money_token(m.group("amount"))
        balance = _parse_signed_money_token(m.group("balance"))
        if amount is None or balance is None:
            continue
        date_guess = f"{int(m.group('day'))}/{int(m.group('month'))}/{inferred_year}"
        rows.append(
            {
                "date": _normalize_date(date_guess, forced_year=inferred_year),
                "description": m.group("desc").strip(),
                "amount": amount,
                "balance": balance,
                "charges": None,
            }
        )
    return rows


def _parse_signed_money_token(token: str) -> float | None:
    raw = token.strip().replace(" ", "").replace(",", "")
    if not raw:
        return None
    sign = 1.0
    if raw.startswith("-"):
        sign = -1.0
        raw = raw[1:]
    elif raw.startswith("+"):
        raw = raw[1:]
    if raw.endswith("-"):
        sign = -1.0
        raw = raw[:-1]
    elif raw.endswith("+"):
        raw = raw[:-1]
    if not raw:
        return None
    try:
        return sign * float(raw)
    except ValueError:
        return None


def _merge_bboxes(boxes: list[dict[str, float]]) -> dict[str, float] | None:
    if not boxes:
        return None
    return {
        "x_min": min(float(box.get("x_min", 0.0)) for box in boxes),
        "y_min": min(float(box.get("y_min", 0.0)) for box in boxes),
        "x_max": max(float(box.get("x_max", 1.0)) for box in boxes),
        "y_max": max(float(box.get("y_max", 1.0)) for box in boxes),
    }


def _default_bbox() -> dict[str, float]:
    return {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0}


def _col_bbox(row_bbox: dict[str, float], x_min: float, x_max: float) -> dict[str, float]:
    """Build a cell bbox using fixed page-normalised x-coordinates and the row's y-span."""
    return {
        "x_min": x_min,
        "x_max": x_max,
        "y_min": float(row_bbox.get("y_min", 0.0)),
        "y_max": float(row_bbox.get("y_max", 1.0)),
    }


# ---------------------------------------------------------------------------
# Column boundaries measured from actual PDF word coordinates.
# Values are normalised page x-positions (0.0 = left edge, 1.0 = right edge).
# Boundaries sit at the midpoint between the rightmost data in one column
# and the leftmost data in the next column.
# ---------------------------------------------------------------------------

def _capitec_business_cell_bboxes(row_bbox: dict[str, float]) -> dict[str, dict[str, float]]:
    # Measured from Capitec Business.pdf (595.28 x 841.88):
    #   Post Date   data: 0.0636 – 0.1218
    #   Trans Date  data: 0.1285 – 0.1845
    #   Description data: 0.1937 – ~0.32
    #   Reference   data: 0.3612 – ~0.58
    #   Fees        data: 0.6510 – 0.6793
    #   Amount      data: 0.7377 – 0.7965
    #   Balance     data: 0.8691 – 0.9363
    return {
        "post_date":        _col_bbox(row_bbox, 0.035, 0.125),
        "transaction_date": _col_bbox(row_bbox, 0.125, 0.189),
        "description":      _col_bbox(row_bbox, 0.189, 0.340),
        "reference":        _col_bbox(row_bbox, 0.340, 0.615),
        "charges":          _col_bbox(row_bbox, 0.615, 0.710),
        "amount":           _col_bbox(row_bbox, 0.710, 0.833),
        "balance":          _col_bbox(row_bbox, 0.833, 0.975),
    }


def _capitec_personal_cell_bboxes(row_bbox: dict[str, float]) -> dict[str, dict[str, float]]:
    # Measured from Capitec Personal.pdf (595.28 x 841.88):
    #   Date        data: 0.0577 – 0.1249
    #   Description data: 0.1409 – ~0.50
    #   Category    data: 0.5188 – ~0.62   (not a separate review column)
    #   Money In    data: 0.6598 – 0.7120
    #   Money Out   data: 0.7464 – 0.8031
    #   Fee         data: 0.8376 – 0.8682
    #   Balance     data: 0.8875 – 0.9398
    return {
        "date":        _col_bbox(row_bbox, 0.035, 0.133),
        "description": _col_bbox(row_bbox, 0.133, 0.638),
        "amount":      _col_bbox(row_bbox, 0.638, 0.821),
        "charges":     _col_bbox(row_bbox, 0.821, 0.878),
        "balance":     _col_bbox(row_bbox, 0.878, 0.975),
    }


def _standard_bank_cell_bboxes(row_bbox: dict[str, float]) -> dict[str, dict[str, float]]:
    # Measured from Standard Bank.pdf (595.224 x 841.824):
    #   Details     data: 0.0700 – ~0.32
    #   Svc Fee ##  data: 0.3760 – 0.3890
    #   Debits      data: 0.4738 – 0.5462
    #   Credits     data: 0.5683 – 0.6146  (header only, no sample data)
    #   Date        data: 0.6557 – 0.6936
    #   Balance     data: 0.7721 – 0.8604
    return {
        "description": _col_bbox(row_bbox, 0.035, 0.360),
        "charges":     _col_bbox(row_bbox, 0.360, 0.450),
        "amount":      _col_bbox(row_bbox, 0.450, 0.640),
        "date":        _col_bbox(row_bbox, 0.640, 0.740),
        "balance":     _col_bbox(row_bbox, 0.740, 0.975),
    }


# ---------------------------------------------------------------------------
# Position-based column split boundaries (normalised page x-coordinates).
# Used by _split_words_by_x to assign words to columns based on their
# physical x-position in the PDF, eliminating text-heuristic guessing.
# ---------------------------------------------------------------------------
_COLUMN_BOUNDARIES: dict[str, dict[str, float]] = {
    "capitec": {
        "desc_ref_split": 0.340,
        "charges_x_start": 0.615,
        "charges_x_end": 0.710,
        "amount_x_start": 0.710,
        "amount_x_end": 0.833,
        "balance_x_start": 0.833,
    },
    "capitec_personal": {
        "desc_category_split": 0.505,
    },
    "standard_bank": {
        "desc_end": 0.360,
    },
}


def _split_words_by_x(
    words: list[dict[str, Any]], boundary_x: float,
) -> tuple[str, str]:
    """Split a list of word dicts into left/right text at *boundary_x*.

    Words whose ``x_min`` is below *boundary_x* go left; the rest go right.
    """
    left = [w["text"] for w in words if w["x_min"] < boundary_x]
    right = [w["text"] for w in words if w["x_min"] >= boundary_x]
    return " ".join(left), " ".join(right)


def _extract_money_by_position(
    words: list[dict[str, Any]],
    col_bounds: dict[str, float],
) -> dict[str, float | None]:
    """Assign money values to charges/amount/balance using word x-positions.

    Returns a dict with ``charges``, ``amount``, ``balance`` keys (each
    ``float | None``).  Words are grouped into three x-zones defined by
    *col_bounds* and the joined text in each zone is parsed as a signed
    money token.
    """
    charges_start = col_bounds["charges_x_start"]
    amount_start = col_bounds["amount_x_start"]
    balance_start = col_bounds["balance_x_start"]

    fee_words = [w["text"] for w in words if charges_start <= w["x_min"] < amount_start]
    amt_words = [w["text"] for w in words if amount_start <= w["x_min"] < balance_start]
    bal_words = [w["text"] for w in words if w["x_min"] >= balance_start]

    fee_text = " ".join(fee_words).strip()
    amt_text = " ".join(amt_words).strip()
    bal_text = " ".join(bal_words).strip()

    return {
        "charges": _parse_signed_money_token(fee_text) if fee_text else None,
        "amount": _parse_signed_money_token(amt_text) if amt_text else None,
        "balance": _parse_signed_money_token(bal_text) if bal_text else None,
    }


# Text-only fallback prefixes -- only used by _parse_capitec_business_text
# (the non-layout path) and as a last resort when word positions are missing.
# The layout parser now uses position-based splitting via _split_words_by_x.
_CAPITEC_BIZ_DESC_PREFIXES: tuple[str, ...] = tuple(sorted(
    [
        "POS Local Purchase",
        "International POS Purchase",
        "International POS Pu",
        "Fuel Purchase",
        "Backdated S/Debit",
        "Debit Order",
        "Outward EFT",
        "Monthly Service Fee",
        "Notification Fee",
        "Immediate Payment",
        "Deposit Transfer",
        "RTC Deposit",
        "Ret Cr Transfer",
        "Cash Withdrawal",
        "Cash Deposit",
        "Inter Account Transfer",
        "Reversal",
        "Payment Received",
        "PayShap",
        "Credit Transfer",
        "Stop Order",
    ],
    key=len,
    reverse=True,
))


def _split_capitec_business_desc_ref(text: str) -> tuple[str, str]:
    value = " ".join((text or "").split())
    if not value:
        return "", ""
    value_lower = value.lower()
    for prefix in _CAPITEC_BIZ_DESC_PREFIXES:
        if value_lower.startswith(prefix.lower()):
            desc = value[:len(prefix)].strip()
            ref = value[len(prefix):].strip()
            if desc:
                return desc, ref
    ref_start = re.search(r"(?:^|\s)(\d{8,}|\*{2,}\d{2,}\*{2,})", value)
    if ref_start:
        idx = ref_start.start(1)
        desc = value[:idx].strip()
        ref = value[idx:].strip()
        if desc:
            return desc, ref
    words = value.split()
    if len(words) <= 2:
        return value, ""
    desc = " ".join(words[:2]).strip()
    ref = " ".join(words[2:]).strip()
    return desc, ref


def _preprocess_bank_text(text: str) -> str:
    if not text:
        return text
    normalized = text.replace("\u00a0", " ").replace("\t", " ")
    normalized = re.sub(r"(\d),(?=\d{2}\b)", r"\1.", normalized)
    return normalized


def _match_date_at_start(line: str) -> tuple[re.Match[str] | None, bool]:
    match = DATE_AT_START_RE.match(line)
    if match:
        return match, False
    match = DATE_DAY_MON_YEAR_RE.match(line)
    if match:
        return match, False
    match = DATE_DAY_MON_RE.match(line)
    return match, match is not None


def _parse_transactions_from_text_generic(
    text: str, forced_year: int | None = None,
) -> list[dict[str, Any]]:
    """Legacy text-only parser used for non-OCR and fallback paths."""
    if not text.strip():
        return []

    rows: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        line = re.sub(r"(?<=\d)\s+(?=[\.,])", "", line)
        line = re.sub(r"(?<=[\.,])\s+(?=\d)", "", line)
        if not line:
            continue

        m, is_day_mon_line = _match_date_at_start(line)
        if not m:
            continue

        date_raw = m.group("date")
        line_without_date = line[m.end() :].strip()

        money_tokens = list(MONEY_RE.finditer(line_without_date))
        if money_tokens:
            balance_idx = len(money_tokens) - 1
            for idx, tok in enumerate(money_tokens):
                tail = line_without_date[tok.end() : tok.end() + 6].lower()
                if "cr" in tail or "dr" in tail:
                    balance_idx = idx

            if balance_idx > 0:
                amt_tok = money_tokens[balance_idx - 1]
                bal_tok = money_tokens[balance_idx]
                amount = _parse_number(amt_tok.group(0).replace("(", "-").replace(")", ""))
                balance = _parse_number(bal_tok.group(0).replace("(", "-").replace(")", ""))

                if is_day_mon_line and abs(balance) < 500:
                    continue

                around_amt = line_without_date[amt_tok.start() : amt_tok.end() + 6].lower()
                desc_left = line_without_date[: amt_tok.start()].strip()
                if "cr" in around_amt or "credit" in desc_left.lower():
                    signed_amount = abs(amount)
                elif "dr" in around_amt:
                    signed_amount = -abs(amount)
                else:
                    signed_amount = -abs(amount) if is_day_mon_line else amount

                desc = " ".join(desc_left.split())
                if desc:
                    rows.append(
                        {
                            "date": _normalize_date(date_raw, forced_year=forced_year),
                            "description": desc,
                            "amount": signed_amount,
                            "balance": balance,
                        }
                    )
                    continue

        if is_day_mon_line:
            continue

        numbers = NUMBER_RE.findall(line_without_date)
        if not numbers:
            continue

        parsed_numbers = [_parse_number(token) for token in numbers]
        balance = parsed_numbers[-1] if len(parsed_numbers) >= 2 else None
        amount = None
        if len(parsed_numbers) in (1, 2):
            amount = parsed_numbers[0]
        else:
            debit = parsed_numbers[-3]
            credit = parsed_numbers[-2]
            if debit != 0 and credit == 0:
                amount = -abs(debit)
            elif credit != 0 and debit == 0:
                amount = abs(credit)
            else:
                amount = credit - debit

        desc = NUMBER_RE.sub("", line_without_date).strip()
        desc = " ".join(desc.split())
        rows.append(
            {
                "date": _normalize_date(date_raw, forced_year=forced_year),
                "description": desc,
                "amount": amount,
                "balance": balance,
            }
        )

    return rows


def _extract_layout(layout: Any) -> tuple[list[float], list[float]]:
    """Return (xs, ys) from normalized_vertices on a layout's bounding_poly."""
    bbox = getattr(layout, "bounding_poly", None)
    nv = getattr(bbox, "normalized_vertices", []) or []
    xs = [float(getattr(v, "x", 0.0)) for v in nv]
    ys = [float(getattr(v, "y", 0.0)) for v in nv]
    return xs, ys


# ---------------------------------------------------------------------------
# Visual-row column boundaries (normalized x).  Derived from the standard
# FNB cheque-account statement layout.
# ---------------------------------------------------------------------------
_COL_CARD_X = 0.50
_COL_AMOUNT_X = 0.70
_COL_BALANCE_X = 0.83
_COL_CHARGES_X = 0.92
_ROW_Y_THRESHOLD = 0.006


def _y_center(line: ParsedLine) -> float:
    return (line.y_min + line.y_max) / 2


def _classify_column(line: ParsedLine) -> str:
    x = line.x_min
    if x >= _COL_CHARGES_X:
        return "charges"
    if x >= _COL_BALANCE_X:
        return "balance"
    if x >= _COL_AMOUNT_X:
        return "amount"
    if x >= _COL_CARD_X:
        return "card_info"
    return "left"


def _bbox_from_lines(lines: list[ParsedLine]) -> dict[str, float] | None:
    if not lines:
        return None
    return {
        "x_min": min(l.x_min for l in lines),
        "y_min": min(l.y_min for l in lines),
        "x_max": max(l.x_max for l in lines),
        "y_max": max(l.y_max for l in lines),
    }


def _extract_page_lines(
    document: Any, page: Any, page_index: int,
) -> list[ParsedLine]:
    """Build ParsedLine objects (with tokens) for every line on *page*."""
    page_tokens_raw: list[dict[str, Any]] = []
    for token in getattr(page, "tokens", []):
        layout = token.layout
        text_anchor = getattr(layout, "text_anchor", None)
        segments = getattr(text_anchor, "text_segments", []) or []
        if not segments:
            continue
        tok_start = int(getattr(segments[0], "start_index", 0) or 0)
        tok_end = int(getattr(segments[-1], "end_index", 0) or 0)
        text = document.text[tok_start:tok_end].strip()
        if not text:
            continue
        xs, ys = _extract_layout(layout)
        if not xs or not ys:
            continue
        page_tokens_raw.append({
            "text": text,
            "x_min": min(xs), "y_min": min(ys),
            "x_max": max(xs), "y_max": max(ys),
            "start": tok_start, "end": tok_end,
        })

    result: list[ParsedLine] = []
    for line in getattr(page, "lines", []):
        layout = line.layout
        text_anchor = getattr(layout, "text_anchor", None)
        segments = getattr(text_anchor, "text_segments", []) or []
        if not segments:
            continue
        line_start = int(getattr(segments[0], "start_index", 0) or 0)
        line_end = int(getattr(segments[-1], "end_index", 0) or 0)
        raw_text = document.text[line_start:line_end]
        cleaned = " ".join((raw_text or "").strip().split())
        if not cleaned:
            continue

        line_tokens = [
            ParsedToken(
                text=pt["text"],
                x_min=pt["x_min"], y_min=pt["y_min"],
                x_max=pt["x_max"], y_max=pt["y_max"],
            )
            for pt in page_tokens_raw
            if pt["start"] >= line_start and pt["end"] <= line_end
        ]

        xs, ys = _extract_layout(layout)
        if not xs or not ys:
            xs, ys = [0.0, 1.0], [0.0, 1.0]

        result.append(ParsedLine(
            page_index=page_index,
            text=cleaned,
            x_min=min(xs), y_min=min(ys),
            x_max=max(xs), y_max=max(ys),
            tokens=line_tokens,
        ))
    return result


def _detect_table_bounds(lines: list[ParsedLine]) -> tuple[float, float]:
    """Return (table_start_y, table_end_y) for the transaction table."""
    table_start = 0.0
    table_end = 1.0
    for line in lines:
        text_lower = line.text.strip().lower()
        if text_lower == "date" and line.x_min < 0.06:
            table_start = line.y_max
        if re.match(r"page\s+\d+\s+of\s+\d+", text_lower):
            table_end = line.y_min
            break
    return table_start, table_end


def _group_visual_rows(lines: list[ParsedLine]) -> list[list[ParsedLine]]:
    """Group lines into visual rows by y-coordinate proximity."""
    if not lines:
        return []
    sorted_lines = sorted(lines, key=_y_center)
    rows: list[list[ParsedLine]] = []
    current: list[ParsedLine] = [sorted_lines[0]]
    for line in sorted_lines[1:]:
        if abs(_y_center(line) - _y_center(current[0])) < _ROW_Y_THRESHOLD:
            current.append(line)
        else:
            rows.append(current)
            current = [line]
    rows.append(current)
    return rows


def _parse_money_text(text: str) -> tuple[float | None, bool]:
    """Parse '116,252.58Cr' → (116252.58, True).  Returns (value, is_credit)."""
    if not text:
        return None, False
    clean = text.strip().replace(",", "").replace(" ", "")
    is_credit = clean.lower().endswith("cr")
    if is_credit:
        clean = clean[:-2]
    is_debit = clean.lower().endswith("dr")
    if is_debit:
        clean = clean[:-2]
    clean = clean.replace("(", "").replace(")", "")
    try:
        return float(clean), is_credit
    except ValueError:
        return None, False


def _extract_pdfplumber_lines(page: Any, page_index: int) -> list[dict[str, Any]]:
    words = page.extract_words(
        x_tolerance=2,
        y_tolerance=3,
        keep_blank_chars=False,
        extra_attrs=["x0", "x1", "top", "bottom"],
    )
    if not words:
        return []

    words_sorted = sorted(words, key=lambda w: (float(w.get("top", 0.0)), float(w.get("x0", 0.0))))
    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [words_sorted[0]]
    current_top = float(words_sorted[0].get("top", 0.0))
    for w in words_sorted[1:]:
        top = float(w.get("top", 0.0))
        if abs(top - current_top) <= 3.0:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
            current_top = top
    lines.append(current)

    page_w = float(getattr(page, "width", 1.0) or 1.0)
    page_h = float(getattr(page, "height", 1.0) or 1.0)
    out: list[dict[str, Any]] = []
    for words_line in lines:
        words_line_sorted = sorted(words_line, key=lambda w: float(w.get("x0", 0.0)))
        text = " ".join(str(w.get("text", "")).strip() for w in words_line_sorted).strip()
        if not text:
            continue

        x0 = min(float(w.get("x0", 0.0)) for w in words_line_sorted)
        x1 = max(float(w.get("x1", 0.0)) for w in words_line_sorted)
        y0 = min(float(w.get("top", 0.0)) for w in words_line_sorted)
        y1 = max(float(w.get("bottom", 0.0)) for w in words_line_sorted)
        out.append(
            {
                "page_index": page_index,
                "text": text,
                "bbox_row": {
                    "x_min": max(0.0, min(1.0, x0 / page_w)),
                    "y_min": max(0.0, min(1.0, y0 / page_h)),
                    "x_max": max(0.0, min(1.0, x1 / page_w)),
                    "y_max": max(0.0, min(1.0, y1 / page_h)),
                },
            }
        )
    return out


def _extract_text_line_entries(page: Any, page_index: int) -> list[dict[str, Any]]:
    """Extract text lines with per-word normalised x-positions.

    Uses ``page.extract_words()`` so every line entry carries a ``words``
    list that downstream parsers can use for position-based column splitting.
    """
    words = page.extract_words(
        x_tolerance=2,
        y_tolerance=3,
        keep_blank_chars=False,
    )
    if not words:
        return []

    page_w = float(getattr(page, "width", 1.0) or 1.0)
    page_h = float(getattr(page, "height", 1.0) or 1.0)

    words_sorted = sorted(
        words,
        key=lambda w: (float(w.get("top", 0.0)), float(w.get("x0", 0.0))),
    )
    grouped: list[list[dict[str, Any]]] = []
    cur_group: list[dict[str, Any]] = [words_sorted[0]]
    cur_top = float(words_sorted[0].get("top", 0.0))
    for w in words_sorted[1:]:
        top = float(w.get("top", 0.0))
        if abs(top - cur_top) <= 3.0:
            cur_group.append(w)
        else:
            grouped.append(cur_group)
            cur_group = [w]
            cur_top = top
    grouped.append(cur_group)

    out: list[dict[str, Any]] = []
    for wline in grouped:
        wline_sorted = sorted(wline, key=lambda w: float(w.get("x0", 0.0)))
        text = " ".join(str(w.get("text", "")).strip() for w in wline_sorted).strip()
        if not text:
            continue

        x0 = min(float(w.get("x0", 0.0)) for w in wline_sorted)
        x1 = max(float(w.get("x1", 0.0)) for w in wline_sorted)
        y0 = min(float(w.get("top", 0.0)) for w in wline_sorted)
        y1 = max(float(w.get("bottom", 0.0)) for w in wline_sorted)
        line_h = max(0.0, y1 - y0)
        y0_adj = max(0.0, y0 - line_h * 0.08)
        y1_adj = min(page_h, max(y0_adj, y1 - line_h * 0.22))

        norm_words = [
            {
                "text": str(w.get("text", "")).strip(),
                "x_min": float(w.get("x0", 0.0)) / page_w,
                "x_max": float(w.get("x1", 0.0)) / page_w,
            }
            for w in wline_sorted
            if str(w.get("text", "")).strip()
        ]

        out.append(
            {
                "page_index": page_index,
                "text": text,
                "words": norm_words,
                "bbox_row": {
                    "x_min": max(0.0, min(1.0, x0 / page_w)),
                    "y_min": max(0.0, min(1.0, y0_adj / page_h)),
                    "x_max": max(0.0, min(1.0, x1 / page_w)),
                    "y_max": max(0.0, min(1.0, y1_adj / page_h)),
                },
            }
        )
    return out


def _build_fallback_line_entries_from_text(text: str, page_index: int) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return []
    count = len(lines)
    out: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        y0 = idx / count
        y1 = min(1.0, (idx + 1) / count)
        out.append(
            {
                "page_index": page_index,
                "text": line,
                "bbox_row": {
                    "x_min": 0.02,
                    "y_min": y0,
                    "x_max": 0.98,
                    "y_max": y1,
                },
            }
        )
    return out


def _parse_pdfplumber_line(
    line: dict[str, Any],
    forced_year: int | None,
    prev_date: str | None,
    profile: BankParserProfile,
) -> dict[str, Any] | None:
    text = _preprocess_bank_text(str(line.get("text", "")).strip())
    if not text:
        return None

    m, is_day_mon_line = _match_date_at_start(text)
    if not m:
        return None

    date_raw = m.group("date")
    normalized_date = _normalize_date(date_raw, forced_year=forced_year)
    line_without_date = text[m.end() :].strip()

    amount: float | None = None
    balance: float | None = None
    money_tokens = list(MONEY_RE.finditer(line_without_date))
    if money_tokens:
        balance_idx = len(money_tokens) - 1
        for idx, tok in enumerate(money_tokens):
            tail = line_without_date[tok.end() : tok.end() + 6].lower()
            if "cr" in tail or "dr" in tail:
                balance_idx = idx
        if balance_idx > 0:
            amt_tok = money_tokens[balance_idx - 1]
            bal_tok = money_tokens[balance_idx]
            amount = _parse_number(amt_tok.group(0).replace("(", "-").replace(")", ""))
            balance = _parse_number(bal_tok.group(0).replace("(", "-").replace(")", ""))
            around_amt = line_without_date[amt_tok.start() : amt_tok.end() + 6].lower()
            desc_left = line_without_date[: amt_tok.start()].strip()
            if "cr" in around_amt or "credit" in desc_left.lower():
                amount = abs(amount)
            elif "dr" in around_amt:
                amount = -abs(amount)
            else:
                amount = -abs(amount) if is_day_mon_line else amount

    if amount is None:
        numbers = NUMBER_RE.findall(line_without_date)
        if numbers:
            parsed_numbers = [_parse_number(token) for token in numbers]
            balance = parsed_numbers[-1] if len(parsed_numbers) >= 2 else None
            if len(parsed_numbers) in (1, 2):
                amount = parsed_numbers[0]
            else:
                debit = parsed_numbers[-3]
                credit = parsed_numbers[-2]
                if debit != 0 and credit == 0:
                    amount = -abs(debit)
                elif credit != 0 and debit == 0:
                    amount = abs(credit)
                else:
                    amount = credit - debit

    description = NUMBER_RE.sub("", line_without_date).strip()
    description = " ".join(description.split()) if description else None
    row_bbox = line.get("bbox_row") or {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0}
    final_date = normalized_date or prev_date
    if not final_date and amount is None and balance is None:
        return None

    needs_review = amount is None or balance is None
    return {
        "date": final_date,
        "description": description,
        "amount": amount,
        "balance": balance,
        "charges": None,
        "page_index": int(line.get("page_index", 0)),
        "needs_review": needs_review,
        "bbox": row_bbox,
        "bbox_row": row_bbox,
        "bbox_date": row_bbox,
        "bbox_description": row_bbox,
        "bbox_amount": row_bbox,
        "bbox_balance": row_bbox,
        "bbox_charges": row_bbox,
        "bank_id": profile.id,
    }
def _bbox_from_tokens(tokens: list[ParsedToken]) -> dict[str, float] | None:
    if not tokens:
        return None
    return {
        "x_min": min(t.x_min for t in tokens),
        "y_min": min(t.y_min for t in tokens),
        "x_max": max(t.x_max for t in tokens),
        "y_max": max(t.y_max for t in tokens),
    }


def _split_date_tokens(
    line: ParsedLine, date_text: str,
) -> tuple[list[ParsedToken], list[ParsedToken]]:
    """Split a line's tokens into (date_tokens, remaining_tokens).

    Matches tokens by word text AND requires them to be left of any
    non-date token so that a word like '01' later in the description
    is not accidentally grabbed.
    """
    date_words = date_text.lower().split()
    remaining: list[str] = list(date_words)
    date_toks: list[ParsedToken] = []
    desc_toks: list[ParsedToken] = []

    sorted_tokens = sorted(line.tokens, key=lambda t: t.x_min)
    date_phase = True
    for tok in sorted_tokens:
        word = tok.text.strip().lower()
        if date_phase and word in remaining:
            remaining.remove(word)
            date_toks.append(tok)
            if not remaining:
                date_phase = False
        else:
            date_phase = False
            desc_toks.append(tok)

    return date_toks, desc_toks


def _parse_visual_row(
    vrow: list[ParsedLine],
    page_index: int,
    forced_year: int | None,
    prev_date: str | None,
) -> dict[str, Any] | None:
    """Convert one visual row (lines at similar y) into a transaction dict."""
    date_raw: str | None = None
    date_normalized: str | None = None
    desc_parts: list[str] = []
    card_info_parts: list[str] = []
    amount_text: str | None = None
    balance_text: str | None = None
    charges_text: str | None = None

    left_lines: list[ParsedLine] = []

    date_toks: list[ParsedToken] = []
    desc_toks: list[ParsedToken] = []
    amount_toks: list[ParsedToken] = []
    balance_toks: list[ParsedToken] = []
    charges_toks: list[ParsedToken] = []

    for line in vrow:
        col = _classify_column(line)
        if col == "left":
            left_lines.append(line)
            m, _ = _match_date_at_start(line.text)
            if m:
                date_raw = m.group("date")
                date_normalized = _normalize_date(date_raw, forced_year=forced_year)
                d_toks, r_toks = _split_date_tokens(line, date_raw)
                date_toks.extend(d_toks)
                desc_toks.extend(r_toks)
                rest = line.text[m.end():].strip()
                if rest:
                    rest = rest.lstrip("#").strip()
                    desc_parts.append(rest)
            else:
                desc_parts.append(line.text)
                desc_toks.extend(line.tokens)
        elif col == "card_info":
            left_lines.append(line)
            card_info_parts.append(line.text)
            desc_toks.extend(line.tokens)
        elif col == "amount":
            amount_text = line.text.strip()
            amount_toks.extend(line.tokens)
        elif col == "balance":
            balance_text = line.text.strip()
            balance_toks.extend(line.tokens)
        elif col == "charges":
            charges_text = line.text.strip()
            charges_toks.extend(line.tokens)

    if not date_normalized and not amount_text and not balance_text:
        return None

    if not date_normalized:
        date_normalized = prev_date

    all_desc = desc_parts + card_info_parts
    description = " ".join(all_desc).strip() or None

    amount_val, amount_is_credit = _parse_money_text(amount_text)
    if amount_val is not None and not amount_is_credit:
        amount_val = -abs(amount_val)

    balance_val, _ = _parse_money_text(balance_text)
    charges_val, _ = _parse_money_text(charges_text)

    row_bbox = _bbox_from_lines(vrow) or {
        "x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1,
    }

    needs_review = amount_val is None or balance_val is None

    return {
        "date": date_normalized,
        "description": description,
        "amount": amount_val,
        "balance": balance_val,
        "charges": charges_val,
        "page_index": page_index,
        "needs_review": needs_review,
        "bbox": row_bbox,
        "bbox_row": row_bbox,
        "bbox_date": _bbox_from_tokens(date_toks) or row_bbox,
        "bbox_description": _bbox_from_tokens(desc_toks) or row_bbox,
        "bbox_amount": _bbox_from_tokens(amount_toks) or row_bbox,
        "bbox_balance": _bbox_from_tokens(balance_toks) or row_bbox,
        "bbox_charges": _bbox_from_tokens(charges_toks) or row_bbox,
    }


def _parse_transactions_from_document_fnb(
    document: Document, forced_year: Optional[int] = None,
) -> List[dict[str, Any]]:
    """
    Layout-aware parser for FNB statements using Document AI's Document.

    Groups lines into visual rows by y-coordinate, then classifies each line
    into date / description / amount / balance columns by x-coordinate.  This
    handles Document AI's tendency to return column-grouped rather than
    row-grouped lines.
    """
    if document is None or not getattr(document, "pages", None):
        return []

    all_transactions: List[dict[str, Any]] = []
    for page_index, page in enumerate(document.pages or []):
        page_lines = _extract_page_lines(document, page, page_index)
        table_start, table_end = _detect_table_bounds(page_lines)

        table_lines = [
            l for l in page_lines
            if _y_center(l) > table_start and _y_center(l) < table_end
        ]

        visual_rows = _group_visual_rows(table_lines)
        prev_date: str | None = None

        for vrow in visual_rows:
            tx = _parse_visual_row(vrow, page_index, forced_year, prev_date)
            if tx is None:
                continue
            all_transactions.append(tx)
            if tx.get("date"):
                prev_date = tx["date"]

            log_event(
                level=20,
                event="parser_visual_row",
                details=tx.get("description", "")[:80],
                extra={
                    "page_index": page_index,
                    "date": tx.get("date"),
                    "amount": tx.get("amount"),
                    "balance": tx.get("balance"),
                    "charges": tx.get("charges"),
                    "needs_review": tx.get("needs_review"),
                },
            )

    return all_transactions


def _parse_transactions_from_document_text_fallback(
    document: Document, forced_year: Optional[int] = None, bank_id: str = "fnb",
) -> List[dict[str, Any]]:
    text = getattr(document, "text", "") or ""
    rows = parse_transactions_from_text(text, forced_year=forced_year, bank_id=bank_id)
    out: List[dict[str, Any]] = []
    default_bbox = {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0}

    profile = get_enabled_bank_profile(bank_id)
    cell_bbox_fn = {
        "capitec": _capitec_business_cell_bboxes,
        "capitec_personal": _capitec_personal_cell_bboxes,
        "standard_bank": _standard_bank_cell_bboxes,
    }.get(profile.id)

    for row in rows:
        amount = row.get("amount")
        balance = row.get("balance")
        enriched: dict[str, Any] = {
            "date": row.get("date"),
            "post_date": row.get("post_date"),
            "transaction_date": row.get("transaction_date"),
            "description": row.get("description"),
            "reference": row.get("reference"),
            "amount": amount,
            "balance": balance,
            "charges": row.get("charges"),
            "page_index": row.get("page_index", 0),
            "needs_review": amount is None or balance is None,
            "bank_id": profile.id,
            "bbox": default_bbox,
            "bbox_row": default_bbox,
        }
        if cell_bbox_fn:
            cell_boxes = cell_bbox_fn(default_bbox)
            for key, box in cell_boxes.items():
                enriched[f"bbox_{key}"] = box
        else:
            enriched["bbox_date"] = default_bbox
            enriched["bbox_description"] = default_bbox
            enriched["bbox_amount"] = default_bbox
            enriched["bbox_balance"] = default_bbox
            enriched["bbox_charges"] = default_bbox
        out.append(enriched)
    return out


def _extract_docai_line_entries(document: Document) -> list[dict[str, Any]]:
    """Extract line entries with per-word positions from a Document AI response.

    Groups tokens into lines by y-position (same approach as
    ``_extract_text_line_entries`` for pdfplumber) so the existing
    bank-specific parsers can consume them identically.
    """
    all_entries: list[dict[str, Any]] = []
    doc_text = document.text or ""

    for page_index, page in enumerate(getattr(document, "pages", []) or []):
        tokens = list(page.tokens or [])
        if not tokens:
            continue

        word_dicts: list[dict[str, Any]] = []
        for tok in tokens:
            segs = tok.layout.text_anchor.text_segments
            if not segs:
                continue
            tok_start = int(getattr(segs[0], "start_index", 0) or 0)
            tok_end = int(getattr(segs[0], "end_index", 0) or 0)
            text = doc_text[tok_start:tok_end].strip()
            if not text:
                continue
            nvs = tok.layout.bounding_poly.normalized_vertices
            if len(nvs) < 4:
                continue
            x_min = min(v.x for v in nvs)
            x_max = max(v.x for v in nvs)
            y_min = min(v.y for v in nvs)
            y_max = max(v.y for v in nvs)
            y_mid = (y_min + y_max) / 2.0
            word_dicts.append({
                "text": text,
                "x_min": x_min,
                "x_max": x_max,
                "y_min": y_min,
                "y_max": y_max,
                "y_mid": y_mid,
            })

        if not word_dicts:
            continue

        word_dicts.sort(key=lambda w: (w["y_mid"], w["x_min"]))
        line_tolerance = 0.005
        grouped: list[list[dict[str, Any]]] = []
        cur_group: list[dict[str, Any]] = [word_dicts[0]]
        cur_y = word_dicts[0]["y_mid"]
        for w in word_dicts[1:]:
            if abs(w["y_mid"] - cur_y) <= line_tolerance:
                cur_group.append(w)
            else:
                grouped.append(cur_group)
                cur_group = [w]
                cur_y = w["y_mid"]
        grouped.append(cur_group)

        for wline in grouped:
            wline_sorted = sorted(wline, key=lambda w: w["x_min"])
            text = " ".join(w["text"] for w in wline_sorted).strip()
            if not text:
                continue
            x0 = min(w["x_min"] for w in wline_sorted)
            x1 = max(w["x_max"] for w in wline_sorted)
            y0 = min(w["y_min"] for w in wline_sorted)
            y1 = max(w["y_max"] for w in wline_sorted)
            line_h = max(0.0, y1 - y0)
            y0_adj = max(0.0, y0 - line_h * 0.08)
            y1_adj = min(1.0, max(y0_adj, y1 - line_h * 0.22))

            norm_words = [
                {"text": w["text"], "x_min": w["x_min"], "x_max": w["x_max"]}
                for w in wline_sorted
            ]

            all_entries.append({
                "page_index": page_index,
                "text": text,
                "words": norm_words,
                "bbox_row": {
                    "x_min": max(0.0, min(1.0, x0)),
                    "y_min": max(0.0, min(1.0, y0_adj)),
                    "x_max": max(0.0, min(1.0, x1)),
                    "y_max": max(0.0, min(1.0, y1_adj)),
                },
            })

    return all_entries


def parse_transactions_from_document(
    document: Document, forced_year: Optional[int] = None, bank_id: str = "fnb",
) -> List[dict[str, Any]]:
    profile = get_enabled_bank_profile(bank_id)
    if profile.document_rule_set == "fnb_layout":
        return _parse_transactions_from_document_fnb(document, forced_year=forced_year)

    line_entries = _extract_docai_line_entries(document)
    if line_entries:
        rows = _parse_non_fnb_line_entries_with_layout(
            line_entries, profile=profile, forced_year=forced_year,
        )
        if rows:
            return rows

    return _parse_transactions_from_document_text_fallback(
        document, forced_year=forced_year, bank_id=profile.id,
    )


def _normalize_date(date_str: str, forced_year: int | None = None) -> str:
    clean = date_str.strip().replace("-", "/")
    if re.match(r"^\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}$", clean):
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                parsed = datetime.strptime(clean.title(), fmt)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue
    if re.match(r"^\d{1,2}\s+[A-Za-z]{3}$", clean):
        if forced_year is not None:
            parsed = datetime.strptime(f"{clean.title()} {forced_year}", "%d %b %Y")
            return parsed.strftime("%Y-%m-%d")
        return clean.title()

    parts = clean.split("/")
    if len(parts) != 3:
        return clean

    a, b, c = parts
    if len(a) == 4:
        parsed = datetime.strptime(f"{a}/{b}/{c}", "%Y/%m/%d")
    else:
        fmt = "%d/%m/%Y" if len(c) == 4 else "%d/%m/%y"
        parsed = datetime.strptime(f"{int(a)}/{int(b)}/{int(c)}", fmt)
    return parsed.strftime("%Y-%m-%d")


def _parse_number(num_str: str) -> float:
    return float(num_str.replace(",", "").strip())
