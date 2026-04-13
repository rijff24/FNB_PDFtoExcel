"""Compare page 0 parser results against expected CSV."""
import csv
import sys
sys.path.insert(0, ".")

from pathlib import Path
from app.services.document_ai import _process_document
from app.services.parser import parse_transactions_from_document

pdf_bytes = Path("docs/samplePDF/2. Apr23.pdf").read_bytes()
doc = _process_document(pdf_bytes)
txs = parse_transactions_from_document(doc)

page0 = [t for t in txs if t.get("page_index") == 0]

with open("docs/samplePDF/page1Table.csv", newline="") as f:
    expected = list(csv.DictReader(f))

print(f"Total transactions: {len(txs)}")
print(f"Page 0 parsed: {len(page0)}")
print(f"Expected (CSV): {len(expected)}")
print()

print("=" * 120)
print(f"{'#':>3s}  {'DATE':10s}  {'AMOUNT':>15s}  {'BALANCE':>15s}  {'REV':5s}  DESCRIPTION")
print("-" * 120)
for i, t in enumerate(page0):
    print(
        f"{i+1:3d}  "
        f"{str(t.get('date') or ''):10s}  "
        f"{str(t.get('amount') or ''):>15s}  "
        f"{str(t.get('balance') or ''):>15s}  "
        f"{'Y' if t.get('needs_review') else ' ':5s}  "
        f"{(t.get('description') or '')[:70]}"
    )

print()
print("=" * 120)
print("EXPECTED (CSV):")
print("-" * 120)
for i, row in enumerate(expected):
    print(
        f"{i+1:3d}  "
        f"{row['Date']:10s}  "
        f"{row['Amount']:>15s}  "
        f"{row['Balance']:>15s}  "
        f"{'':5s}  "
        f"{row['Description'][:70]}"
    )
