from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.database import AsyncStorage, get_storage
from app.schemas.sync import SyncStatusResponse, SyncTriggerResponse
from app.services.sync import SyncService

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/run", response_model=SyncTriggerResponse)
async def run_sync(storage: AsyncStorage = Depends(get_storage)):
    service = SyncService(storage)
    if service.is_running:
        return SyncTriggerResponse(message="Sync already running")
    jobs = await service.sync_all()
    return SyncTriggerResponse(message=f"Synced {len(jobs)} accounts")


@router.get("/status", response_model=SyncStatusResponse)
async def get_sync_status(storage: AsyncStorage = Depends(get_storage)):
    service = SyncService(storage)
    status = await service.get_status()
    return SyncStatusResponse(**status)


@router.post("/account/{account_id}", response_model=SyncTriggerResponse)
async def sync_account(account_id: str, storage: AsyncStorage = Depends(get_storage)):
    service = SyncService(storage)
    job = await service.sync_account(account_id)
    return SyncTriggerResponse(message=f"Sync completed with status: {job['status']}", job_id=job["id"])


@router.post("/all", response_model=SyncTriggerResponse)
async def sync_all(storage: AsyncStorage = Depends(get_storage)):
    service = SyncService(storage)
    if service.is_running:
        return SyncTriggerResponse(message="Sync already running")
    jobs = await service.sync_all()
    return SyncTriggerResponse(message=f"Synced {len(jobs)} accounts")
