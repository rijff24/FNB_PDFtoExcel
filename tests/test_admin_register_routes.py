from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services.access_control import AuthResult
from app.services.auth import AuthorizedUser


def test_register_request_creates_pending(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.register.create_signup_request",
        lambda email, requested_name, requested_organization, how_heard_about_us: {
            "request_id": "r1",
            "status": "pending",
            "suggested_org_id": None,
            "email": email,
            "requested_name": requested_name,
            "requested_organization": requested_organization,
            "how_heard_about_us": how_heard_about_us,
        },
    )
    client = TestClient(app)
    response = client.post(
        "/register/request",
        json={
            "email": "new@example.com",
            "requested_name": "New User",
            "organization": "Acme Holdings",
            "how_heard_about_us": "friend",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["request_id"] == "r1"


def test_admin_data_requires_admin(monkeypatch) -> None:
    def _deny(_auth, path, require_admin=False, request=None):
        raise HTTPException(status_code=403, detail="Admin access required.")

    monkeypatch.setattr("app.routes.admin.authenticate_request", _deny)
    client = TestClient(app)
    response = client.get("/admin/data", headers={"Authorization": "Bearer token"})
    assert response.status_code == 403


def test_admin_data_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.admin.authenticate_request",
        lambda _auth, path, require_admin=False, request=None: AuthorizedUser(email="admin@example.com", uid="admin1"),
    )
    monkeypatch.setattr("app.routes.admin.list_users", lambda limit=500: [{"uid": "u1", "email": "user@example.com"}])
    monkeypatch.setattr("app.routes.admin.list_organizations", lambda: [])
    monkeypatch.setattr(
        "app.routes.admin.get_billing_pricing_global",
        lambda: {"google_usd_per_classified_document": 0.75, "margin_per_document_usd": 0.05},
    )
    monkeypatch.setattr("app.routes.admin.list_signup_requests", lambda status=None, limit=500: [])
    monkeypatch.setattr(
        "app.routes.admin.get_admin_usage_summary",
        lambda month=None, limit=2000: {"by_user": [], "by_org": [], "totals": {"total_pages": 0, "total_billable": 0.0, "event_count": 0}},
    )
    monkeypatch.setattr("app.routes.admin.list_app_errors", lambda limit=500: [])
    monkeypatch.setattr(
        "app.routes.admin.get_reconciliation_history",
        lambda days=35: {
            "summary": {
                "app_estimate_mtd": 0.0,
                "financial_truth_mtd": None,
                "variance_amount_mtd": None,
                "variance_pct_mtd": None,
                "status": "awaiting_data",
                "last_reconciled_at": None,
            },
            "rows": [],
        },
    )

    client = TestClient(app)
    response = client.get("/admin/data", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    payload = response.json()
    assert "users" in payload
    assert "report" in payload
    assert "reconciliation" in payload


def test_admin_split_endpoints_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.admin.authenticate_request",
        lambda _auth, path, require_admin=False, request=None: AuthorizedUser(email="admin@example.com", uid="admin1"),
    )
    monkeypatch.setattr("app.routes.admin.list_organizations", lambda: [])
    monkeypatch.setattr(
        "app.routes.admin.get_billing_pricing_global",
        lambda: {"google_usd_per_classified_document": 0.75, "margin_per_document_usd": 0.05},
    )
    monkeypatch.setattr(
        "app.routes.admin.get_reconciliation_history",
        lambda days=35: {"summary": {"status": "ok"}, "rows": []},
    )
    monkeypatch.setattr("app.routes.admin.list_users", lambda limit=500: [{"uid": "u1", "email": "user@example.com"}])
    monkeypatch.setattr("app.routes.admin.list_signup_requests", lambda status=None, limit=500: [])
    monkeypatch.setattr("app.routes.admin.list_app_errors", lambda limit=500: [])
    monkeypatch.setattr(
        "app.routes.admin.get_admin_usage_summary",
        lambda month=None, limit=2000: {"by_user": [{"uid": "u1"}], "by_org": [], "totals": {"event_count": 1}},
    )

    client = TestClient(app)
    assert client.get("/admin/overview", headers={"Authorization": "Bearer token"}).status_code == 200
    assert client.get("/admin/users", headers={"Authorization": "Bearer token"}).status_code == 200
    assert client.get("/admin/requests", headers={"Authorization": "Bearer token"}).status_code == 200
    assert client.get("/admin/errors", headers={"Authorization": "Bearer token"}).status_code == 200
    assert client.get("/admin/usage/summary", headers={"Authorization": "Bearer token"}).status_code == 200


def test_admin_users_include_org_name(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.admin.authenticate_request",
        lambda _auth, path, require_admin=False, request=None: AuthorizedUser(email="admin@example.com", uid="admin-org-name"),
    )
    monkeypatch.setattr(
        "app.routes.admin.list_users",
        lambda limit=500: [{"uid": "u1", "email": "user@example.com", "org_id": "o1"}],
    )
    monkeypatch.setattr(
        "app.routes.admin.list_organizations",
        lambda: [{"org_id": "o1", "name": "Swan Computing"}],
    )

    client = TestClient(app)
    response = client.get("/admin/users", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["org_name"] == "Swan Computing"


def test_admin_approve_request_auto_creates_firebase_user(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.admin.authenticate_request_with_mode",
        lambda _auth, request, path, require_admin=True: AuthResult(
            user=AuthorizedUser(email="admin@example.com", uid="admin1"),
            auth_mode="bearer",
        ),
    )
    monkeypatch.setattr(
        "app.routes.admin.get_signup_request",
        lambda request_id: {"request_id": request_id, "email": "test@test.com"},
    )
    monkeypatch.setattr(
        "app.routes.admin.create_or_get_user_by_email",
        lambda email: {"uid": "firebase_uid_123", "email": email},
    )
    monkeypatch.setattr(
        "app.routes.admin.generate_password_setup_link",
        lambda _email: "https://example.com/reset",
    )
    monkeypatch.setattr("app.routes.admin.approve_signup_request", lambda *args, **kwargs: None)

    client = TestClient(app)
    response = client.post(
        "/admin/requests/r1/approve",
        headers={"Authorization": "Bearer token"},
        json={},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["uid"] == "firebase_uid_123"
    assert "password_setup_link" in payload


def test_admin_reconciliation_get_and_run(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.admin.authenticate_request",
        lambda _auth, path, require_admin=False, request=None: AuthorizedUser(email="admin@example.com", uid="admin1"),
    )
    monkeypatch.setattr(
        "app.routes.admin.authenticate_request_with_mode",
        lambda _auth, request, path, require_admin=True: AuthResult(
            user=AuthorizedUser(email="admin@example.com", uid="admin1"),
            auth_mode="bearer",
        ),
    )
    monkeypatch.setattr(
        "app.routes.admin.get_reconciliation_history",
        lambda days=35: {"summary": {"status": "ok"}, "rows": [{"day": "2026-04-08"}]},
    )
    monkeypatch.setattr(
        "app.routes.admin.run_reconciliation",
        lambda days=35, persist=True: {"summary": {"status": "ok"}, "rows": [{"day": "2026-04-08"}]},
    )

    client = TestClient(app)
    response_get = client.get("/admin/reconciliation", headers={"Authorization": "Bearer token"})
    assert response_get.status_code == 200
    assert response_get.json()["summary"]["status"] == "ok"

    response_run = client.post("/admin/reconciliation/run", headers={"Authorization": "Bearer token"})
    assert response_run.status_code == 200
    assert response_run.json()["summary"]["status"] == "ok"


def test_admin_billing_pricing_put_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.admin.authenticate_request_with_mode",
        lambda _auth, request, path, require_admin=True: AuthResult(
            user=AuthorizedUser(email="admin@example.com", uid="admin1"),
            auth_mode="bearer",
        ),
    )
    monkeypatch.setattr(
        "app.routes.admin.set_billing_pricing_global",
        lambda _payload: {"google_usd_per_classified_document": 0.8, "margin_per_document_usd": 0.05},
    )
    monkeypatch.setattr("app.routes.admin.invalidate_prefix", lambda _p: None)

    client = TestClient(app)
    response = client.put(
        "/admin/billing-pricing",
        headers={"Authorization": "Bearer token"},
        json={"google_usd_per_classified_document": 0.8, "margin_per_document_usd": 0.05},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["billing_pricing"]["google_usd_per_classified_document"] == 0.8


def test_admin_mutation_requires_csrf_for_cookie_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.admin.authenticate_request_with_mode",
        lambda _auth, request, path, require_admin=True: AuthResult(
            user=AuthorizedUser(email="admin@example.com", uid="admin1"),
            auth_mode="session_cookie",
        ),
    )
    client = TestClient(app)
    response = client.post("/admin/orgs", json={"name": "Acme", "domains": []})
    assert response.status_code == 403
