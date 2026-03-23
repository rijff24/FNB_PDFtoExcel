def test_scaffold_imports() -> None:
    # These should import cleanly even before extraction logic is implemented.
    from app.ui.app import run  # noqa: F401
    from app.services.extraction_service import ExtractionService

    service = ExtractionService()
    assert service is not None
