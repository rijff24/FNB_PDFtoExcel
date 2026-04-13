from fastapi import APIRouter, Body, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.services.access_control import authenticate_request_with_mode
from app.services.auth import (
    AuthError,
    create_session_cookie_from_id_token,
    csrf_cookie_name,
    csrf_cookie_settings,
    generate_csrf_token,
    session_cookie_name,
    session_cookie_settings,
    verify_id_token_and_allowlist,
)

router = APIRouter()


@router.post("/auth/session")
async def create_auth_session(payload: dict = Body(...)) -> JSONResponse:
    id_token = str(payload.get("id_token") or "").strip()
    try:
        user = verify_id_token_and_allowlist(id_token)
        session_cookie, max_age = create_session_cookie_from_id_token(id_token)
        csrf_token = generate_csrf_token()
    except AuthError as exc:
        msg = str(exc)
        status_code = 403 if ("allowlist" in msg.lower() or "admin" in msg.lower()) else 401
        raise HTTPException(status_code=status_code, detail=msg) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    body = {"ok": True, "email": user.email, "uid": user.uid}
    response = JSONResponse(body)

    session_settings = session_cookie_settings()
    response.set_cookie(value=session_cookie, **session_settings)

    csrf_settings = csrf_cookie_settings()
    response.set_cookie(value=csrf_token, **csrf_settings)

    # Keep response hints explicit for clients that display session metadata.
    response.headers["X-Session-Max-Age"] = str(max_age)
    return response


@router.delete("/auth/session")
async def delete_auth_session() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(session_cookie_name(), path="/")
    response.delete_cookie(csrf_cookie_name(), path="/")
    return response


@router.get("/auth/me")
async def auth_me(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    auth_result = authenticate_request_with_mode(
        authorization,
        request=request,
        path="/auth/me",
        require_admin=False,
    )
    return JSONResponse(
        {
            "authenticated": True,
            "email": auth_result.user.email,
            "uid": auth_result.user.uid,
            "auth_mode": auth_result.auth_mode,
        }
    )
