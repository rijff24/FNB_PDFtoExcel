# How the App Works

Owner: Product + Engineering  
Last reviewed: 2026-04-15

## What this app does

Bank Statement To Excel converts supported bank statement PDFs into structured Excel files.

The app is built around a review-first workflow:

1. You upload a PDF statement.
2. The app extracts transactions (with OCR enabled by default).
3. You review and correct extracted rows before export.
4. You download an Excel file for your accounting workflow.

## End-to-end flow

### 1) Sign in and access

- You sign in with Google or Email/Password.
- Access is controlled by approved accounts.

### 2) Upload and extraction

- You upload one PDF statement from the home page.
- The app runs extraction using the configured parser and OCR path.
- If extraction fails or returns no rows, you get a clear error and can retry.

### 3) Review and correction

- The Review page shows extracted transactions in a table.
- You can edit row values before export.
- You can mark rows for follow-up if they need manual checks.

### 4) Export

- When you are happy with the review, export to Excel.
- The downloaded file contains your corrected transaction data.

### 5) Billing and usage tracking

- Each successful processed document contributes to your usage totals.
- Billing estimates are live during the month.
- Finalized billing values are locked at month end.

## Reliability and boundaries

- OCR usually improves extraction quality for complex statements.
- Accuracy depends on PDF quality and supported bank layouts.
- You should always review extracted data before using it downstream.
