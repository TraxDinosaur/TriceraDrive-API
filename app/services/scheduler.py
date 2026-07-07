from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.database import get_storage
from app.services.sync import SyncService

logger = logging.getLogger(__name__)

_sync_task: asyncio.Task | None = None


async def start_sync_scheduler():
    global _sync_task
    _sync_task = asyncio.create_task(_run_sync_loop())
    logger.info("Sync scheduler started")


async def stop_sync_scheduler():
    global _sync_task
    if _sync_task:
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        _sync_task = None
    logger.info("Sync scheduler stopped")


async def _run_sync_loop():
    while True:
        try:
            interval = get_settings().sync_interval_seconds
            await asyncio.sleep(interval)
            storage = await get_storage()
            service = SyncService(storage)
            if not service.is_running:
                logger.info("Running scheduled sync for all accounts")
                await service.sync_all()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Scheduled sync failed")
