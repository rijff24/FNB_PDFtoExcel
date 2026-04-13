# API Reference

All endpoints are defined in `app/routes/upload.py` and served by the FastAPI application.

## Endpoints

### `POST /auth/session`

Creates a backend Firebase session cookie from a Firebase ID token.

- **Auth**: Firebase ID token in request body
- **Content-Type**: `application/json`
- **Body**:

```json
{ "id_token": "<firebase_id_token>" }
```

- **Response**: `200 JSON` with `ok`, `email`, `uid`
- **Cookies set**:
  - `session` (HttpOnly session cookie)
  - `csrf_token` (readable CSRF cookie)

---

### `DELETE /auth/session`

Clears backend auth cookies.

- **Auth**: None
- **Response**: `200 JSON` `{ "ok": true }`

---

### `GET /auth/me`

Returns current authenticated user context.

- **Auth**: Required — Bearer token or session cookie
- **Response**: `200 JSON` with `authenticated`, `email`, `uid`, `auth_mode`

---

### `GET /`

Serves the upload/sign-in page.

- **Auth**: None (public page)
- **Response**: HTML (`index.html` template)

---

### `POST /extract`

Extracts transactions from a PDF and returns an Excel file.

- **Auth**: Required — `Authorization: Bearer <Firebase ID token>` or session cookie
- **CSRF**: If session-cookie authenticated, send `X-CSRF-Token`
- **Content-Type**: `multipart/form-data`

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | File (PDF) | Yes | The FNB statement PDF to process |
| `enable_ocr` | Boolean | No (default `false`) | Whether to use Google Document AI for OCR |

**Success Response** (`200`):
- Content-Type: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- Content-Disposition: `attachment; filename="<original>_transactions.xlsx"`
- Body: Binary Excel file

**Error Responses**:

| Status | Condition |
|---|---|
| `400` | Not a PDF, empty file, or Document AI misconfigured |
| `401` | Missing or invalid Bearer token |
| `403` | User email not in allowlist |
| `422` | No transactions extracted from the PDF |
| `500` | Server misconfiguration (missing env vars) |

---

### `POST /extract/preview`

Extracts transactions with full layout/bounding-box data for the review UI.

- **Auth**: Required — `Authorization: Bearer <Firebase ID token>` or session cookie
- **CSRF**: If session-cookie authenticated, send `X-CSRF-Token`
- **Content-Type**: `multipart/form-data`

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | File (PDF) | Yes | The FNB statement PDF |
| `enable_ocr` | Boolean | Yes (`true`) | Must be `true`; preview requires Document AI |

**Success Response** (`200 JSON`):

```json
{
  "session_id": "a1b2c3d4...",
  "transactions": [
    {
      "id": "tx_0001",
      "date": "2023-04-03",
      "description": "POS Purchase - SPAR",
      "amount": -245.50,
      "balance": 12500.00,
      "charges": 3.95,
      "needs_review": false,
      "page_index": 0,
      "bbox": { "x_min": 0.03, "y_min": 0.12, "x_max": 0.97, "y_max": 0.14 },
      "bbox_row": { "x_min": 0.03, "y_min": 0.12, "x_max": 0.97, "y_max": 0.14 },
      "bbox_date": { "x_min": 0.03, "y_min": 0.12, "x_max": 0.10, "y_max": 0.14 },
      "bbox_description": { "x_min": 0.03, "y_min": 0.12, "x_max": 0.65, "y_max": 0.14 },
      "bbox_amount": { "x_min": 0.70, "y_min": 0.12, "x_max": 0.82, "y_max": 0.14 },
      "bbox_balance": { "x_min": 0.83, "y_min": 0.12, "x_max": 0.92, "y_max": 0.14 },
      "bbox_charges": { "x_min": 0.92, "y_min": 0.12, "x_max": 0.97, "y_max": 0.14 }
    }
  ],
  "pages": [
    { "page_index": 0, "width": 1.0, "height": 1.0 }
  ]
}
```

**Error Responses**: Same as `POST /extract`, plus `400` if `enable_ocr` is `false`.

---

### `GET /review`

Serves the review page (PDF viewer + transactions table).

- **Auth**: None (session-based; the session must exist)
- **Query Parameter**: `session_id` — the session ID from `/extract/preview`
- **Response**: HTML (`review.html` template)

---

### `GET /preview/data/{session_id}`

Returns the parsed transaction data for a preview session.

- **Auth**: Required — `Authorization: Bearer <Firebase ID token>` or session cookie
- **Response** (`200 JSON`):

```json
{
  "session_id": "a1b2c3d4...",
  "transactions": [ ... ]
}
```

| Status | Condition |
|---|---|
| `404` | Session not found (expired or invalid ID) |

---

### `PUT /preview/data/{session_id}`

Persists edited transaction data for a preview session.

- **Auth**: Required — `Authorization: Bearer <Firebase ID token>` or session cookie
- **CSRF**: If session-cookie authenticated, send `X-CSRF-Token`
- **Content-Type**: `application/json`

Request body:

```json
{
  "transactions": [
    {
      "id": "tx_0001",
      "date": "2023-04-03",
      "description": "POS Purchase - SPAR",
      "amount": -245.50,
      "balance": 12500.00,
      "charges": 3.95,
      "needs_review": false,
      "review_state": "done"
    }
  ]
}
```

Response (`200 JSON`):

```json
{
  "session_id": "a1b2c3d4...",
  "transactions": [ ... ]
}
```

| Status | Condition |
|---|---|
| `400` | Invalid payload (`transactions` missing or not a list) |
| `404` | Session not found |

---

### `GET /preview/pdf/{session_id}`

Returns the raw PDF bytes for a preview session.

- **Auth**: None (session-based)
- **Response** (`200`): `application/pdf` binary
- **Error**: `404` if session not found

---

### `GET /preview/download/{session_id}`

Downloads an Excel file built from the session’s current (possibly user-edited) transactions.

- **Auth**: None (session-based)
- **Response** (`200`): `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- **Content-Disposition**: `attachment; filename="statement_preview_edited.xlsx"`
- **Error**: `404` if session not found

---

## Transaction Object Schema

Each transaction in the API response contains:

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Stable identifier (`tx_0001`, `tx_0002`, ...) |
| `date` | `string \| null` | Normalized date (`YYYY-MM-DD`) or `null` |
| `description` | `string \| null` | Transaction description text |
| `amount` | `float \| null` | Signed amount (negative = debit, positive = credit) |
| `balance` | `float \| null` | Account balance after this transaction |
| `charges` | `float \| null` | Accrued bank charges for this transaction |
| `needs_review` | `boolean` | `true` if amount or balance could not be parsed |
| `review_state` | `string` | UI review state: `blank`, `needs`, or `done` |
| `page_index` | `integer` | Zero-based PDF page number |
| `bbox` | `object` | Row-level bounding box (alias of `bbox_row`) |
| `bbox_row` | `object` | Full row bounding box |
| `bbox_date` | `object` | Date field bounding box |
| `bbox_description` | `object` | Description field bounding box |
| `bbox_amount` | `object` | Amount field bounding box |
| `bbox_balance` | `object` | Balance field bounding box |
| `bbox_charges` | `object` | Charges field bounding box |

All bounding boxes use normalized coordinates (0.0–1.0) relative to page dimensions:

```json
{ "x_min": 0.03, "y_min": 0.12, "x_max": 0.65, "y_max": 0.14 }
```
