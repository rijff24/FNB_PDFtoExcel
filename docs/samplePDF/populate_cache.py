"""One-shot script: process a sample PDF through Document AI and cache the result."""
import sys
sys.path.insert(0, ".")

from pathlib import Path
from app.services.document_ai import _process_document, _CACHE_DIR

pdf_path = Path("docs/samplePDF/FNB.pdf")
pdf_bytes = pdf_path.read_bytes()
print(f"PDF size: {len(pdf_bytes)} bytes")
print(f"Cache dir: {_CACHE_DIR.resolve()}")

doc = _process_document(pdf_bytes)
print(f"Pages: {len(doc.pages)}")
print(f"Text length: {len(doc.text)}")

import glob
cached = glob.glob(str(_CACHE_DIR / "*.json"))
print(f"Cache files: {cached}")
print("Done.")
