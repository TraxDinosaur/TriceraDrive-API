from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone

from app.database import AsyncStorage
from app.adapters.mega import MEGAAdapter
from app.services.account import AccountService
from app.utils.encryption import decrypt_value

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {"txt", "md", "json", "csv", "log", "xml", "yaml", "yml", "ini", "cfg", "conf", "env", "toml"}
MAX_INDEX_SIZE = 1 * 1024 * 1024  # 1MB


class SyncService:

    def __init__(self, storage: AsyncStorage):
        self.storage = storage
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def _index_text_files(self, account_id: str, adapter: MEGAAdapter):
        await self.storage.remove_all_content_indexes(account_id)
        fs = await adapter.get_filesystem()
        for node in fs:
            if node.type.value != 0:
                continue
            name = node.attributes.name if node.attributes else ""
            ext = name.split(".")[-1].lower() if "." in name else ""
            if ext not in TEXT_EXTENSIONS:
                continue
            size = int(node.size) if node.size else 0
            if size > MAX_INDEX_SIZE or size == 0:
                continue
            try:
                tmp = tempfile.mkdtemp()
                local = await adapter.download_file_by_id(node.id, tmp)
                with open(local, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if content.strip():
                    await self.storage.index_file_content(node.id, account_id, content)
            except Exception:
                logger.debug("Skipped content index for %s", name)
            finally:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)

    async def sync_account(self, account_id: str) -> dict:
        await self.storage.set_sync_status(account_id, {
            "id": account_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

        adapter = None
        job = {"id": account_id, "status": "running"}
        try:
            account_raw = await self.storage.get_account(account_id)
            if not account_raw:
                job["status"] = "failed"
                job["error_message"] = "Account not found"
                await self.storage.set_sync_status(account_id, job)
                return job

            adapter = MEGAAdapter(email=account_raw["email"])
            if account_raw.get("encrypted_session"):
                session = decrypt_value(account_raw["encrypted_session"])
                restored = await adapter.restore_session(session)
                if not restored:
                    job["status"] = "failed"
                    job["error_message"] = "Session expired"
                    await self.storage.update_account(account_id, {"status": "error"})
                    await self.storage.set_sync_status(account_id, job)
                    return job

            fs = await adapter.get_filesystem(force=True)

            await self.storage.save_filesystem(account_id, fs)
            await self.storage.replace_files_for_account(account_id, fs)

            await self._index_text_files(account_id, adapter)

            quota = await adapter.get_quota()
            await self.storage.update_account(account_id, {
                "storage_used": quota.used,
                "storage_total": quota.total,
                "status": "active",
                "last_sync_at": datetime.now(timezone.utc).isoformat(),
            })

            job["status"] = "completed"
            job["file_count"] = len([n for n in fs if n.type.value == 0])
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            await self.storage.set_sync_status(account_id, job)

        except Exception as e:
            logger.exception("Sync failed for account %s", account_id)
            job["status"] = "failed"
            job["error_message"] = str(e)
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            await self.storage.update_account(account_id, {"status": "error"})
            await self.storage.set_sync_status(account_id, job)
        finally:
            if adapter:
                await adapter.close()

        return job

    async def sync_all(self) -> list[dict]:
        self._is_running = True
        jobs = []
        try:
            accounts = await self.storage.list_accounts()
            for account in accounts:
                if account.get("status") == "disabled":
                    continue
                job = await self.sync_account(account["id"])
                jobs.append(job)
            return jobs
        finally:
            self._is_running = False

    async def get_status(self) -> dict:
        accounts = await self.storage.list_accounts()
        return {
            "is_running": self._is_running,
            "accounts": [
                {
                    "account_id": a["id"],
                    "account_label": a["label"],
                    "status": a.get("status", "unknown"),
                    "last_sync_at": a.get("last_sync_at"),
                    "error_message": None,
                }
                for a in accounts
            ],
        }
