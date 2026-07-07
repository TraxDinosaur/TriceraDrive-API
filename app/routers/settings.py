from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import get_settings
from app.database import AsyncStorage, get_storage

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    sync_interval_seconds: int | None = None
    max_retry_count: int | None = None
    upload_behavior: str | None = None
    debug: bool | None = None


@router.get("")
async def get_settings_endpoint():
    s = get_settings()
    return {
        "app_name": s.app_name,
        "debug": s.debug,
        "sync_interval_seconds": s.sync_interval_seconds,
        "max_retry_count": s.max_retry_count,
        "upload_behavior": "rename",
    }


@router.put("")
async def update_settings(body: SettingsUpdate, storage: AsyncStorage = Depends(get_storage)):
    if body.sync_interval_seconds is not None:
        await storage.set_setting("sync_interval_seconds", str(body.sync_interval_seconds))
    if body.max_retry_count is not None:
        await storage.set_setting("max_retry_count", str(body.max_retry_count))
    if body.upload_behavior is not None:
        await storage.set_setting("upload_behavior", body.upload_behavior)
    if body.debug is not None:
        await storage.set_setting("debug", str(body.debug))
    return await get_settings_endpoint()
