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

from app.services.logging_utils import log_event


DATE_AT_START_RE = re.compile(
    r"^\s*(?P<date>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2})\b",
)
DATE_DAY_MON_RE = re.compile(r"^\s*(?P<date>\d{1,2}\s+[A-Za-z]{3})\b", re.IGNORECASE)
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


def parse_transactions_from_pdf_bytes(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """Non-OCR path: keep existing pdfplumber + text-only parser."""
    all_text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            all_text_parts.append(page.extract_text() or "")
    return parse_transactions_from_text("\n".join(all_text_parts))


def parse_transactions_from_pdf_bytes_with_layout(
    pdf_bytes: bytes, forced_year: int | None = None,
) -> list[dict[str, Any]]:
    """
    Non-OCR parser with layout metadata for review/highlighting.

    Uses pdfplumber word coordinates to build line-level normalized bboxes and
    emits transactions compatible with the review UI contract.
    """
    rows: list[dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_index, page in enumerate(pdf.pages):
            line_entries = _extract_pdfplumber_lines(page, page_index)
            prev_date: str | None = None
            for entry in line_entries:
                tx = _parse_pdfplumber_line(entry, forced_year=forced_year, prev_date=prev_date)
                if tx is None:
                    continue
                rows.append(tx)
                if tx.get("date"):
                    prev_date = str(tx["date"])
    return rows


def parse_transactions_from_text(text: str, forced_year: int | None = None) -> list[dict[str, Any]]:
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

        m = DATE_AT_START_RE.match(line)
        is_day_mon_line = False
        if not m:
            m = DATE_DAY_MON_RE.match(line)
            is_day_mon_line = m is not None
        if not m:
            continue

        date_raw = m.group("date")
        line_without_date = DATE_AT_START_RE.sub("", line, count=1).strip()
        if line_without_date == line:
            line_without_date = DATE_DAY_MON_RE.sub("", line, count=1).strip()

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


def _parse_pdfplumber_line(
    line: dict[str, Any], forced_year: int | None, prev_date: str | None,
) -> dict[str, Any] | None:
    text = str(line.get("text", "")).strip()
    if not text:
        return None

    m = DATE_AT_START_RE.match(text)
    is_day_mon_line = False
    if not m:
        m = DATE_DAY_MON_RE.match(text)
        is_day_mon_line = m is not None
    if not m:
        return None

    date_raw = m.group("date")
    normalized_date = _normalize_date(date_raw, forced_year=forced_year)
    line_without_date = DATE_AT_START_RE.sub("", text, count=1).strip()
    if line_without_date == text:
        line_without_date = DATE_DAY_MON_RE.sub("", text, count=1).strip()

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
            m = DATE_AT_START_RE.match(line.text) or DATE_DAY_MON_RE.match(line.text)
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


def parse_transactions_from_document(document: Document, forced_year: Optional[int] = None) -> List[dict[str, Any]]:
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


def _normalize_date(date_str: str, forced_year: int | None = None) -> str:
    clean = date_str.strip().replace("-", "/")
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
