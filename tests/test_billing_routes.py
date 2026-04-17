from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.main import app
from app.services.access_control import AuthResult
from app.services.auth import AuthorizedUser


def _fake_transparency() -> dict:
    return {
        "google_usd_per_classified_document": 0.75,
        "margin_per_document_usd": 0.05,
        "usd_to_zar": 18.5,
        "infra_monthly_usd": 9.30,
        "default_pool_scope": "user",
        "default_unassigned_pool_behavior": "per_user_fallback",
        "pricing_transparency_line_items": [],
        "notice_non_profit": "n",
    }


def test_billing_data_returns_summary_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.billing.authenticate_request",
        lambda _auth, path, request=None: AuthorizedUser(email="user@example.com", uid="u1"),
    )
    monkeypatch.setattr("app.routes.billing.billing_enabled", lambda: False)
    monkeypatch.setattr("app.routes.billing.get_billing_pricing_global", _fake_transparency)
    client = TestClient(app)
    response = client.get("/billing/data", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["billing_enabled"] is False
    assert payload["pricing_model"] == "pooled_live_finalized_v1"
    assert "shared_infra_per_document_usd" in payload["pricing"]
    assert "report" in payload


def test_billing_data_returns_organization_pool_label(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.billing.authenticate_request",
        lambda _auth, path, request=None: AuthorizedUser(email="user@example.com", uid="u-org-label"),
    )
    monkeypatch.setattr("app.routes.billing.billing_enabled", lambda: True)
    monkeypatch.setattr("app.routes.billing.get_billing_pricing_global", _fake_transparency)
    monkeypatch.setattr(
        "app.routes.billing.get_user_billing_settings",
        lambda _uid: SimpleNamespace(monthly_limit_amount=500.0, warn_pct=80.0, hard_stop_enabled=True),
    )
    monkeypatch.setattr(
        "app.routes.billing.get_billing_report",
        lambda _uid: {
            "month": "202604",
            "rollup": {"total_documents": 0, "total_non_ocr_documents": 0, "total_billable": 0.0, "event_count": 0},
            "daily_breakdown": [],
            "recent_events": [],
        },
    )
    monkeypatch.setattr(
        "app.routes.billing.resolve_billing_pool",
        lambda _uid, email=None, ym=None, pricing=None: {
            "scope": "organization",
            "pool_id": "org:o1",
            "org_id_at_lock": "o1",
        },
    )
    monkeypatch.setattr(
        "app.routes.billing.get_pool_rollup",
        lambda _pool_id, _ym=None: {
            "total_documents": 0,
            "total_non_ocr_documents": 0,
            "total_billable": 0.0,
            "event_count": 0,
        },
    )
    monkeypatch.setattr("app.routes.billing.get_organization", lambda _org_id: {"org_id": "o1", "name": "Swan Computing"})
    monkeypatch.setattr("app.routes.billing.get_finalized_statement", lambda _pool_id, _ym: None)

    client = TestClient(app)
    response = client.get("/billing/data", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["pricing"]["pool_label"] == "Swan Computing"
    assert payload["pricing"]["pool_detail"] == "Organization billing pool"
    assert payload["pricing"]["monthly_platform_cost_zar"] == 172.05
    assert payload["pricing"]["pool_id"] == "org:o1"


def test_billing_limits_updates_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.billing.authenticate_request_with_mode",
        lambda _auth, request, path: AuthResult(
            user=AuthorizedUser(email="user@example.com", uid="u2"),
            auth_mode="bearer",
        ),
    )
    monkeypatch.setattr("app.routes.billing.billing_enabled", lambda: True)
    monkeypatch.setattr(
        "app.routes.billing.update_user_billing_settings",
        lambda _uid, _updates: type(
            "Settings",
            (),
            {"monthly_limit_amount": 123.0, "warn_pct": 75.0, "hard_stop_enabled": True},
        )(),
    )
    client = TestClient(app)
    response = client.put(
        "/billing/limits",
        headers={"Authorization": "Bearer token"},
        json={"monthly_limit_amount": 123.0, "warn_pct": 75.0, "hard_stop_enabled": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["monthly_limit_amount"] == 123.0
    assert payload["settings"]["warn_pct"] == 75.0


def test_billing_page_renders() -> None:
    client = TestClient(app)
    response = client.get("/billing")
    assert response.status_code == 200
    assert "Billing" in response.text and "Usage" in response.text
    assert "transparencyScopeNote" in response.text
    assert "Calculator assumptions" in response.text
    assert "How we price each document (pooled)" in response.text


def test_billing_limits_requires_csrf_for_cookie_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.billing.authenticate_request_with_mode",
        lambda _auth, request, path: AuthResult(
            user=AuthorizedUser(email="user@example.com", uid="u3"),
            auth_mode="session_cookie",
        ),
    )
    monkeypatch.setattr("app.routes.billing.billing_enabled", lambda: True)
    client = TestClient(app)
    response = client.put(
        "/billing/limits",
        json={"monthly_limit_amount": 123.0, "warn_pct": 75.0, "hard_stop_enabled": True},
    )
    assert response.status_code == 403
