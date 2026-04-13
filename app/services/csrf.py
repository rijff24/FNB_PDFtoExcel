from fastapi import HTTPException, Request

from app.services.auth import csrf_cookie_name


def should_enforce_csrf(request: Request, auth_mode: str) -> bool:
    if auth_mode != "session_cookie":
        return False
    return request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}


def validate_double_submit_csrf(request: Request) -> None:
    cookie_name = csrf_cookie_name()
    cookie_token = request.cookies.get(cookie_name)
    header_token = request.headers.get("X-CSRF-Token")
    if not cookie_token or not header_token or cookie_token != header_token:
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
