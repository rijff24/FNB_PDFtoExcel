# Local Development Guide

## Prerequisites

- **Python 3.11+** (3.12+ recommended)
- **Node.js 18+** (for building the Firebase Auth bundle)
- **Google Cloud CLI** (`gcloud`) for Document AI authentication
- A **Firebase project** linked to the GCP project (see [authentication.md](authentication.md))

## Setup

### 1. Clone and Install Python Dependencies

```bash
git clone <repository-url>
cd FNB_PDFtoExcel
pip install -r requirements.txt
```

**Python dependencies** (`requirements.txt`):
- `fastapi` — Web framework
- `uvicorn` — ASGI server
- `python-multipart` — Form/file upload parsing
- `jinja2` — HTML template rendering
- `google-cloud-documentai` — Document AI SDK
- `google-api-core` — GCP API core utilities
- `pdfplumber` — Local PDF text extraction (non-OCR path)
- `openpyxl` — Excel file generation
- `pyjwt` — JWT decoding and verification
- `cryptography` — X.509 certificate handling for JWT verification
- `requests` — HTTP client for fetching Firebase public certificates

### 2. Install Frontend Dependencies

```bash
cd frontend
npm install
```

### 3. Build the Firebase Auth Bundle

```bash
cd frontend
npx esbuild auth.js --bundle --platform=browser --format=iife --outfile=../app/static/firebase-auth.js
```

This must be re-run whenever `frontend/auth.js` is modified.

### 4. Set Environment Variables

Create a `.env` file or export these in your shell:

```bash
# Required for auth
export FIREBASE_PROJECT_ID="fnb-pdf-to-excel-prod-491212"
export ALLOWED_USER_EMAILS="your@email.com"
export ADMIN_EMAILS="rijff24@gmail.com"

# Required for OCR (Document AI)
export GOOGLE_CLOUD_PROJECT="fnb-pdf-to-excel-prod-491212"
export DOCUMENTAI_LOCATION="eu"
export DOCUMENTAI_PROCESSOR_ID="83c6261eda24a42f"

# Required for quota enforcement before OCR
export MAX_FILE_SIZE_MB="10"
export MAX_PAGES_PER_REQUEST="50"
export MAX_REQUESTS_PER_MINUTE_PER_USER="10"
export MAX_PAGES_PER_DAY_PER_USER="300"
export REDIS_URL="redis://localhost:6379/0"

# Optional billing/usage tracking
export BILLING_ENABLED="true"
export DEFAULT_MONTHLY_LIMIT="100.00"
export DEFAULT_WARN_PCT="80"
export FIRESTORE_DATABASE_ID="fnb-billing"
export ADMIN_ERROR_TRACKING_ENABLED="true"
export BQ_BILLING_TABLE="fnb-pdf-to-excel-prod-491212.billing_export_eu.gcp_billing_export_resource_v1_0186D4_09428F_680219"
export RECONCILIATION_TZ="Africa/Johannesburg"
export RECONCILIATION_ALERT_PCT="15"
export RECONCILIATION_COLLECTION="cost_reconciliation_daily"

# Optional
export LOG_LEVEL="DEBUG"
export DOCAI_CACHE_DIR=".cache/docai"
```

### 5. Authenticate with Google Cloud (for Document AI)

```bash
gcloud auth application-default login
```

### 6. Run the Server

```bash
uvicorn app.main:app --reload
```

The app will be available at `http://127.0.0.1:8000`.

Preview-first note:

- The home page now routes uploads to preview/review first.
- Final Excel download happens from the review page after optional edits.
- This applies to both OCR and non-OCR flows.

### 7. Run with Docker Compose (App + Redis)

```bash
docker compose up --build
```

