# Engineering Documentation

Owner: Engineering
Last reviewed: 2026-04-16

These docs are implementation-facing. User-safe Help Center content lives in `docs/help/` and is the only documentation source served by `/help`.

## Core References

| Document | Description |
|---|---|
| [Architecture](architecture.md) | System layout, route modules, data flow, and key design decisions. |
| [API Reference](api-reference.md) | Auth requirements, request shapes, response shapes, and route ownership. |
| [Frontend](frontend.md) | Templates, static assets, current UI behavior, and remaining extraction plan. |
| [UI Audit](ui-audit.md) | Current screen inventory, UI baseline, remaining issues, and redesign risks. |
| [UI Design Contract](ui-design-contract.md) | Stable design rules for maintaining the Swan-style app UI. |
| [Parser](parser.md) | Multi-bank parser behavior and validation notes. |
| [Document AI](document-ai.md) | OCR integration, official limits/pricing source notes, and cache behavior. |
| [Billing Transparency](billing-transparency.md) | Pooled billing model and customer-visible totals. |
| [Authentication](authentication.md) | Firebase session-cookie and Bearer fallback flow. |
| [Development](development.md) | Local setup, test commands, and tooling expectations. |
| [Deployment](deployment.md) | Cloud Run deployment notes. |
| [Operations Runbook](operations-runbook.md) | Smoke checks, rollback criteria, and day-2 operations. |

## Documentation Rules

- Keep API and route docs synchronized with `app/routes/`.
- Keep `/help` user-safe and concise; do not point it at engineering docs.
- Cite official Google Cloud pages for Document AI pricing, processor behavior, and limits.
- Update the UI audit and frontend docs whenever the visual system, review workspace behavior, or Help Center content source changes.
