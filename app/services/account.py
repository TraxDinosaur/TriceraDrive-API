from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.database import AsyncStorage
from app.adapters.mega import MEGAAdapter
from app.exceptions import MFARequiredError
from app.utils.encryption import decrypt_value, encrypt_value


def _strip_sensitive(account: dict) -> dict:
    return {k: v for k, v in account.items() if k != "encrypted_session"}


class AccountService:

    def __init__(self, storage: AsyncStorage):
        self.storage = storage

    async def create_account(self, data) -> dict:
        adapter = MEGAAdapter(email=data.email, password=data.password)
        try:
            if data.mfa_code:
                session_token = await adapter.authenticate_with_mfa(data.email, data.password, data.mfa_code)
            else:
                session_token = await adapter.authenticate(data.email, data.password)

            quota = await adapter.get_quota()
            encrypted = encrypt_value(session_token)

            account = await self.storage.create_account({
                "label": data.label,
                "email": data.email,
                "auth_type": data.auth_type,
                "encrypted_session": encrypted,
                "storage_used": quota.used,
                "storage_total": quota.total,
            })
            return _strip_sensitive(account)
        except MFARequiredError:
            raise
        finally:
            await adapter.close()

    async def list_accounts(self) -> list[dict]:
        accounts = await self.storage.list_accounts()
        return [_strip_sensitive(a) for a in accounts]

    async def get_account(self, account_id: str) -> Optional[dict]:
        account = await self.storage.get_account(account_id)
        return _strip_sensitive(account) if account else None

    async def get_account_with_session(self, account_id: str) -> Optional[dict]:
        return await self.storage.get_account(account_id)

    async def _get_account_raw(self, account_id: str) -> Optional[dict]:
        return await self.storage.get_account(account_id)

    async def update_account(self, account_id: str, data) -> Optional[dict]:
        updates = data.model_dump(exclude_unset=True)
        if not updates:
            return await self.get_account(account_id)
        account = await self.storage.update_account(account_id, updates)
        return _strip_sensitive(account) if account else None

    async def delete_account(self, account_id: str) -> bool:
        return await self.storage.delete_account(account_id)

    async def set_active_upload_target(self, account_id: str) -> Optional[dict]:
        account = await self.storage.set_active_upload_target(account_id)
        return _strip_sensitive(account) if account else None

    async def get_active_upload_target(self) -> Optional[dict]:
        account = await self.storage.get_active_upload_target()
        return _strip_sensitive(account) if account else None

    async def test_account(self, account_id: str):
        account = await self._get_account_raw(account_id)
        if not account:
            return False, "Account not found"

        adapter = MEGAAdapter(email=account["email"])
        try:
            if account.get("encrypted_session"):
                session = decrypt_value(account["encrypted_session"])
                await adapter.restore_session(session)
            ok = await adapter.test_connection()
            if ok:
                quota = await adapter.get_quota()
                await self.storage.update_account(account_id, {
                    "storage_used": quota.used,
                    "storage_total": quota.total,
                    "status": "active",
                })
                return True, "Connection successful"
            return False, "Connection failed"
        except Exception as e:
            await self.storage.update_account(account_id, {"status": "error"})
            return False, str(e)
        finally:
            await adapter.close()

    async def update_sync_time(self, account_id: str):
        await self.storage.update_account(account_id, {
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
        })

    async def disable_account(self, account_id: str) -> Optional[dict]:
        account = await self._get_account_raw(account_id)
        if not account:
            return None
        updates = {"status": "disabled", "is_active_upload_target": 0}
        await self.storage.update_account(account_id, updates)
        return await self.get_account(account_id)

    async def enable_account(self, account_id: str) -> Optional[dict]:
        account = await self._get_account_raw(account_id)
        if not account:
            return None
        await self.storage.update_account(account_id, {"status": "active"})
        return await self.get_account(account_id)

    async def re_auth_account(self, account_id: str):
        from app.schemas.account import AccountTestResult
        account = await self._get_account_raw(account_id)
        if not account:
            return AccountTestResult(success=False, message="Account not found")

        adapter = MEGAAdapter(email=account["email"])
        try:
            if account.get("encrypted_session"):
                session = decrypt_value(account["encrypted_session"])
                restored = await adapter.restore_session(session)
                if not restored:
                    return AccountTestResult(success=False, message="Session expired, remove and re-add")

            ok = await adapter.test_connection()
            if ok:
                quota = await adapter.get_quota()
                await self.storage.update_account(account_id, {
                    "storage_used": quota.used,
                    "storage_total": quota.total,
                    "status": "active",
                })
                return AccountTestResult(success=True, message="Re-authentication successful")
            return AccountTestResult(success=False, message="Connection failed")
        except Exception as e:
            await self.storage.update_account(account_id, {"status": "error"})
            return AccountTestResult(success=False, message=str(e))
        finally:
            await adapter.close()
