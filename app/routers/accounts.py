from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.database import AsyncStorage, get_storage
from app.exceptions import MFARequiredError
from app.schemas.account import AccountCreate, AccountResponse, AccountTestResult, AccountUpdate, MFARequiredResponse
from app.services.account import AccountService

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(data: AccountCreate, storage: AsyncStorage = Depends(get_storage)):
    service = AccountService(storage)
    try:
        return await service.create_account(data)
    except MFARequiredError:
        return JSONResponse(
            status_code=449,
            content=MFARequiredResponse().model_dump(),
        )


@router.get("", response_model=list[AccountResponse])
async def list_accounts(storage: AsyncStorage = Depends(get_storage)):
    return await AccountService(storage).list_accounts()


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: str, storage: AsyncStorage = Depends(get_storage)):
    account = await AccountService(storage).get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(account_id: str, data: AccountUpdate, storage: AsyncStorage = Depends(get_storage)):
    account = await AccountService(storage).update_account(account_id, data)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: str, storage: AsyncStorage = Depends(get_storage)):
    deleted = await AccountService(storage).delete_account(account_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Account not found")


@router.post("/{account_id}/test", response_model=AccountTestResult)
async def test_account(account_id: str, storage: AsyncStorage = Depends(get_storage)):
    success, message = await AccountService(storage).test_account(account_id)
    return AccountTestResult(success=success, message=message)


@router.post("/{account_id}/set-active-upload-target", response_model=AccountResponse)
async def set_active_upload_target(account_id: str, storage: AsyncStorage = Depends(get_storage)):
    account = await AccountService(storage).set_active_upload_target(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.get("/active/upload-target", response_model=AccountResponse)
async def get_active_upload_target(storage: AsyncStorage = Depends(get_storage)):
    account = await AccountService(storage).get_active_upload_target()
    if not account:
        raise HTTPException(status_code=404, detail="No active upload target set")
    return account


@router.post("/{account_id}/disable", response_model=AccountResponse)
async def disable_account(account_id: str, storage: AsyncStorage = Depends(get_storage)):
    account = await AccountService(storage).disable_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.post("/{account_id}/enable", response_model=AccountResponse)
async def enable_account(account_id: str, storage: AsyncStorage = Depends(get_storage)):
    account = await AccountService(storage).enable_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.post("/{account_id}/re-auth", response_model=AccountTestResult)
async def re_auth_account(account_id: str, storage: AsyncStorage = Depends(get_storage)):
    return await AccountService(storage).re_auth_account(account_id)


@router.get("/duplicates", status_code=200)
async def find_duplicates(
    min_size: int = Query(0),
    storage: AsyncStorage = Depends(get_storage),
):
    rows = await storage.db.execute_fetchall(
        """SELECT f1.name, f1.size, f1.hash,
                  f1.account_id AS account_a, f1.node_id AS id_a,
                  f2.account_id AS account_b, f2.node_id AS id_b,
                  a1.label AS label_a, a2.label AS label_b
           FROM files f1
           JOIN files f2 ON f1.name = f2.name AND f1.size = f2.size
           JOIN accounts a1 ON a1.id = f1.account_id
           JOIN accounts a2 ON a2.id = f2.account_id
           WHERE f1.is_deleted = 0 AND f2.is_deleted = 0
             AND (f1.account_id < f2.account_id
                  OR (f1.account_id = f2.account_id AND f1.node_id < f2.node_id))
             AND f1.size >= ?
           ORDER BY f1.size DESC""",
        (min_size,),
    )
    return [{k: r[k] for k in r.keys()} for r in rows]


@router.get("/export", status_code=200)
async def export_accounts(storage: AsyncStorage = Depends(get_storage)):
    accounts = await storage.list_accounts()
    safe = []
    for a in accounts:
        safe.append({
            "label": a.get("label"),
            "email": a.get("email"),
            "auth_type": a.get("auth_type"),
            "storage_used": a.get("storage_used"),
            "storage_total": a.get("storage_total"),
        })
    return {"accounts": safe, "exported_at": datetime.now(timezone.utc).isoformat()}