The containerized app is available at `http://127.0.0.1:8080`.
In Compose mode, `REDIS_URL` is set to `redis://redis:6379/0`.

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `FIREBASE_PROJECT_ID` | Yes | — | Firebase/GCP project ID |
| `ALLOWED_USER_EMAILS` | Yes | — | Comma-separated email allowlist |
| `ADMIN_EMAILS` | Admin panel | — | Comma-separated admin emails allowed to use `/admin` |
| `GOOGLE_CLOUD_PROJECT` | When OCR | — | GCP project ID |
| `DOCUMENTAI_LOCATION` | When OCR | — | Document AI processor region |
| `DOCUMENTAI_PROCESSOR_ID` | When OCR | — | Document AI processor ID |
| `MAX_FILE_SIZE_MB` | Yes (OCR) | — | Maximum PDF size allowed per OCR request |
| `MAX_PAGES_PER_REQUEST` | Yes (OCR) | — | Maximum pages allowed per OCR request |
| `MAX_REQUESTS_PER_MINUTE_PER_USER` | Yes (OCR) | — | Per-user OCR request limit in a 1-minute window |
| `MAX_PAGES_PER_DAY_PER_USER` | Yes (OCR) | — | Per-user OCR page budget per day |
| `REDIS_URL` | Yes (OCR) | — | Redis connection URL for quota counters |
| `BILLING_ENABLED` | No | `false` | Enables billing and usage tracking flows |
| `DEFAULT_MONTHLY_LIMIT` | No | `100.00` | Default monthly limit for new users |
| `DEFAULT_WARN_PCT` | No | `80` | Warning threshold percentage |
| `FIRESTORE_DATABASE_ID` | No | `(default)` | Firestore database id for named databases |
| `ADMIN_ERROR_TRACKING_ENABLED` | No | `true` | Persist error events to Firestore for admin diagnostics |
| `BQ_BILLING_TABLE` | Reconciliation | — | Full BigQuery billing export table path used for financial truth |
| `RECONCILIATION_TZ` | No | `Africa/Johannesburg` | Timezone for daily reconciliation buckets |
| `RECONCILIATION_ALERT_PCT` | No | `15` | Daily/monthly variance warning threshold percentage |
| `RECONCILIATION_COLLECTION` | No | `cost_reconciliation_daily` | Firestore collection for persisted daily reconciliation snapshots |
| `LOG_LEVEL` | No | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `DOCAI_CACHE_DIR` | No | `.cache/docai` | Directory for Document AI response cache |

## Using the Document AI Cache

To avoid calling the Document AI API during development:

1. **First run**: Process a PDF normally (the response will be cached automatically).
2. **Subsequent runs**: The parser reads from `.cache/docai/<hash>.json` instead of calling the API.
3. **Pre-populate**: Run `python docs/samplePDF/populate_cache.py` to cache the sample PDF.
4. **Clear cache**: Delete `.cache/docai/` to force fresh API calls.

## Testing

### Compare Parser Output to Expected Data

```bash
python docs/samplePDF/compare.py
```

This compares the parser's output for page 0 of the sample PDF against `docs/samplePDF/page1Table.csv`.

### Debug Document AI Output

```bash
python docs/samplePDF/debug_page0.py
```

Prints all lines and tokens from page 0 with their bounding boxes, useful for tuning column boundary constants.

## Common Issues

### Firebase Sign-In Fails with `auth/unauthorized-domain`

Add your local URLs to **Firebase Console → Authentication → Settings → Authorized domains**:
- `http://127.0.0.1:8000`
- `http://localhost:8000`

### Document AI Returns Empty Results

- Ensure the PDF is a valid FNB statement.
- Check that the Document AI processor type is "Bank Statement Parser".
- Verify credentials: `gcloud auth application-default print-access-token` should return a token.

### Parser Misclassifies Columns

The column boundary constants in `parser.py` are calibrated for a specific FNB statement layout. If your statements have a different layout:
1. Run `docs/samplePDF/debug_page0.py` to inspect line x-positions.
2. Adjust `_COL_CARD_X`, `_COL_AMOUNT_X`, `_COL_BALANCE_X`, `_COL_CHARGES_X` in `parser.py`.

## Project File Structure

See [architecture.md](architecture.md) for the full project structure diagram.
