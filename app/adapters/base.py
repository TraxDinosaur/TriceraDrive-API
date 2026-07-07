from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class RemoteFile:
    name: str
    size: int
    remote_path: str
    folder_path: str
    hash: Optional[str] = None
    mime_type: Optional[str] = None
    created_at: Optional[str] = None
    modified_at: Optional[str] = None
    is_folder: bool = False


@dataclass
class QuotaInfo:
    used: int
    total: int


class StorageAdapter(ABC):

    @abstractmethod
    async def test_connection(self) -> bool:
        ...

    @abstractmethod
    async def list_files(self, folder_path: str = "/") -> list[RemoteFile]:
        ...

    @abstractmethod
    async def upload_file(self, local_path: str, remote_path: str) -> RemoteFile:
        ...

    @abstractmethod
    async def download_file(self, remote_path: str, local_path: str) -> str:
        ...

    @abstractmethod
    async def download_file_by_id(self, node_id: str, dest_dir: str = "") -> str:
        ...

    @abstractmethod
    async def delete_file(self, remote_path: str) -> bool:
        ...

    @abstractmethod
    async def rename_file(self, old_path: str, new_path: str) -> bool:
        ...

    @abstractmethod
    async def get_quota(self) -> QuotaInfo:
        ...

    @abstractmethod
    async def authenticate(self, email: str, password: str) -> str:
        ...

    @abstractmethod
    async def restore_session(self, session_data: str) -> bool:
        ...

    @abstractmethod
    async def close(self):
        ...
