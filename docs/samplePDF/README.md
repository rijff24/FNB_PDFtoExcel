# Sample Statements Catalog

All bank statement samples used for OCR/parser development live in this folder:
`c:/dev/FNB_PDFtoExcel/docs/samplePDF`

## Current files in this folder

- `2. Apr23.pdf` (referenced by local helper scripts; may be untracked locally)
- `page1Table.csv` (expected rows for page-1 comparison)
- `actual_output.json` (captured extraction output snapshot)
- `compare.py`, `debug_page0.py`, `populate_cache.py` (local validation helpers)

## Bank rollout mapping

- **FNB**: baseline parser artifacts already present (`2. Apr23.pdf`, `page1Table.csv`).
- **Capitec**: place sample PDF(s) in this folder and use bank ID `capitec` / `capitec_personal`.
- **Standard Bank**: place sample PDF(s) in this folder and use bank ID `standard_bank`.

## Validation expectation

For each bank rollout phase, parser/OCR changes should be validated against samples in this folder before marking the phase complete.
