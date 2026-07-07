from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AccountCreate(BaseModel):
    label: str
    email: str
    password: str
    auth_type: str = "password"
    mfa_code: Optional[str] = None


class MFARequiredResponse(BaseModel):
    mfa_required: bool = True
    detail: str = "2FA code required for this account"


class AccountUpdate(BaseModel):
    label: Optional[str] = None
    email: Optional[str] = None


class AccountResponse(BaseModel):
    id: str
    label: str
    email: str
    auth_type: str
    storage_used: int
    storage_total: int
    is_active_upload_target: bool
    status: str
    last_sync_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AccountTestResult(BaseModel):
    success: bool
    message: str
    storage_used: Optional[int] = None
    storage_total: Optional[int] = None
