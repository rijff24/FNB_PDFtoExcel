# Parser — Transaction Extraction

**File**: `app/services/parser.py`

The parser is responsible for extracting structured transaction data (date, description, amount, balance, charges) from FNB bank statement PDFs. It supports two parsing modes: a **visual-row layout parser** (primary, used with Document AI) and a **legacy text parser** (fallback, used with pdfplumber).

## Visual-Row Layout Parser

### Why a Visual-Row Approach?

Google Document AI's OCR engine returns `line` objects grouped by **column** rather than by **visual row**. For an FNB statement table like:

```
Date        Description          Amount      Balance     Charges
03 Apr      POS Purchase SPAR    245.50      12,500.00   3.95
```

Document AI may return separate `line` objects for:
- `"03 Apr POS Purchase SPAR"` (left column cluster)
- `"245.50"` (amount column)
- `"12,500.00Cr"` (balance column)
- `"3.95"` (charges column)

These lines share the same vertical (y) position but have different horizontal (x) positions. A naive line-by-line text parser would fail to associate these fragments into a single transaction.

### Algorithm

The visual-row parser works in four stages:

#### 1. Extract Page Lines (`_extract_page_lines`)

For each page in the Document AI `Document` object:
- Iterates over `page.lines` and `page.tokens`.
- For each line, extracts the text (from `document.text` using `text_anchor` segment offsets) and bounding box (normalized 0–1 coordinates from `layout.bounding_poly.normalized_vertices`).
- Maps tokens to their parent lines by checking if the token's text segment falls within the line's segment range.
- Returns a list of `ParsedLine` dataclass objects.

#### 2. Detect Table Bounds (`_detect_table_bounds`)

Identifies the vertical extent of the transaction table on the page:
- **Start**: Finds a line with text `"date"` (case-insensitive) at `x_min < 0.06` — this is the table header. The table starts just below it.
- **End**: Finds a line matching `"Page X of Y"` — this is the page footer. The table ends just above it.
- Lines outside these bounds (headers, footers, account summaries) are filtered out.

#### 3. Group Visual Rows (`_group_visual_rows`)

- Sorts all table lines by their vertical center (`y_center = (y_min + y_max) / 2`).
- Groups consecutive lines into "visual rows" if their `y_center` values are within `_ROW_Y_THRESHOLD` (0.006 in normalized coordinates, approximately 6px on a standard page).
- Each visual row contains all lines that appear on the same horizontal band.

#### 4. Parse Each Visual Row (`_parse_visual_row`)

For each visual row, classifies every line into a column based on its `x_min`:

| Column | x_min threshold | Content |
|---|---|---|
| `left` | `< 0.50` | Date and/or description text |
| `card_info` | `≥ 0.50, < 0.70` | Card number or vendor info (appended to description) |
| `amount` | `≥ 0.70, < 0.83` | Transaction amount |
| `balance` | `≥ 0.83, < 0.92` | Running balance |
| `charges` | `≥ 0.92` | Accrued bank charges |

**Column boundary constants** (normalized x-coordinates, derived from the standard FNB cheque account statement layout):
```python
_COL_CARD_X    = 0.50
_COL_AMOUNT_X  = 0.70
_COL_BALANCE_X = 0.83
_COL_CHARGES_X = 0.92
```

**Date extraction**: Within `left` lines, regex patterns detect dates in `DD/MM/YYYY` or `DD Mon` formats. The text after the date is treated as description.

**Amount/balance parsing** (`_parse_money_text`):
- Strips commas and spaces.
- Detects `Cr` (credit) or `Dr` (debit) suffix.
- Credits are positive, debits are negative.
- Balances are always positive (absolute value).

**Charges parsing**: Same `_parse_money_text` function, always positive.

**Date inheritance**: If a visual row has no date (e.g., continuation rows), it inherits the date from the previous transaction (`prev_date`).

**Needs review flag**: Set to `true` if either `amount` or `balance` could not be parsed.

### Data Classes

```python
@dataclass
class ParsedToken:
    text: str
    x_min: float   # Normalized 0–1
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
    tokens: list[ParsedToken]
```

### Output

Each transaction is a dictionary with keys: `date`, `description`, `amount`, `balance`, `charges`, `page_index`, `needs_review`, `bbox`, `bbox_row`, `bbox_date`, `bbox_description`, `bbox_amount`, `bbox_balance`, `bbox_charges`.

All `bbox_*` fields are dictionaries with `x_min`, `y_min`, `x_max`, `y_max` in normalized (0–1) coordinates. These are used by the review UI to highlight regions on the rendered PDF.

---

## Legacy Text Parser

**Function**: `parse_transactions_from_text`

Used when OCR is disabled (pdfplumber path) or as a general-purpose fallback. It operates on plain text (no layout/coordinate information).

### Algorithm

1. Splits text into lines.
2. For each line, attempts to match a date at the start (via `DATE_AT_START_RE` or `DATE_DAY_MON_RE`).
3. After removing the date, searches for `MONEY_RE` patterns (e.g., `1,234.56`).
4. The last money token is assumed to be the balance; the second-to-last is the amount.
5. Credit/debit is determined by nearby `Cr`/`Dr` text or by heuristics (day-month format lines are assumed debit).
6. Remaining text before the first money token is the description.

### Limitations

- No bounding box data (no `bbox_*` fields).
- No charges column extraction.
- Less accurate than the visual-row parser for complex multi-line transactions.

---

## Regex Patterns

| Pattern | Purpose |
|---|---|
| `DATE_AT_START_RE` | Matches `DD/MM/YYYY`, `DD-MM-YYYY`, `YYYY/MM/DD` at line start |
| `DATE_DAY_MON_RE` | Matches `DD Mon` (e.g., `03 Apr`) at line start |
| `NUMBER_RE` | General number pattern with optional commas |
| `MONEY_RE` | Strict monetary pattern requiring exactly 2 decimal places |

---

## Tuning Column Boundaries

If the parser misclassifies columns for a different FNB statement layout, adjust the `_COL_*_X` constants in `parser.py`. Use the `docs/samplePDF/debug_page0.py` script to inspect raw line positions from Document AI and identify the correct x-coordinate boundaries.
