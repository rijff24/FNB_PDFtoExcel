# Known Limitations

Owner: Engineering  
Last reviewed: 2026-04-15

## Parsing and OCR

- Some PDF layouts still require OCR for accurate extraction.
- Low-quality scans can reduce extraction accuracy.
- Bank format changes may temporarily reduce parser accuracy.

## Quotas and protection limits

- File size, page count, per-minute, and daily limits are enforced.
- If quota backend is unavailable, OCR requests may fail closed to prevent uncontrolled spend.

## Review/session behavior

- Preview sessions are temporary and can expire.
- Unsaved review edits can be lost if you leave the page before saving.

## Billing behavior

- Billing totals are shown as live estimates during the month.
- Finalized monthly values may differ slightly after month-end finalization.

## Workarounds

- Retry with OCR enabled for hard-to-parse statements.
- Break very large PDFs into smaller files when possible.
- Save review edits before long pauses or page changes.

## When to report immediately

- Login or upload fails repeatedly for valid files
- Review page cannot load PDF or transactions
- Billing usage changes unexpectedly without recent activity
