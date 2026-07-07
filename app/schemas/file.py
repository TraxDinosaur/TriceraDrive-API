from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FileResponse(BaseModel):
    id: str
    account_id: str
    name: str
    extension: Optional[str] = None
    size: int
    hash: Optional[str] = None
    folder_path: str
    remote_path: str
    mime_type: Optional[str] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    is_deleted: bool
    is_favorite: bool

    model_config = {"from_attributes": True}


class FileListParams(BaseModel):
    account_id: Optional[str] = None
    folder_path: Optional[str] = None
    search: Optional[str] = None
    file_type: Optional[str] = None
    file_types: Optional[str] = None
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    include_deleted: bool = False
    sort_by: str = "name"
    sort_order: str = "asc"
    limit: int = 100
    offset: int = 0


class FileSearchResult(BaseModel):
    id: str
    account_id: str
    name: str
    extension: Optional[str] = None
    size: int
    folder_path: str
    account_email: str
    account_label: str
