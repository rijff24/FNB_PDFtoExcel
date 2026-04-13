import logging
import os
import secrets
import time
from datetime import timedelta
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import jwt
import requests
from jwt import InvalidTokenError
from jwt.exceptions import InvalidKeyError, PyJWTError

from app.services.logging_utils import log_event
from app.services.firebase_auth_admin import get_firebase_admin_auth


_CERTS_CACHE_TTL_SECONDS = 60 * 60  # 1 hour
_certs_cache: dict[str, Any] = {"fetched_at": 0.0, "certs": {}}
_public_keys_cache: dict[str, Any] = {"fetched_at": 0.0, "keys": {}}


def _cert_pem_to_public_key_pem(cert_pem: str) -> str:
    """
    Convert an X.509 PEM certificate to a PEM public key string.

    PyJWT expects a public key, not a certificate in some configurations.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    public_key = cert.public_key()
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


@dataclass(frozen=True)
class AuthorizedUser:
    email: str
    uid: str


class AuthError(RuntimeError):
    """Raised when auth fails (mapped by route)."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _session_cookie_name() -> str:
    return os.getenv("SESSION_COOKIE_NAME", "session").strip() or "session"


def _csrf_cookie_name() -> str:
    return os.getenv("CSRF_COOKIE_NAME", "csrf_token").strip() or "csrf_token"


def _session_cookie_max_age_seconds() -> int:
    raw = os.getenv("SESSION_COOKIE_MAX_AGE_SECONDS", "").strip()
    if not raw:
        return 60 * 60 * 24 * 5
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise RuntimeError("SESSION_COOKIE_MAX_AGE_SECONDS must be an integer.") from exc
    if parsed <= 0:
        raise RuntimeError("SESSION_COOKIE_MAX_AGE_SECONDS must be > 0.")
    return parsed


def _session_cookie_secure() -> bool:
    raw = os.getenv("SESSION_COOKIE_SECURE", "").strip()
    if raw:
        return _is_truthy(raw)
    app_env = os.getenv("APP_ENV", "").strip().lower()
    return app_env in {"prod", "production", "staging"}


def _session_cookie_samesite() -> str:
    value = os.getenv("SESSION_COOKIE_SAMESITE", "lax").strip().lower()
    if value not in {"lax", "strict", "none"}:
        raise RuntimeError("SESSION_COOKIE_SAMESITE must be one of: lax, strict, none.")
    return value


def session_cookie_settings() -> dict[str, Any]:
    max_age = _session_cookie_max_age_seconds()
    return {
        "key": _session_cookie_name(),
        "httponly": True,
        "secure": _session_cookie_secure(),
        "samesite": _session_cookie_samesite(),
        "max_age": max_age,
        "expires": max_age,
        "path": "/",
    }


