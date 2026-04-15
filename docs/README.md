# Bank Statement To Excel — Documentation

This folder contains the full technical documentation for the **Bank Statement To Excel** project, a web application that extracts structured transaction data from South African bank statement PDFs (FNB, Capitec Business, Capitec Personal, Standard Bank) and exports them as Excel spreadsheets.

## Documentation Index

| Document | Description |
|---|---|
| [Architecture](architecture.md) | System architecture, component diagram, data flow |
| [API Reference](api-reference.md) | All HTTP endpoints with request/response schemas |
| [Parser](parser.md) | Multi-bank position-based PDF parsing algorithm |
| [Document AI](document-ai.md) | Google Document AI integration, caching, and configuration |
| [Authentication](authentication.md) | Firebase Authentication flow and email allowlist |
| [Frontend](frontend.md) | Browser-side auth, esbuild bundling, and review UI |
| [Development](development.md) | Local development setup, environment variables, testing |
| [Deployment](deployment.md) | Cloud Run deployment, GCP setup, and production config |
| [Beta Onboarding](beta-onboarding.md) | Tester quickstart and first-run checklist |
| [Known Limitations](known-limitations.md) | Current product limits and workarounds |
| [Support and Escalation](support-and-escalation.md) | Severity levels, response expectations, escalation path |
| [Incident and Recovery](incident-and-recovery.md) | Incident lifecycle and recovery checklist |
| [Billing Transparency](billing-transparency.md) | Pooled billing explanation and user-visible totals |
| [Operations Runbook](operations-runbook.md) | Day-2 operations, smoke checks, rollback guidance |

## Quick Links

- **Repository root**: `../`
- **Backend entry point**: `../app/main.py`
- **Sample PDF & test data**: `samplePDF/`
- **Root README**: `../README.md`
