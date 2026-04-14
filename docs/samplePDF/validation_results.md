# Local Validation Results

Command run from repo root:

`python docs/samplePDF/validate_samples.py`

Latest observed output:

- `FNB.pdf` (`fnb`): `rows=0`, `needs_review=0`
- `Capitec Business.pdf` (`capitec`): `rows=77`, `needs_review=0`
- `Capitec Personal.pdf` (`capitec_personal`): `rows=90`, `needs_review=0`
- `Standard Bank.pdf` (`standard_bank`): `rows=167`, `needs_review=0`

Notes:

- These results are from the current non-OCR parser path.
- For scanned/image-only statements, the backend now returns `ocr_recommended` when OCR is disabled.
