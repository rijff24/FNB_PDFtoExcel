from fastapi import HTTPException

from app.services.access_control import authenticate_request_with_mode
from app.services.auth import AuthorizedUser


def test_authenticate_request_prefers_bearer(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.access_control.verify_bearer_token_and_allowlist",
        lambda _auth: AuthorizedUser(email="bearer@example.com", uid="u1"),
    )
    monkeypatch.setattr(
        "app.services.access_control.verify_session_cookie_and_allowlist",
        lambda _cookie: AuthorizedUser(email="cookie@example.com", uid="u2"),
    )
    monkeypatch.setattr("app.services.access_control.ensure_user_is_active", lambda _uid, email=None: None)

    result = authenticate_request_with_mode(
        "Bearer abc",
        request=None,
        path="/test",
    )
    assert result.user.email == "bearer@example.com"
    assert result.auth_mode == "bearer"


def test_authenticate_request_cookie_fallback(monkeypatch) -> None:
    class _Request:
        cookies = {"session": "cookie-value"}

    monkeypatch.setattr(
        "app.services.access_control.verify_session_cookie_and_allowlist",
        lambda _cookie: AuthorizedUser(email="cookie@example.com", uid="u2"),
    )
    monkeypatch.setattr("app.services.access_control.ensure_user_is_active", lambda _uid, email=None: None)

    result = authenticate_request_with_mode(
        None,
        request=_Request(),
        path="/test",
    )
    assert result.user.email == "cookie@example.com"
    assert result.auth_mode == "session_cookie"


def test_authenticate_request_maps_auth_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.access_control.verify_bearer_token_and_allowlist",
        lambda _auth: (_ for _ in ()).throw(RuntimeError("bad config")),
    )
    try:
        authenticate_request_with_mode("Bearer abc", request=None, path="/test")
    except HTTPException as exc:
        assert exc.status_code == 500
    else:
        raise AssertionError("Expected HTTPException")
