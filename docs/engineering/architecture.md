# Architecture

## Overview

Bank Statement To Excel is a **FastAPI** web application that accepts supported South African bank statement PDFs, extracts transaction data using either local PDF layout extraction or cloud OCR (Google Document AI), opens a browser review workspace, and exports reviewed results as `.xlsx` files.

The current browser UI is server-rendered Jinja plus vanilla JavaScript. Future UI work should follow [UI Audit](ui-audit.md) and [UI Design Contract](ui-design-contract.md).

## High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          User Browser                               │
│  ┌────────────┐   ┌──────────────┐   ┌─────────────────────────┐   │
│  │ index.html  │   │ firebase-    │   │ review.html             │   │
│  │ (Upload UI) │   │ auth.js      │   │ (PDF viewer + table)    │   │
│  │             │   │ (Firebase    │   │ PDF.js canvas rendering │   │
│  │             │   │  Auth SDK)   │   │ Per-cell bbox highlight │   │
│  └──────┬──────┘   └──────┬───────┘   └────────────┬────────────┘   │
│         │                 │                         │               │
└─────────┼─────────────────┼─────────────────────────┼───────────────┘
          │  POST /extract/preview │ Cookie or Bearer auth │ GET /preview/*
          ▼                 ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend (app/)                           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Routes (app/routes/*.py)                                    │   │
│  │  ┌──────────┐  ┌────────────────┐  ┌────────────────────┐   │   │
│  │  │ GET /    │  │ POST /extract  │  │ POST /extract/     │   │   │
│  │  │ (index)  │  │ (download xlsx)│  │      preview       │   │   │
│  │  └──────────┘  └────────┬───────┘  └────────┬───────────┘   │   │
│  │                         │                    │               │   │
│  │  ┌──────────────────┐   │  ┌─────────────────┘               │   │
│  │  │ GET /review      │   │  │  ┌───────────────────────────┐  │   │
│  │  │ GET /preview/    │   │  │  │ preview_store.py          │  │   │
│  │  │   data/{id}      │   │  │  │ Redis or local fallback   │  │   │
│  │  │ GET /preview/    │   │  │  └───────────────────────────┘  │   │
│  │  │   pdf/{id}       │   │  │                                 │   │
│  │  └──────────────────┘   │  │                                 │   │
│  └─────────────────────────┼──┼─────────────────────────────────┘   │
│                            │  │                                     │
│  ┌─────────────────────────▼──▼─────────────────────────────────┐   │
│  │  Services                                                     │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐   │   │
│  │  │ auth.py      │  │ document_    │  │ parser.py         │   │   │
│  │  │ Firebase JWT │  │ ai.py        │  │ Visual-row column │   │   │
│  │  │ verification │  │ Document AI  │  │ detection parser  │   │   │
│  │  │ + allowlist  │  │ + file cache │  │ + pdfplumber      │   │   │
│  │  └──────────────┘  └──────┬───────┘  └───────────────────┘   │   │
│  │                           │          ┌───────────────────┐   │   │
│  │                           │          │ excel_export.py   │   │   │
│  │                           │          │ openpyxl builder  │   │   │
│  │                           │          └───────────────────┘   │   │
│  └───────────────────────────┼──────────────────────────────────┘   │
│                              │                                      │
└──────────────────────────────┼──────────────────────────────────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │ Google Document AI API  │
                  │ (Bank Statement Parser) │
                  └────────────────────────┘
```

## Project Structure

```
FNB_PDFtoExcel/
├── app/
│   ├── main.py                  # FastAPI app factory, router registration
│   ├── routes/
│   │   ├── __init__.py
│   │   └── upload.py            # All HTTP endpoints
│   ├── services/
│   │   ├── __init__.py
│   │   ├── auth.py              # Firebase JWT verification + email allowlist
│   │   ├── banks.py             # Bank parser profiles (FNB, Capitec, Std Bank, etc.)
│   │   ├── document_ai.py       # Document AI client + file-based caching
│   │   ├── parser.py            # Transaction extraction (position-based + legacy text)
│   │   ├── excel_export.py      # openpyxl Excel builder (bank-specific columns)
│   │   └── logging_utils.py     # Structured JSON logger
│   ├── templates/
│   │   ├── index.html           # Upload / sign-in page
│   │   └── review.html          # PDF viewer + parsed transactions table
│   ├── static/
│   │   ├── style.css            # Shared Swan-style app CSS
│   │   ├── review.css           # Review workspace CSS
│   │   └── firebase-auth.js     # Bundled Firebase Auth (from frontend/auth.js)
│   └── utils/
│       ├── __init__.py
│       └── files.py             # Temp file helpers
├── frontend/
│   ├── auth.js                  # Firebase Auth source (bundled with esbuild)
│   └── package.json             # npm deps: firebase, esbuild
├── docs/
│   ├── README.md                # This documentation index
│   ├── samplePDF/               # Test PDFs, comparison scripts, cached outputs
│   └── *.md                     # Detailed documentation files
├── requirements.txt             # Python dependencies
├── Procfile                     # Cloud Run / Heroku process command
├── .gitignore
└── README.md                    # Root project overview
```

## Data Flow

### Legacy Direct Download Flow (`POST /extract`)

1. User signs in via Firebase Authentication in the browser.
2. API clients may still call `POST /extract` directly with a PDF file, bank id, OCR flag, and auth header/cookie.
3. The browser upload UI no longer uses this as the primary path; it routes users through preview and review first.
4. Backend verifies the Firebase ID token and checks the email allowlist.
5. If OCR is enabled: sends PDF bytes to Google Document AI, receives full `Document` object, passes to `parse_transactions_from_text`.
6. If OCR is disabled: uses `pdfplumber` to extract text locally, passes to `parse_transactions_from_text`.
7. Parser returns a list of transaction dictionaries.
8. `build_excel_bytes` creates an `.xlsx` in memory.
9. Backend streams the Excel file back as a download.

### Preview & Review Flow (`POST /extract/preview`)

1. Same auth flow as above.
2. Browser sends `POST /extract/preview` with the PDF and OCR flag.
3. Backend calls `process_document_with_layout` to get the full Document AI `Document` (with bounding boxes).
4. `parse_transactions_from_document` uses the visual-row parser to extract transactions with per-field bounding boxes.
5. Backend stores the PDF bytes and parsed transactions through `app/services/preview_store.py`, using Redis when `REDIS_URL` is configured and a process-local fallback otherwise.
6. Browser redirects to `/review?session_id=<id>`.
7. Review page loads PDF via `GET /preview/pdf/{id}` and renders with PDF.js.
8. Review page loads transactions via `GET /preview/data/{id}` and renders in a table.
9. Hovering over table cells highlights the corresponding region on the PDF using bounding box coordinates.

## Key Design Decisions

- **Multi-bank support**: Configurable bank parser profiles (`app/services/banks.py`) define per-bank OCR processors, parsing rules, and UI column templates. The user selects a bank before upload; each bank has its own parser and review table layout.
- **Position-based column splitting**: Word-level x-coordinates from pdfplumber (or Document AI tokens) determine which column each word belongs to. Precise normalised x-boundaries are defined per bank in `_COLUMN_BOUNDARIES`, eliminating text-heuristic guessing.
- **Multi-line transaction merging**: Continuation lines (no leading date) are automatically merged into the previous transaction, supporting multi-line descriptions and split category/money fields.
- **Visual-row parser**: Document AI returns lines grouped by column rather than by visual row. The parser re-groups lines by y-coordinate proximity, then classifies each line into a column by x-coordinate.
- **File-based Document AI cache**: API responses are cached to `.cache/docai/` as JSON, keyed by SHA-256 hash of the PDF bytes. This avoids repeated API calls during development and testing. The cache directory is volume-mounted in Docker Compose for persistence across rebuilds.
- **Preview session storage**: `preview_store.py` stores PDF bytes and parsed data for the review UI. Production and Docker Compose use Redis through `REDIS_URL`; local single-process development can fall back to in-memory storage.
- **Swan-style server-rendered UI**: Standard pages share `app/static/style.css`; the review workspace uses `app/static/review.css` for equal-height panels, fit-width PDF rendering at `100%`, grouped toolbar controls, and transaction table sizing.
- **No service account keys**: Firebase ID tokens are verified using Google's public certificates, avoiding the need to download or manage service account JSON keys.
- **Bank-specific Excel export**: `excel_export.py` auto-detects the bank format from transaction data and exports with matching column headers (e.g. Capitec Personal exports Date, Description, Category, Money In, Money Out, Fee, Balance).
