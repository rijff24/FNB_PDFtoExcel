# UI Audit

Owner: Engineering
Last reviewed: 2026-04-16

This audit records the current Swan-style UI baseline, remaining issues, and implementation risks for future UI work.

## Screen Inventory

| Screen | Template | Scripts/data | Primary user task | Current issues |
|---|---|---|---|---|
| Upload / sign-in | `app/templates/index.html` | `frontend/auth.js`, `/auth/session`, `/billing/data`, `/admin/me`, `/extract/preview` | Sign in, check usage, upload a statement for preview. | Swan dark surfaces are in place. Remaining risk is keeping signed-out/signed-in states visually consistent as auth behavior changes. |
| Review workspace | `app/templates/review.html`, `app/static/review.css` | Inline JS, PDF.js CDN, `/preview/pdf/{id}`, `/preview/data/{id}`, `PUT /preview/data/{id}`, `/preview/download/{id}` | Compare parsed transactions to the PDF, edit rows, save, and export. | Review CSS is extracted and toolbar groups are clearer. Remaining risk is complex inline JS for PDF rendering, highlighting, split resize, sync scroll, fit-width zoom, and table editing. |
| Billing | `app/templates/billing.html` | Inline JS, `/billing/data`, `PUT /billing/limits` | Understand monthly usage, pricing, estimates, limits, and recent events. | Dark Swan surfaces and table wrappers are in place. Remaining risk is long content density on small screens. |
| Help Center | `app/templates/help.html` | Markdown renderer in `app/routes/upload.py`, `docs/help/` | Read short user-safe help topics. | Help content is split from engineering docs and uses the shared visual system. Remaining risk is topic drift as app behavior changes. |
| Register | `app/templates/register.html` | Inline JS, `/register/request` | Request account access. | Uses the shared dark form pattern. Remaining risk is validation/detail messaging as signup states expand. |
| Admin | `app/templates/admin.html` | Inline JS, `/admin/*` endpoints | Operate users, billing, organizations, signup requests, reconciliation, and diagnostics. | Dark surfaces, controls, modals, and table wrappers are in place. Remaining risk is information density and very long operational tables. |

## Cross-Screen Findings

- Shared Swan tokens now cover typography, dark surfaces, inputs, buttons, tables, statuses, and layout shells.
- Review CSS is extracted to `app/static/review.css`; review behavior remains inline JavaScript and should be the next extraction target only after broader behavior tests exist.
- Standard pages now share a dark Swan-navy page feel with subtle gradient accents.
- Tables use darker wrappers and controlled overflow, but billing/admin density still needs real-device review.
- Review is still the highest-risk UI because it combines PDF rendering, synchronized scroll, editing, column resize, split resize, and export.

## Current Baseline

1. Shared styling lives in `app/static/style.css`; review-specific styling lives in `app/static/review.css`.
2. App pages use the Swan Computing palette: navy base, blue/teal accents, subtle gradients, and dark form controls.
3. Review desktop layout is viewport-sized with equal-height PDF and transaction panels.
4. Review PDF zoom is relative to fit-width; `100%` fills the PDF panel width.
5. Parsed transaction date columns are sized to show full `YYYY-MM-DD` values.
6. `/help` serves only `docs/help/` content.

## Remaining Priorities

1. Add browser-level visual smoke checks for upload, review, billing, admin, and help.
2. Split review JavaScript into maintainable modules once behavior coverage is stronger.
3. Continue reducing admin and billing density without hiding operational detail.
4. Review mobile/narrow layouts with real documents and long tables.

## Non-Goals For The Next UI Pass

- No migration to React, Vue, or another frontend framework.
- No parser or billing model changes.
- No removal of existing review capabilities such as PDF highlighting, row editing, zooming, or synchronized scrolling.
