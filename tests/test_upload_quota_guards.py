import io

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.main import app
from app.services.auth import AuthorizedUser


def _pdf_bytes(page_count: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _set_quota_env(monkeypatch) -> None:
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "10")
    monkeypatch.setenv("MAX_PAGES_PER_REQUEST", "5")
    monkeypatch.setenv("MAX_REQUESTS_PER_MINUTE_PER_USER", "10")
    monkeypatch.setenv("MAX_PAGES_PER_DAY_PER_USER", "20")
    monkeypatch.setenv("REDIS_URL", "redis://fake")


def test_extract_ocr_enforces_quota_before_docai(monkeypatch) -> None:
    _set_quota_env(monkeypatch)
    monkeypatch.setattr(
        "app.routes.upload._authenticate_mutating_request",
        lambda _request, _auth, path: AuthorizedUser(email="user@example.com", uid="u1"),
    )
    monkeypatch.setattr("app.routes.upload.enforce_redis_quotas", lambda *_args, **_kwargs: None)

    called = {"docai": False}

    def _fail_if_called(_pdf: bytes, bank_id: str = "fnb") -> str:
        called["docai"] = True
        return "text"

    monkeypatch.setattr("app.routes.upload.extract_text_with_document_ai", _fail_if_called)

    client = TestClient(app)
    response = client.post(
        "/extract",
        files={"file": ("statement.pdf", _pdf_bytes(page_count=6), "application/pdf")},
        data={"enable_ocr": "true"},
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 400
    assert "max pages" in response.json()["detail"].lower()
    assert called["docai"] is False


def test_extract_no_ocr_bypasses_quota_backend(monkeypatch) -> None:
    _set_quota_env(monkeypatch)
    monkeypatch.setattr(
        "app.routes.upload._authenticate_mutating_request",
        lambda _request, _auth, path: AuthorizedUser(email="user@example.com", uid="u1"),
    )

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("quota backend should not run on non-OCR path")

    monkeypatch.setattr("app.routes.upload.enforce_redis_quotas", _should_not_run)
    monkeypatch.setattr(
        "app.routes.upload.parse_transactions_from_pdf_bytes",
        lambda _pdf, forced_year=None, bank_id="fnb": [
            {"date": "2026-01-01", "description": "x", "amount": 1.0, "balance": 1.0},
        ],
    )

    client = TestClient(app)
    response = client.post(
        "/extract",
        files={"file": ("statement.pdf", _pdf_bytes(page_count=1), "application/pdf")},
        data={"enable_ocr": "false"},
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_extract_preview_enforces_quota(monkeypatch) -> None:
    _set_quota_env(monkeypatch)
    monkeypatch.setattr(
        "app.routes.upload._authenticate_mutating_request",
        lambda _request, _auth, path: AuthorizedUser(email="user@example.com", uid="u2"),
    )
    monkeypatch.setattr("app.routes.upload.enforce_redis_quotas", lambda *_args, **_kwargs: None)

    called = {"docai": False}

    class _Doc:
        pages = []

    def _fail_if_called(_pdf: bytes, bank_id: str = "fnb"):
        called["docai"] = True
        return _Doc()

    monkeypatch.setattr("app.routes.upload.process_document_with_layout", _fail_if_called)

    client = TestClient(app)
    response = client.post(
        "/extract/preview",
        files={"file": ("statement.pdf", _pdf_bytes(page_count=6), "application/pdf")},
        data={"enable_ocr": "true"},
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 400
    assert "max pages" in response.json()["detail"].lower()
    assert called["docai"] is False


def test_extract_preview_non_ocr_uses_pdfplumber_layout(monkeypatch) -> None:
    _set_quota_env(monkeypatch)
    monkeypatch.setattr(
        "app.routes.upload._authenticate_mutating_request",
        lambda _request, _auth, path: AuthorizedUser(email="user@example.com", uid="u3"),
    )

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("OCR quota backend should not run when OCR is disabled")

    monkeypatch.setattr("app.routes.upload.enforce_redis_quotas", _should_not_run)
    monkeypatch.setattr(
        "app.routes.upload.parse_transactions_from_pdf_bytes_with_layout",
        lambda _pdf, forced_year=None, bank_id="fnb": [
            {
                "date": "2026-01-01",
                "description": "A",
                "amount": 10.0,
                "balance": 20.0,
                "charges": None,
                "page_index": 0,
                "needs_review": False,
                "bbox_row": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.6, "y_max": 0.2},
            }
        ],
    )

    client = TestClient(app)
    response = client.post(
        "/extract/preview",
        files={"file": ("statement.pdf", _pdf_bytes(page_count=1), "application/pdf")},
        data={"enable_ocr": "false"},
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"]
    assert len(payload["transactions"]) == 1
    assert payload["transactions"][0]["bbox"]
    assert payload["pages"] == [{"page_index": 0, "width": 1.0, "height": 1.0}]


def test_extract_preview_billing_limit_blocks(monkeypatch) -> None:
    _set_quota_env(monkeypatch)
    monkeypatch.setattr(
        "app.routes.upload._authenticate_mutating_request",
        lambda _request, _auth, path: AuthorizedUser(email="user@example.com", uid="u4"),
    )
    monkeypatch.setattr("app.routes.upload.billing_enabled", lambda: True)
    monkeypatch.setattr(
        "app.routes.upload._billing_precheck",
        lambda **_kwargs: (_ for _ in ()).throw(
            __import__("fastapi").HTTPException(status_code=429, detail={"code": "billing_limit_reached"})
        ),
    )

    client = TestClient(app)
    response = client.post(
        "/extract/preview",
        files={"file": ("statement.pdf", _pdf_bytes(page_count=1), "application/pdf")},
        data={"enable_ocr": "false"},
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 429


def test_extract_preview_billing_warning_metadata(monkeypatch) -> None:
    _set_quota_env(monkeypatch)
    monkeypatch.setattr(
        "app.routes.upload._authenticate_mutating_request",
        lambda _request, _auth, path: AuthorizedUser(email="user@example.com", uid="u5"),
    )
    monkeypatch.setattr("app.routes.upload.billing_enabled", lambda: True)
    monkeypatch.setattr("app.routes.upload.enforce_redis_quotas", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.routes.upload._billing_precheck",
        lambda **_kwargs: {
            "page_count": 2,
            "cost": None,
            "decision": type(
                "Decision",
                (),
                {"warning": "warn", "projected_total": 12.5, "limit_remaining": 3.5},
            )(),
        },
    )
    monkeypatch.setattr(
        "app.routes.upload.parse_transactions_from_pdf_bytes_with_layout",
        lambda _pdf, forced_year=None, bank_id="fnb": [
            {
                "date": "2026-01-01",
                "description": "A",
                "amount": 10.0,
                "balance": 20.0,
                "charges": None,
                "page_index": 0,
                "needs_review": False,
                "bbox_row": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.6, "y_max": 0.2},
            }
        ],
    )

    client = TestClient(app)
    response = client.post(
        "/extract/preview",
        files={"file": ("statement.pdf", _pdf_bytes(page_count=1), "application/pdf")},
        data={"enable_ocr": "false"},
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["billing_warning"] == "warn"
    assert payload["billing_projected_total"] == 12.5
    assert payload["billing_limit_remaining"] == 3.5


def test_extract_preview_bank_defaults_and_normalizes(monkeypatch) -> None:
    _set_quota_env(monkeypatch)
    monkeypatch.setattr(
        "app.routes.upload._authenticate_mutating_request",
        lambda _request, _auth, path: AuthorizedUser(email="user@example.com", uid="u6"),
    )
    monkeypatch.setattr("app.routes.upload.enforce_redis_quotas", lambda *_args, **_kwargs: None)

    seen_bank_ids: list[str] = []

    def _parse_layout(_pdf: bytes, forced_year=None, bank_id: str = "fnb"):
        seen_bank_ids.append(bank_id)
        return [
            {
                "date": "2026-01-01",
                "description": "A",
                "amount": 10.0,
                "balance": 20.0,
                "charges": None,
                "page_index": 0,
                "needs_review": False,
                "bbox_row": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.6, "y_max": 0.2},
            }
        ]

    monkeypatch.setattr("app.routes.upload.parse_transactions_from_pdf_bytes_with_layout", _parse_layout)

    client = TestClient(app)
    files = {"file": ("statement.pdf", _pdf_bytes(page_count=1), "application/pdf")}
    headers = {"Authorization": "Bearer token"}

    response_default = client.post(
        "/extract/preview",
        files=files,
        data={"enable_ocr": "false"},
        headers=headers,
    )
    assert response_default.status_code == 200

    response_alias = client.post(
        "/extract/preview",
        files=files,
        data={"enable_ocr": "false", "bank": "capitecpersonal"},
        headers=headers,
    )
    assert response_alias.status_code == 200

    response_disabled = client.post(
        "/extract/preview",
        files=files,
        data={"enable_ocr": "false", "bank": "absa"},
        headers=headers,
    )
    assert response_disabled.status_code == 200

    assert seen_bank_ids == ["fnb", "capitec_personal", "fnb"]


def test_extract_preview_returns_ocr_recommended_on_empty_non_ocr(monkeypatch) -> None:
    _set_quota_env(monkeypatch)
    monkeypatch.setattr(
        "app.routes.upload._authenticate_mutating_request",
        lambda _request, _auth, path: AuthorizedUser(email="user@example.com", uid="u9"),
    )
    monkeypatch.setattr("app.routes.upload.enforce_redis_quotas", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.routes.upload.parse_transactions_from_pdf_bytes_with_layout",
        lambda _pdf, forced_year=None, bank_id="fnb": [],
    )

    client = TestClient(app)
    response = client.post(
        "/extract/preview",
        files={"file": ("statement.pdf", _pdf_bytes(page_count=1), "application/pdf")},
        data={"enable_ocr": "false", "bank": "capitec"},
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "ocr_recommended"
    assert detail["suggested_enable_ocr"] is True
    assert detail["bank"] == "capitec"


def test_extract_returns_ocr_recommended_on_empty_non_ocr(monkeypatch) -> None:
    _set_quota_env(monkeypatch)
    monkeypatch.setattr(
        "app.routes.upload._authenticate_mutating_request",
        lambda _request, _auth, path: AuthorizedUser(email="user@example.com", uid="u10"),
    )
    monkeypatch.setattr(
        "app.routes.upload.parse_transactions_from_pdf_bytes",
        lambda _pdf, forced_year=None, bank_id="fnb": [],
    )

    client = TestClient(app)
    response = client.post(
        "/extract",
        files={"file": ("statement.pdf", _pdf_bytes(page_count=1), "application/pdf")},
        data={"enable_ocr": "false", "bank": "standard_bank"},
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "ocr_recommended"
    assert detail["suggested_enable_ocr"] is True
    assert detail["bank"] == "standard_bank"


def test_extract_requires_csrf_when_cookie_auth(monkeypatch) -> None:
    _set_quota_env(monkeypatch)

    def _auth_result(_authorization, request=None, path=None):
        from app.services.access_control import AuthResult

        return AuthResult(
            user=AuthorizedUser(email="user@example.com", uid="u7"),
            auth_mode="session_cookie",
        )

    monkeypatch.setattr("app.routes.upload.authenticate_request_with_mode", _auth_result)
    client = TestClient(app)
    response = client.post(
        "/extract",
        files={"file": ("statement.pdf", _pdf_bytes(page_count=1), "application/pdf")},
        data={"enable_ocr": "false"},
    )
    assert response.status_code == 403


def test_preview_read_endpoints_require_auth(monkeypatch) -> None:
    monkeypatch.setattr("app.routes.upload.get_preview_session", lambda _session_id: {"pdf_bytes": b"pdf", "transactions": []})
    client = TestClient(app)
    assert client.get("/preview/data/s1").status_code == 401
    assert client.get("/preview/pdf/s1").status_code == 401
    assert client.get("/preview/download/s1").status_code == 401


def test_preview_read_endpoints_authenticated(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.upload.authenticate_request",
        lambda _auth, request=None, path=None: AuthorizedUser(email="user@example.com", uid="u8"),
    )
    monkeypatch.setattr(
        "app.routes.upload.get_preview_session",
        lambda _session_id: {"pdf_bytes": b"%PDF-1.4", "transactions": [{"id": "tx_0001"}]},
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}

    data_response = client.get("/preview/data/s1", headers=headers)
    assert data_response.status_code == 200
    assert data_response.json()["transactions"] == [{"id": "tx_0001"}]

    pdf_response = client.get("/preview/pdf/s1", headers=headers)
    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"].startswith("application/pdf")

    download_response = client.get("/preview/download/s1", headers=headers)
    assert download_response.status_code == 200
    assert download_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

