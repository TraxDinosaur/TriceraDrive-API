from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SyncStatusResponse(BaseModel):
    is_running: bool
    last_run_at: Optional[datetime] = None
    accounts: list[SyncAccountStatus]


class SyncAccountStatus(BaseModel):
    account_id: str
    account_label: str
    status: str
    last_sync_at: Optional[datetime] = None
    error_message: Optional[str] = None


class SyncTriggerResponse(BaseModel):
    message: str
    job_id: Optional[str] = None
