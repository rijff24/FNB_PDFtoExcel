from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.services.admin_store import create_signup_request

router = APIRouter()


@router.get("/register")
async def register_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "register.html", {"request": request})


@router.post("/register/request")
async def register_request(payload: dict) -> JSONResponse:
    email = str(payload.get("email") or "").strip().lower()
    requested_name = str(payload.get("requested_name") or "").strip()
    organization = str(payload.get("organization") or "").strip()
    how_heard_about_us = str(payload.get("how_heard_about_us") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required.")
    if not organization:
        raise HTTPException(status_code=400, detail="Organization is required.")
    row = create_signup_request(
        email=email,
        requested_name=requested_name,
        requested_organization=organization,
        how_heard_about_us=how_heard_about_us,
    )
    return JSONResponse(
        {
            "ok": True,
            "request_id": row["request_id"],
            "status": row["status"],
            "suggested_org_id": row.get("suggested_org_id"),
        }
    )
