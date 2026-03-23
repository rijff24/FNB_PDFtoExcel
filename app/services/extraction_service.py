from app.parsers.fnb_statement_parser import FNBStatementParser


class ExtractionService:
    """Orchestrates PDF parsing and Excel export (scaffold only)."""

    def __init__(self) -> None:
        self.parser = FNBStatementParser()

    def extract_to_excel(self, pdf_path: str, excel_path: str) -> None:
        """
        Extract transactions from the given PDF and write them to Excel.

        This is a placeholder for the next development step.
        """
        # Keep extraction logic out of this scaffolding step.
        raise NotImplementedError("Extraction logic is not implemented yet.")
