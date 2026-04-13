import hashlib
import logging
import os
import traceback
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.cloud import documentai
from google.cloud.documentai import Document, RawDocument

from app.services.logging_utils import log_event

_CACHE_DIR = Path(os.getenv("DOCAI_CACHE_DIR", ".cache/docai"))


def _cache_key(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def _read_cache(key: str) -> Document | None:
    path = _CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        json_str = path.read_text(encoding="utf-8")
        doc = Document.from_json(json_str)
        log_event(logging.INFO, "docai_cache_hit", path=str(path))
        return doc
    except Exception:
        log_event(logging.WARNING, "docai_cache_read_failed", details=traceback.format_exc())
        return None


def _write_cache(key: str, document: Document) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CACHE_DIR / f"{key}.json"
        json_str = type(document).to_json(document)
        path.write_text(json_str, encoding="utf-8")
        log_event(logging.INFO, "docai_cache_write", path=str(path))
    except Exception as exc:
        log_event(logging.WARNING, "docai_cache_write_failed", details=str(exc))


def _process_document(pdf_bytes: bytes) -> Document:
    key = _cache_key(pdf_bytes)
    cached = _read_cache(key)
    if cached is not None:
        return cached

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.getenv("DOCUMENTAI_LOCATION", "").strip()
    processor_id = os.getenv("DOCUMENTAI_PROCESSOR_ID", "").strip()

    if not project_id or not location or not processor_id:
        raise RuntimeError(
            "Missing Document AI configuration. Set GOOGLE_CLOUD_PROJECT, "
            "DOCUMENTAI_LOCATION, and DOCUMENTAI_PROCESSOR_ID."
        )

    endpoint = f"{location}-documentai.googleapis.com"
    client = documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=endpoint)
    )
    processor_name = client.processor_path(project_id, location, processor_id)

    request = documentai.ProcessRequest(
        name=processor_name,
        raw_document=RawDocument(content=pdf_bytes, mime_type="application/pdf"),
    )
    result = client.process_document(request=request)
    _write_cache(key, result.document)
    return result.document


def extract_text_with_document_ai(pdf_bytes: bytes) -> str:
    """Backwards-compatible helper: keep using full-document processing but return text only."""
    document = _process_document(pdf_bytes)
    return document.text or ""


def process_document_with_layout(pdf_bytes: bytes) -> Document:
    """Return the full Document AI `Document`, including layout, for layout-aware parsing."""
    return _process_document(pdf_bytes)
