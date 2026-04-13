# Architecture

## Overview

FNB PDF to Excel is a **FastAPI** web application that accepts FNB bank statement PDFs, extracts transaction data using either local text extraction (pdfplumber) or cloud OCR (Google Document AI), and exports structured results as `.xlsx` files. A browser-based review UI allows users to visually inspect parsed transactions against the original PDF.

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
          │  POST /extract  │  Bearer token           │  GET /preview/*
          ▼                 ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend (app/)                           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Routes (app/routes/upload.py)                               │   │
│  │  ┌──────────┐  ┌────────────────┐  ┌────────────────────┐   │   │
│  │  │ GET /    │  │ POST /extract  │  │ POST /extract/     │   │   │
│  │  │ (index)  │  │ (download xlsx)│  │      preview       │   │   │
│  │  └──────────┘  └────────┬───────┘  └────────┬───────────┘   │   │
│  │                         │                    │               │   │
│  │  ┌──────────────────┐   │  ┌─────────────────┘               │   │
│  │  │ GET /review      │   │  │  ┌───────────────────────────┐  │   │
│  │  │ GET /preview/    │   │  │  │ In-memory session store   │  │   │
│  │  │   data/{id}      │   │  │  │ (PREVIEW_SESSIONS dict)   │  │   │
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
│   │   ├── document_ai.py       # Document AI client + file-based caching
│   │   ├── parser.py            # Transaction extraction (visual-row + legacy text)
│   │   ├── excel_export.py      # openpyxl Excel builder
│   │   └── logging_utils.py     # Structured JSON logger
│   ├── templates/
│   │   ├── index.html           # Upload / sign-in page
│   │   └── review.html          # PDF viewer + parsed transactions table
│   ├── static/
│   │   ├── style.css            # Shared CSS
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

### Direct Download Flow (`POST /extract`)

1. User signs in via Firebase Authentication in the browser.
2. User uploads a PDF and clicks "Download Excel".
3. Browser sends `POST /extract` with the PDF file and `Authorization: Bearer <token>`.
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
5. Backend stores the PDF bytes and parsed transactions in `PREVIEW_SESSIONS` (in-memory dict keyed by session ID).
6. Browser redirects to `/review?session_id=<id>`.
7. Review page loads PDF via `GET /preview/pdf/{id}` and renders with PDF.js.
8. Review page loads transactions via `GET /preview/data/{id}` and renders in a table.
9. Hovering over table cells highlights the corresponding region on the PDF using bounding box coordinates.

## Key Design Decisions

- **Visual-row parser**: Document AI returns lines grouped by column rather than by visual row. The parser re-groups lines by y-coordinate proximity, then classifies each line into a column (date/description, amount, balance, charges) by x-coordinate. This is more robust than text-line-based parsing for FNB statements.
- **File-based Document AI cache**: API responses are cached to `.cache/docai/` as JSON, keyed by SHA-256 hash of the PDF bytes. This avoids repeated API calls during development and testing.
- **In-memory preview sessions**: The `PREVIEW_SESSIONS` dict stores PDF bytes and parsed data for the review UI. This is suitable for single-process deployments; production would use Redis or a database.
- **No service account keys**: Firebase ID tokens are verified using Google's public certificates, avoiding the need to download or manage service account JSON keys.
