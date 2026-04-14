# Parser — Transaction Extraction

**Files**: `app/services/parser.py`, `app/services/banks.py`

The parser extracts structured transaction data from South African bank statement PDFs. It supports multiple banks (FNB, Capitec Business, Capitec Personal, Standard Bank) with per-bank parsing profiles. Two parsing modes are available: a **position-based layout parser** (primary, uses pdfplumber word positions or Document AI tokens) and a **text-only fallback parser**.

## Multi-Bank Support

Bank parser profiles are defined in `app/services/banks.py`. Each profile specifies:
- `id`: internal identifier (e.g. `"capitec"`, `"capitec_personal"`)
- `label`: user-visible name in the bank selector dropdown
- `processor_env_name`: which Document AI processor to use for OCR
- `text_rule_set` / `document_rule_set`: which parsing functions to invoke

The user selects a bank before upload. The parser dispatches to bank-specific functions based on the profile.

## Position-Based Column Splitting

The primary parsing approach uses word-level x-coordinates to assign text to columns:

1. `_extract_text_line_entries` (pdfplumber path) or `_extract_docai_line_entries` (Document AI path) extracts each line's text plus a list of words with normalised `x_min` positions.
2. `_COLUMN_BOUNDARIES` defines per-bank x-coordinate boundaries for splitting columns (e.g. where description ends and reference/category begins, where money columns start).
3. `_split_words_by_x` splits a word list into left/right text at a given x-boundary.
4. Bank-specific money extractors (e.g. `_extract_money_by_position`, `_extract_capitec_personal_money_by_position`) assign money values to their correct fields by checking which x-zone each word falls into.

### Multi-Line Transaction Merging

When a PDF line doesn't start with a date, it's treated as a continuation of the previous transaction. Words from the continuation line are merged into the appropriate columns (description, category, money) based on their x-position.

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

## Bank-Specific Parsers

### FNB
- Columns: Date, Description, Amount, Balance, Charges
- Uses the visual-row parser for Document AI; text-only fallback for pdfplumber.

### Capitec Business
- Columns: Post Date, Trans. Date, Description, Reference, Fees, Amount, Balance
- Position-based splitting: description/reference boundary at x=0.340; money columns at x=0.615/0.710/0.833.
- Handles fee-only rows (no amount) via x-zone assignment.
- Multi-line transaction merging for continuation lines.

### Capitec Personal
- Columns: Date, Description, Category, Money In, Money Out, Fee, Balance
- Description/Category boundary at x=0.505; money zones at x=0.638/0.730/0.821/0.878.
- Multi-line transaction merging for wrapped descriptions and categories.

### Standard Bank
- Columns: Description, Amount, Date (MM DD), Balance
- Date is inferred from a two-digit month+day pair plus a year extracted from the document header.

## Tuning Column Boundaries

To adjust column boundaries for a bank, update the `_COLUMN_BOUNDARIES` dict in `parser.py` and the corresponding `_*_cell_bboxes` function. Use `docs/samplePDF/debug_page0.py` to inspect raw word positions from pdfplumber or Document AI and identify the correct x-coordinate boundaries.
