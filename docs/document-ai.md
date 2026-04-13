# Google Document AI Integration

**File**: `app/services/document_ai.py`

The application uses Google Cloud's **Document AI** service with a **Bank Statement Parser** processor to perform OCR and layout extraction on FNB statement PDFs.

## Overview

Document AI provides:
- **OCR text extraction**: Full text content of the PDF.
- **Layout information**: Bounding boxes for every line and token, with normalized (0–1) coordinates relative to page dimensions.
- **Page structure**: Pages, lines, tokens, paragraphs, and tables.

The visual-row parser (see [parser.md](parser.md)) relies on the layout information — specifically the `line` and `token` bounding boxes — to reconstruct the transaction table structure.

## Configuration

| Environment Variable | Required | Description |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | Yes (when OCR enabled) | GCP project ID |
| `DOCUMENTAI_LOCATION` | Yes (when OCR enabled) | Processor region (e.g., `eu`) |
| `DOCUMENTAI_PROCESSOR_ID` | Yes (when OCR enabled) | Document AI processor ID |

## API Functions

### `process_document_with_layout(pdf_bytes: bytes) -> Document`

Primary function used by the preview/review flow. Returns the full Document AI `Document` object including all pages, lines, tokens, and their bounding boxes.

### `extract_text_with_document_ai(pdf_bytes: bytes) -> str`

Backwards-compatible helper that returns only the extracted text (no layout data). Used by the direct download flow when OCR is enabled.

### `_process_document(pdf_bytes: bytes) -> Document`

Internal function that handles the actual API call. Checks the file cache first, calls the API only on cache miss, and writes the result to cache on success.

## File-Based Caching

To avoid repeated (and costly) Document AI API calls during development and testing, responses are cached to disk.

### How It Works

1. **Cache key**: SHA-256 hash of the raw PDF bytes.
2. **Cache directory**: `.cache/docai/` (configurable via `DOCAI_CACHE_DIR` env var).
3. **Cache format**: JSON files named `<sha256_hash>.json`, containing the serialized `Document` protobuf.
4. **Read**: `Document.from_json(json_str)` — uses proto-plus deserialization.
5. **Write**: `type(document).to_json(document)` — uses proto-plus serialization.

### Cache Flow

```
_process_document(pdf_bytes)
    │
    ├── Compute SHA-256 hash of pdf_bytes
    │
    ├── Check .cache/docai/<hash>.json
    │   ├── EXISTS → Document.from_json() → return cached Document
    │   └── NOT FOUND → continue
    │
    ├── Call Document AI API
    │
    ├── Write result to .cache/docai/<hash>.json
    │
    └── Return Document
```

### Cache Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `DOCAI_CACHE_DIR` | `.cache/docai` | Directory for cached Document AI responses |

The `.cache/` directory is in `.gitignore` and should not be committed.

### Populating the Cache

For development, you can pre-populate the cache by running:

```bash
python docs/samplePDF/populate_cache.py
```

This processes `docs/samplePDF/2. Apr23.pdf` through Document AI and saves the result. Subsequent parser runs against the same PDF will use the cache.

## GCP Setup

### Document AI Processor

1. Go to **Document AI → Processors → Create processor** in the GCP Console.
2. Select **Bank Statement Parser** as the processor type.
3. Name: `fnb-bank-statement-parser`
4. Region: `eu` (must match `DOCUMENTAI_LOCATION`)
5. Note the processor ID for `DOCUMENTAI_PROCESSOR_ID`.

### Authentication for Local Development

```bash
gcloud auth application-default login
```

This creates Application Default Credentials that the `google-cloud-documentai` library uses automatically. No service account key file is needed.

### Authentication for Cloud Run

Assign the **Document AI API User** role to the Cloud Run service account:
```
cloudrun-pdf-service@<project-id>.iam.gserviceaccount.com
```

## Troubleshooting

### Cache Not Working

- Check that the `DOCAI_CACHE_DIR` path is writable.
- Look for `docai_cache_read_failed` or `docai_cache_write_failed` log events.
- Ensure the Document AI SDK version matches the proto-plus serialization format.

### API Errors

- Verify all three env vars are set: `GOOGLE_CLOUD_PROJECT`, `DOCUMENTAI_LOCATION`, `DOCUMENTAI_PROCESSOR_ID`.
- Ensure the Document AI API is enabled in the GCP project.
- Check that the service account / ADC credentials have the `documentai.documents.process` permission.
