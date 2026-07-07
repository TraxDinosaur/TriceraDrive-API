from __future__ import annotations

from typing import Optional

from app.database import AsyncStorage


class FileService:

    def __init__(self, storage: AsyncStorage):
        self.storage = storage

    async def list_files(self, params) -> list[dict]:
        return await self.storage.list_files(params)

    async def get_file(self, file_id: str) -> Optional[dict]:
        return await self.storage.get_file(file_id)

    async def search_files(self, q: str, limit: int = 50) -> list[dict]:
        return await self.storage.search_files(q, limit)

    async def get_active_account_ids(self) -> list[str]:
        accounts = await self.storage.list_accounts()
        return [a["id"] for a in accounts if a.get("status") == "active"]
