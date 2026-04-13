import os
from typing import Any


def _app():
    import firebase_admin
    from firebase_admin import credentials

    if not firebase_admin._apps:
        project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip() or None
        firebase_admin.initialize_app(credentials.ApplicationDefault(), {"projectId": project_id})
    return firebase_admin.get_app()


def get_firebase_admin_auth():
    from firebase_admin import auth

    _app()
    return auth


def create_or_get_user_by_email(email: str) -> dict[str, Any]:
    auth = get_firebase_admin_auth()
    cleaned = email.strip().lower()
    try:
        user = auth.get_user_by_email(cleaned)
    except auth.UserNotFoundError:
        user = auth.create_user(email=cleaned, email_verified=False, disabled=False)
    return {"uid": user.uid, "email": user.email}


def generate_password_setup_link(email: str) -> str:
    auth = get_firebase_admin_auth()
    cleaned = email.strip().lower()
    return auth.generate_password_reset_link(cleaned)