def csrf_cookie_settings() -> dict[str, Any]:
    max_age = _session_cookie_max_age_seconds()
    return {
        "key": _csrf_cookie_name(),
        "httponly": False,
        "secure": _session_cookie_secure(),
        "samesite": _session_cookie_samesite(),
        "max_age": max_age,
        "expires": max_age,
        "path": "/",
    }


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _parse_authorization_header(authorization: Optional[str]) -> str:
    if not authorization:
        raise AuthError("Missing Authorization header.")
    if not authorization.lower().startswith("bearer "):
        raise AuthError("Authorization must be a Bearer token.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise AuthError("Bearer token is empty.")
    return token


def _get_allowed_emails() -> set[str]:
    allowed_raw = os.getenv("ALLOWED_USER_EMAILS", "").strip()
    if not allowed_raw:
        # Fail closed: no allowlist configured means nobody is allowed.
        raise RuntimeError("Missing required environment variable: ALLOWED_USER_EMAILS")
    return {email.strip().lower() for email in allowed_raw.split(",") if email.strip()}


def _get_admin_emails() -> set[str]:
    admins_raw = os.getenv("ADMIN_EMAILS", "").strip()
    if not admins_raw:
        return set()
    return {email.strip().lower() for email in admins_raw.split(",") if email.strip()}


def _fetch_firebase_securetoken_certs() -> Dict[str, str]:
    """
    Fetch Firebase public certs used to verify securetoken JWT signatures.

    Returns:
      Mapping of kid -> PEM certificate string.
    """

    now = time.time()
    if now - float(_certs_cache["fetched_at"]) < _CERTS_CACHE_TTL_SECONDS:
        return dict(_certs_cache["certs"])

    url = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    certs: Dict[str, str] = {}
    # The endpoint may return either:
    # 1) {"keys":[{"keyId":"...","x509Cert":"..."}]} or
    # 2) {"<kid>":"-----BEGIN CERTIFICATE-----..."}
    if isinstance(payload, dict) and isinstance(payload.get("keys"), list):
        for key in payload.get("keys", []):
            kid = key.get("keyId")
            cert = key.get("x509Cert")
            if kid and cert:
                certs[str(kid)] = cert
    else:
        # Assume it's already a kid -> certificate mapping.
        for kid, cert in (payload or {}).items():
            if isinstance(cert, str) and cert.startswith("-----BEGIN"):
                certs[str(kid)] = cert

    _certs_cache["fetched_at"] = now
    _certs_cache["certs"] = certs
    # Certs rotated -> clear derived public-key cache.
    _public_keys_cache["fetched_at"] = now
    _public_keys_cache["keys"] = {}
    return certs


def _verify_firebase_id_token(token: str) -> Dict[str, Any]:
    firebase_project_id = _require_env("FIREBASE_PROJECT_ID")
    issuer = f"https://securetoken.google.com/{firebase_project_id}"

    # Read header without verification to select correct cert.
    # Malformed tokens should be treated as auth failures (401), not 500s.
    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001 - treat any parse failures as auth failures
        log_event(
            logging.ERROR,
            "auth_failed_malformed_token",
            details=str(exc),
        )
        raise AuthError("Malformed Firebase ID token.") from exc

    kid = unverified_header.get("kid")
    if not kid:
        log_event(logging.ERROR, "auth_failed_missing_kid")
        raise AuthError("JWT header missing kid.")

    certs = _fetch_firebase_securetoken_certs()

    # Prefer the cert by `kid`, but if it isn't found (or verification fails due
    # to rotation/caching edge-cases), fall back to trying all known certs.
    # This avoids false negatives like "Unable to find matching certificate".
    cert_pems_to_try: list[str] = []
    preferred = certs.get(str(kid))
    if preferred:
        cert_pems_to_try.append(preferred)
    cert_pems_to_try.extend([pem for pem in certs.values() if pem not in cert_pems_to_try])

    last_exc: Exception | None = None
    for public_key in cert_pems_to_try:
        try:
            # Verify signature + standard JWT claims (exp, nbf, etc.),
            # but do audience verification ourselves to avoid Firebase token
            # edge-cases around `aud`.
            # PyJWT expects a public key PEM; the securetoken endpoint returns
            # X.509 cert PEMs.
            cache_key = public_key
            public_pem = _public_keys_cache["keys"].get(cache_key)
            if not public_pem:
                public_pem = _cert_pem_to_public_key_pem(public_key)
                _public_keys_cache["keys"][cache_key] = public_pem

            claims = jwt.decode(
                token,
                key=public_pem,
                algorithms=["RS256"],
                options={"verify_aud": False, "verify_iss": False},
            )

            iss_claim = str(claims.get("iss") or "").strip()
            if iss_claim and iss_claim != issuer:
                log_event(
                    logging.ERROR,
                    "auth_failed_invalid_issuer",
                    details=f"iss={iss_claim} expected={issuer}",
                )
                raise AuthError("Invalid token issuer.")

            return claims
        except (InvalidTokenError, InvalidKeyError) as exc:
            last_exc = exc
        except AuthError as exc:
            # Issuer mismatch should be treated as auth failure.
            raise exc

    # Any unexpected decode/key errors should still map to auth failures.
    if isinstance(last_exc, PyJWTError):
        log_event(logging.ERROR, "auth_failed_invalid_token", details=str(last_exc))
        raise AuthError("Invalid Firebase ID token.") from last_exc
    log_event(logging.ERROR, "auth_failed_invalid_token", details=str(last_exc) if last_exc else None)
    raise AuthError("Invalid Firebase ID token.") from (last_exc or None)


def create_session_cookie_from_id_token(id_token: str) -> tuple[str, int]:
    if not id_token:
        raise AuthError("Firebase ID token is required.")
    auth = get_firebase_admin_auth()
    max_age_seconds = _session_cookie_max_age_seconds()
    expires_in = timedelta(seconds=max_age_seconds)
    try:
        cookie = auth.create_session_cookie(id_token, expires_in=expires_in)
    except Exception as exc:  # noqa: BLE001
        log_event(logging.ERROR, "auth_failed_create_session_cookie", details=str(exc))
        raise AuthError("Unable to create Firebase session cookie.") from exc
    return cookie, max_age_seconds


def _authorize_claims_and_allowlist(claims: Dict[str, Any], *, auth_method: str) -> AuthorizedUser:
    email = str(claims.get("email") or "").strip().lower()
    uid = str(claims.get("uid") or claims.get("user_id") or claims.get("sub") or "").strip()
    if not email:
        log_event(logging.ERROR, "auth_failed_missing_email_claim", extra={"auth_method": auth_method})
        raise AuthError("Firebase token missing email claim.")
    if not uid:
        log_event(
            logging.ERROR,
            "auth_failed_missing_uid_claim",
            user_email=email,
            extra={"auth_method": auth_method},
        )
        raise AuthError("Firebase token missing user id claim.")

    allowed = _get_allowed_emails()
    if email not in allowed:
        # Manual-approval flow: allow users that exist in our user profile store
        # and are marked active, even if they are not in static env allowlist.
        profile_status = None
        try:
            from app.services.admin_store import get_user_profile_if_exists

            profile = get_user_profile_if_exists(uid)
            profile_status = str((profile or {}).get("status") or "").strip().lower() if profile else None
        except Exception:  # noqa: BLE001
            profile_status = None

        if profile_status != "active":
            log_event(
                logging.ERROR,
                "auth_failed_allowlist",
                user_email=email,
                uid=uid,
                extra={
                    "allowed_emails": sorted(allowed),
                    "profile_status": profile_status,
                    "auth_method": auth_method,
                },
            )
            raise AuthError("User is not allowlisted.")

    log_event(logging.INFO, "auth_success", user_email=email, uid=uid, extra={"auth_method": auth_method})
    return AuthorizedUser(email=email, uid=uid)


def verify_bearer_token_and_allowlist(authorization: Optional[str]) -> AuthorizedUser:
    """
    Verifies Firebase ID token (no-key) and enforces email allowlist.

    Expected:
      Authorization: Bearer <firebase_id_token>
    """

    token = _parse_authorization_header(authorization)
    claims = _verify_firebase_id_token(token)
    return _authorize_claims_and_allowlist(claims, auth_method="bearer")


def verify_id_token_and_allowlist(id_token: str) -> AuthorizedUser:
    claims = _verify_firebase_id_token(id_token)
    return _authorize_claims_and_allowlist(claims, auth_method="id_token")


def verify_session_cookie_and_allowlist(session_cookie: Optional[str]) -> AuthorizedUser:
    if not session_cookie:
        log_event(logging.ERROR, "auth_failed_missing_session_cookie")
        raise AuthError("Missing session cookie.")
    auth = get_firebase_admin_auth()
    try:
        claims = auth.verify_session_cookie(session_cookie, check_revoked=True)
    except Exception as exc:  # noqa: BLE001
        log_event(logging.ERROR, "auth_failed_invalid_session_cookie", details=str(exc))
        raise AuthError("Invalid Firebase session cookie.") from exc
    return _authorize_claims_and_allowlist(dict(claims or {}), auth_method="session_cookie")


def session_cookie_name() -> str:
    return _session_cookie_name()


def csrf_cookie_name() -> str:
    return _csrf_cookie_name()


def require_admin_user(user: AuthorizedUser) -> None:
    admins = _get_admin_emails()
    if user.email.lower() not in admins:
        raise AuthError("Admin access required.")


def is_admin_user(user: AuthorizedUser) -> bool:
    admins = _get_admin_emails()
    return user.email.lower() in admins

