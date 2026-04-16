# Bank Statement To Excel Documentation

This folder is split by audience.

## Documentation Tracks

| Track | Purpose |
|---|---|
| [Engineering](engineering/README.md) | Maintainer-facing architecture, API, frontend, billing, parser, deployment, and operations docs. |
| [Help](help/) | Short in-app Help Center topics for signed-in users. |
| [Sample PDF tools](samplePDF/README.md) | Parser validation scripts, cached outputs, and sample-data notes. |

## Current Product Shape

- FastAPI renders server-side Jinja templates.
- Browser behavior is plain JavaScript plus a bundled Firebase Auth module.
- The main user flow is preview-first: upload, review/edit transactions, then export Excel.
- Supported enabled bank profiles are FNB, Capitec Business, Capitec Personal, and Standard Bank.
- Future UI work should keep the engineering UI audit and design contract synchronized with code changes.

## Important Engineering Docs

- [Architecture](engineering/architecture.md)
- [API Reference](engineering/api-reference.md)
- [Frontend](engineering/frontend.md)
- [UI Audit](engineering/ui-audit.md)
- [UI Design Contract](engineering/ui-design-contract.md)
- [Document AI](engineering/document-ai.md)
- [Development](engineering/development.md)
- [Operations Runbook](engineering/operations-runbook.md)
