from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _markdown_files() -> list[Path]:
    return sorted(DOCS.rglob("*.md"))


def test_docs_do_not_contain_mojibake_markers() -> None:
    bad_markers = ["â", "�"]
    for path in _markdown_files() + [ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for marker in bad_markers:
            assert marker not in text, f"{path} contains mojibake marker {marker!r}"


def test_engineering_docs_do_not_repeat_stale_ui_or_auth_claims() -> None:
    stale_phrases = [
        "two HTML pages",
        '"Download Excel" and "Preview & review"',
        "Must be `true`; preview requires Document AI",
        "Auth**: None (session-based)",
        "All endpoints are defined in `app/routes/upload.py`",
    ]
    for path in sorted((DOCS / "engineering").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            assert phrase not in text, f"{path} still contains stale phrase: {phrase}"


def test_help_docs_are_split_from_engineering_docs() -> None:
    help_files = {path.name for path in (DOCS / "help").glob("*.md")}
    engineering_files = {path.name for path in (DOCS / "engineering").glob("*.md")}
    assert {"getting-started.md", "review-and-export.md", "billing-and-limits.md"}.issubset(help_files)
    assert "architecture.md" in engineering_files
    assert "architecture.md" not in help_files


def test_document_ai_source_notes_are_present() -> None:
    text = (DOCS / "engineering" / "document-ai.md").read_text(encoding="utf-8")
    assert "https://cloud.google.com/document-ai/pricing" in text
    assert "https://docs.cloud.google.com/document-ai/limits" in text
    assert "https://docs.cloud.google.com/document-ai/docs/processors-list" in text
    assert "$0.75 per classified document" in text
    assert "Last updated 2026-04-14 UTC" in text
