"""Dump full parsed Capitec Business output to JSON."""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.services.parser import parse_transactions_from_pdf_bytes_with_layout


def main() -> None:
    sample_path = Path("docs/samplePDF/Capitec Business.pdf")
    output_path = Path("docs/samplePDF/capitec_business_parsed_output.json")

    if not sample_path.exists():
        raise SystemExit(f"Sample not found: {sample_path}")

    rows = parse_transactions_from_pdf_bytes_with_layout(
        sample_path.read_bytes(),
        bank_id="capitec",
    )
    output_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
