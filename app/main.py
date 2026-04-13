from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.routes.billing import router as billing_router
from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.register import router as register_router
from app.routes.upload import router as upload_router
from app.services.logging_utils import logger  # noqa: F401  - ensure logger is configured

app = FastAPI(title="FNB PDF to Excel")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Disable Jinja template caching to avoid a known cache-key issue in some Jinja versions.
templates_dir = Path(__file__).resolve().parent / "templates"
env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True, cache_size=0)
app.state.templates = Jinja2Templates(env=env)

app.include_router(upload_router)
app.include_router(billing_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(register_router)
