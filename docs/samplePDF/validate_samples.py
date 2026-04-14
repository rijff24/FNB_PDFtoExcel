"""Quick local check for bank parser output against sample PDFs."""
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.services.parser import parse_transactions_from_pdf_bytes_with_layout


SAMPLES: list[tuple[str, str]] = [
    ("FNB.pdf", "fnb"),
    ("Capitec Business.pdf", "capitec"),
    ("Capitec Personal.pdf", "capitec_personal"),
    ("Standard Bank.pdf", "standard_bank"),
]


def main() -> None:
    base = Path(__file__).resolve().parent
    for filename, bank_id in SAMPLES:
        path = base / filename
        if not path.exists():
            print(f"MISSING|{filename}|{bank_id}")
            continue
        rows = parse_transactions_from_pdf_bytes_with_layout(path.read_bytes(), bank_id=bank_id)
        needs_review = sum(1 for row in rows if row.get("needs_review"))
        print(f"OK|{filename}|{bank_id}|rows={len(rows)}|needs_review={needs_review}")


if __name__ == "__main__":
    main()
