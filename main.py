from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import init_storage
from app.routers import accounts, auth, files, settings, sync
from app.services.scheduler import start_sync_scheduler, stop_sync_scheduler
from app.utils.logging import setup_logging, RequestLogMiddleware

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_storage()
    await start_sync_scheduler()
    yield
    await stop_sync_scheduler()


app = FastAPI(
    title=get_settings().app_name,
    lifespan=lifespan,
)

app.add_middleware(RequestLogMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(files.router)
app.include_router(sync.router)
app.include_router(settings.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", "???")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error", "request_id": req_id},
    )
