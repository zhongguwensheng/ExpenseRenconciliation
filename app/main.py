from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.api import router as api_router
from app.routes.ui import router as ui_router


app = FastAPI(title="Expense Reconciliation", version="0.1.0")

app.include_router(ui_router)
app.include_router(api_router, prefix="/api")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

