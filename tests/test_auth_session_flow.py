from fastapi.testclient import TestClient

from app.main import app
from app.services.access_control import AuthResult
from app.services.auth import AuthError, AuthorizedUser


def test_create_auth_session_sets_cookies(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.auth.verify_id_token_and_allowlist",
        lambda _id_token: AuthorizedUser(email="user@example.com", uid="u1"),
    )
    monkeypatch.setattr(
        "app.routes.auth.create_session_cookie_from_id_token",
        lambda _id_token: ("session-cookie-value", 3600),
    )
    monkeypatch.setattr("app.routes.auth.generate_csrf_token", lambda: "csrf-value")

    client = TestClient(app)
    response = client.post("/auth/session", json={"id_token": "abc"})
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "session=session-cookie-value" in set_cookie
    assert "csrf_token=csrf-value" in set_cookie


def test_create_auth_session_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.auth.verify_id_token_and_allowlist",
        lambda _id_token: (_ for _ in ()).throw(AuthError("Invalid Firebase ID token.")),
    )
    client = TestClient(app)
    response = client.post("/auth/session", json={"id_token": "bad"})
    assert response.status_code == 401


def test_delete_auth_session_clears_cookies() -> None:
    client = TestClient(app)
    response = client.delete("/auth/session")
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "session=" in set_cookie
    assert "csrf_token=" in set_cookie


def test_auth_me_supports_cookie_or_bearer(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.auth.authenticate_request_with_mode",
        lambda _auth, request, path, require_admin=False: AuthResult(
            user=AuthorizedUser(email="user@example.com", uid="u1"),
            auth_mode="session_cookie",
        ),
    )
    client = TestClient(app)
    response = client.get("/auth/me")
    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["auth_mode"] == "session_cookie"
