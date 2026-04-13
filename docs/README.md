# FNB PDF to Excel — Documentation

This folder contains the full technical documentation for the **FNB PDF to Excel** project, a web application that extracts structured transaction data from First National Bank (FNB) statement PDFs and exports them as Excel spreadsheets.

## Documentation Index

| Document | Description |
|---|---|
| [Architecture](architecture.md) | System architecture, component diagram, data flow |
| [API Reference](api-reference.md) | All HTTP endpoints with request/response schemas |
| [Parser](parser.md) | Deep dive into the visual-row PDF parsing algorithm |
| [Document AI](document-ai.md) | Google Document AI integration, caching, and configuration |
| [Authentication](authentication.md) | Firebase Authentication flow and email allowlist |
| [Frontend](frontend.md) | Browser-side auth, esbuild bundling, and review UI |
| [Development](development.md) | Local development setup, environment variables, testing |
| [Deployment](deployment.md) | Cloud Run deployment, GCP setup, and production config |

## Quick Links

- **Repository root**: `../`
- **Backend entry point**: `../app/main.py`
- **Sample PDF & test data**: `samplePDF/`
- **Root README**: `../README.md`
