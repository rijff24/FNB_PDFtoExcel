import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from app.services.admin_store import ensure_user_is_active
from app.services.auth import (
    AuthError,
    AuthorizedUser,
    require_admin_user,
    session_cookie_name,
    verify_bearer_token_and_allowlist,
    verify_session_cookie_and_allowlist,
)
from app.services.logging_utils import log_event


@dataclass(frozen=True)
class AuthResult:
    user: AuthorizedUser
    auth_mode: str


def authenticate_request_with_mode(
    authorization: str | None = Header(default=None),
    *,
    request: Request | None = None,
    path: str,
    require_admin: bool = False,
) -> AuthResult:
    try:
        auth_mode = "bearer"
        if authorization:
            user = verify_bearer_token_and_allowlist(authorization)
        else:
            auth_mode = "session_cookie"
            cookie_name = session_cookie_name()
            session_cookie = request.cookies.get(cookie_name) if request else None
            user = verify_session_cookie_and_allowlist(session_cookie)

        ensure_user_is_active(user.uid, email=user.email)
        if require_admin:
            require_admin_user(user)
        log_event(
            logging.INFO,
            "auth_method_selected",
            path=path,
            user_email=user.email,
            uid=user.uid,
            extra={"auth_method": auth_mode},
        )
        return AuthResult(user=user, auth_mode=auth_mode)
    except AuthError as exc:
        msg = str(exc)
        status_code = 403 if ("allowlist" in msg.lower() or "admin" in msg.lower()) else 401
        log_event(logging.ERROR, "auth_failed_request", path=path, details=msg)
        raise HTTPException(status_code=status_code, detail=msg) from exc
    except RuntimeError as exc:
        msg = str(exc)
        status_code = 403 if "revoked" in msg.lower() or "pending" in msg.lower() else 500
        event = "account_access_denied" if status_code == 403 else "auth_misconfigured"
        log_event(logging.ERROR, event, path=path, details=msg)
        raise HTTPException(status_code=status_code, detail=msg) from exc


def authenticate_request(
    authorization: str | None = Header(default=None),
    *,
    request: Request | None = None,
    path: str,
    require_admin: bool = False,
) -> AuthorizedUser:
    return authenticate_request_with_mode(
        authorization,
        request=request,
        path=path,
        require_admin=require_admin,
    ).user
