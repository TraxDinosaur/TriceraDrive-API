from __future__ import annotations

import mimetypes
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse as FastAPIFileResponse

from app.database import AsyncStorage, get_storage
from app.schemas.file import FileListParams as FileListParamsSchema
from app.schemas.file import FileResponse as FileSchema
from app.services.account import AccountService
from app.services.file import FileService
from app.utils.encryption import decrypt_value

router = APIRouter(prefix="/files", tags=["files"])


@router.get("", response_model=list[FileSchema])
async def list_files(
    account_id: str | None = Query(None),
    folder_path: str | None = Query(None),
    search: str | None = Query(None),
    file_type: str | None = Query(None),
    file_types: str | None = Query(None),
    min_size: int | None = Query(None),
    max_size: int | None = Query(None),
    sort_by: str = Query("name"),
    sort_order: str = Query("asc"),
    include_deleted: bool = Query(False),
    limit: int = Query(100),
    offset: int = Query(0),
    storage: AsyncStorage = Depends(get_storage),
):
    params = FileListParamsSchema(
        account_id=account_id,
        folder_path=folder_path,
        search=search,
        file_type=file_type,
        file_types=file_types,
        min_size=min_size,
        max_size=max_size,
        include_deleted=include_deleted,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    return await FileService(storage).list_files(params)


@router.get("/search", response_model=list[dict])
async def search_files(
    q: str = Query(..., min_length=1),
    limit: int = Query(50),
    include_content: bool = Query(False),
    storage: AsyncStorage = Depends(get_storage),
):
    results = await FileService(storage).search_files(q, limit)
    seen = {r["id"] for r in results}
    if include_content and len(results) < limit:
        content_results = await storage.search_file_contents(q, limit - len(results))
        for r in content_results:
            if r["id"] not in seen:
                results.append(r)
                seen.add(r["id"])
    return results[:limit]


@router.get("/by-account/{account_id}", response_model=list[FileSchema])
async def get_files_by_account(
    account_id: str,
    folder_path: str | None = Query(None),
    search: str | None = Query(None),
    sort_by: str = Query("name"),
    sort_order: str = Query("asc"),
    limit: int = Query(100),
    offset: int = Query(0),
    storage: AsyncStorage = Depends(get_storage),
):
    params = FileListParamsSchema(
        account_id=account_id,
        folder_path=folder_path,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    return await FileService(storage).list_files(params)


async def _resolve_account(storage, account_service, account_id: str | None) -> dict | None:
    if account_id:
        return await account_service.get_account_with_session(account_id)
    target = await storage.get_active_upload_target()
    if target:
        return await account_service.get_account_with_session(target["id"])
    return None


async def _setup_adapter(account: dict):
    from app.adapters.mega import MEGAAdapter

    adapter = MEGAAdapter(email=account["email"])
    if account.get("encrypted_session"):
        session = decrypt_value(account["encrypted_session"])
        restored = await adapter.restore_session(session)
        if not restored:
            await adapter.close()
            raise HTTPException(status_code=400, detail="Account session expired")
    return adapter


@router.post("/upload", response_model=FileSchema, status_code=201)
async def upload_file(
    file: UploadFile,
    account_id: str | None = Query(None),
    storage: AsyncStorage = Depends(get_storage),
):
    account_service = AccountService(storage)
    target_account = await _resolve_account(storage, account_service, account_id)
    if not target_account:
        raise HTTPException(status_code=400, detail="No active upload target set and no account_id provided")

    adapter = await _setup_adapter(target_account)
    quota = await adapter.get_quota()

    def _has_space(q, file_size):
        return q.total == 0 or (q.used or 0) + file_size <= q.total

    if not _has_space(quota, file.size or 0):
        await adapter.close()
        accounts = await storage.list_accounts()
        switched = None
        for a in accounts:
            if a["id"] == target_account["id"] or a.get("status") == "disabled":
                continue
            test_adapter = await _setup_adapter(a)
            try:
                q = await test_adapter.get_quota()
                if _has_space(q, file.size or 0):
                    await storage.set_active_upload_target(a["id"])
                    switched = a
                    break
            finally:
                await test_adapter.close()

        if switched:
            target_account = await account_service.get_account_with_session(switched["id"])
            adapter = await _setup_adapter(target_account)
            await storage.log_activity("auto_switch", "account", switched["id"], switched.get("label"))
        else:
            raise HTTPException(status_code=400, detail="All accounts full. No space available.")

    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, file.filename or "upload")
    try:
        content = await file.read()
        with open(tmp_path, "wb") as f:
            f.write(content)

        remote_file = await adapter.upload_file(tmp_path, f"/{file.filename or 'upload'}")

        fs = await adapter.get_filesystem(force=True)
        await storage.save_filesystem(target_account["id"], fs)
        await storage.replace_files_for_account(target_account["id"], fs)

        ext = Path(file.filename or "").suffix.lstrip(".").lower() if file.filename else None
        await storage.log_activity("upload", "file", remote_file.hash, remote_file.name, target_account["id"])
        return {
            "id": remote_file.hash,
            "account_id": target_account["id"],
            "name": remote_file.name,
            "extension": ext,
            "size": remote_file.size,
            "hash": remote_file.hash,
            "folder_path": remote_file.folder_path,
            "remote_path": remote_file.remote_path,
            "mime_type": file.content_type,
            "created_at": None,
            "modified_at": None,
            "is_deleted": False,
            "is_favorite": False,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await adapter.close()


@router.post("/folder", response_model=FileSchema, status_code=201)
async def create_folder(
    name: str = Query(..., min_length=1),
    account_id: str | None = Query(None),
    storage: AsyncStorage = Depends(get_storage),
):
    account_service = AccountService(storage)
    target_account = await _resolve_account(storage, account_service, account_id)
    if not target_account:
        raise HTTPException(status_code=400, detail="No active upload target set and no account_id provided")

    adapter = await _setup_adapter(target_account)
    try:
        remote_folder = await adapter.create_folder(name, "/")

        fs = await adapter.get_filesystem(force=True)
        await storage.save_filesystem(target_account["id"], fs)
        await storage.replace_files_for_account(target_account["id"], fs)

        return {
            "id": remote_folder.hash,
            "account_id": target_account["id"],
            "name": remote_folder.name,
            "extension": None,
            "size": 0,
            "hash": remote_folder.hash,
            "folder_path": remote_folder.folder_path,
            "remote_path": remote_folder.remote_path,
            "mime_type": None,
            "created_at": None,
            "modified_at": None,
            "is_deleted": False,
            "is_favorite": False,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Folder creation failed: {str(e)}")
    finally:
        await adapter.close()


@router.post("/new", response_model=FileSchema, status_code=201)
async def create_new_file(
    name: str = Query(..., min_length=1),
    content: str = Query(""),
    account_id: str | None = Query(None),
    storage: AsyncStorage = Depends(get_storage),
):
    if "." not in name:
        name += ".txt"

    account_service = AccountService(storage)
    target_account = await _resolve_account(storage, account_service, account_id)
    if not target_account:
        raise HTTPException(status_code=400, detail="No active upload target set and no account_id provided")

    adapter = None
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, name)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)

        adapter = await _setup_adapter(target_account)
        remote_file = await adapter.upload_file(tmp_path, f"/{name}")

        fs = await adapter.get_filesystem(force=True)
        await storage.save_filesystem(target_account["id"], fs)
        await storage.replace_files_for_account(target_account["id"], fs)

        ext = Path(name).suffix.lstrip(".").lower()
        return {
            "id": remote_file.hash,
            "account_id": target_account["id"],
            "name": remote_file.name,
            "extension": ext,
            "size": remote_file.size,
            "hash": remote_file.hash,
            "folder_path": remote_file.folder_path,
            "remote_path": remote_file.remote_path,
            "mime_type": "text/plain",
            "created_at": None,
            "modified_at": None,
            "is_deleted": False,
            "is_favorite": False,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File creation failed: {str(e)}")
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if adapter:
            await adapter.close()


@router.get("/{file_id}", response_model=FileSchema)
async def get_file(file_id: str, storage: AsyncStorage = Depends(get_storage)):
    file = await FileService(storage).get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    return file


@router.delete("/{file_id}", status_code=204)
async def delete_file(file_id: str, storage: AsyncStorage = Depends(get_storage)):
    fs_service = FileService(storage)
    file = await fs_service.get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account_service = AccountService(storage)
    account = await account_service.get_account_with_session(file["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from app.adapters.mega import MEGAAdapter

    adapter = MEGAAdapter(email=account["email"])
    if account.get("encrypted_session"):
        session = decrypt_value(account["encrypted_session"])
        await adapter.restore_session(session)

    try:
        try:
            await adapter.delete_node_by_id(file_id)
        except Exception:
            pass

        await storage.delete_file_record(file["account_id"], file_id)
        await storage.log_activity("delete", "file", file_id, file.get("name"), file["account_id"])

        fs = await adapter.get_filesystem(force=True)
        await storage.save_filesystem(account["id"], fs)
    finally:
        await adapter.close()


@router.post("/{file_id}/rename", response_model=FileSchema)
async def rename_file(
    file_id: str,
    name: str = Query(..., min_length=1),
    storage: AsyncStorage = Depends(get_storage),
):
    fs_service = FileService(storage)
    file = await fs_service.get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account_service = AccountService(storage)
    account = await account_service.get_account_with_session(file["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Owner account not found")

    from app.adapters.mega import MEGAAdapter

    adapter = MEGAAdapter(email=account["email"])
    if account.get("encrypted_session"):
        session = decrypt_value(account["encrypted_session"])
        await adapter.restore_session(session)

    try:
        old_path = file["remote_path"]
        parts = old_path.rsplit("/", 1)
        new_path = f"{parts[0]}/{name}" if len(parts) > 1 else f"/{name}"
        await adapter.rename_file(old_path, new_path)

        fs = await adapter.get_filesystem(force=True)
        await storage.save_filesystem(account["id"], fs)
        await storage.replace_files_for_account(account["id"], fs)

        file["name"] = name
        ext = Path(name).suffix.lstrip(".").lower() if "." in name else None
        file["extension"] = ext
        await storage.log_activity("rename", "file", file_id, name, file["account_id"])
        return file
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rename failed: {str(e)}")
    finally:
        await adapter.close()


@router.api_route("/{file_id}/download", methods=["GET", "POST"])
async def download_file(
    file_id: str,
    inline: bool = Query(False),
    storage: AsyncStorage = Depends(get_storage),
):
    fs_service = FileService(storage)
    file = await fs_service.get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account_service = AccountService(storage)
    account = await account_service.get_account_with_session(file["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Owner account not found")

    from app.adapters.mega import MEGAAdapter

    adapter = MEGAAdapter(email=account["email"])
    if account.get("encrypted_session"):
        session = decrypt_value(account["encrypted_session"])
        restored = await adapter.restore_session(session)
        if not restored:
            raise HTTPException(status_code=400, detail="Account session expired, please re-authenticate")

    try:
        tmp_dir = tempfile.mkdtemp()
        local_path = await adapter.download_file_by_id(file_id, tmp_dir)
        media_type = file.get("mime_type") or mimetypes.guess_type(file["name"])[0] or "application/octet-stream"
        disp_type = "inline" if inline else "attachment"
        return FastAPIFileResponse(
            path=local_path,
            filename=file["name"],
            media_type=media_type,
            content_disposition_type=disp_type,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found on remote MEGA account")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")
    finally:
        await adapter.close()


@router.post("/{file_id}/save", response_model=FileSchema)
async def save_file_content(
    file_id: str,
    content: str = Query(..., min_length=0),
    storage: AsyncStorage = Depends(get_storage),
):
    import shutil as _shutil

    fs_service = FileService(storage)
    file = await fs_service.get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account_service = AccountService(storage)
    account = await account_service.get_account_with_session(file["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Owner account not found")

    from app.adapters.mega import MEGAAdapter

    adapter = MEGAAdapter(email=account["email"])
    if account.get("encrypted_session"):
        session = decrypt_value(account["encrypted_session"])
        restored = await adapter.restore_session(session)
        if not restored:
            raise HTTPException(status_code=400, detail="Account session expired, please re-authenticate")

    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_path = os.path.join(tmp_dir, file["name"])
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)

        await adapter.delete_node_by_id(file_id)
        remote = await adapter.upload_to_folder(tmp_path, file["name"], file["folder_path"])

        fs = await adapter.get_filesystem(force=True)
        await storage.save_filesystem(account["id"], fs)
        await storage.replace_files_for_account(account["id"], fs)

        file["size"] = remote.size
        file["hash"] = remote.hash or file_id
        file["remote_path"] = remote.remote_path
        return file
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")
    finally:
        _shutil.rmtree(tmp_dir, ignore_errors=True)
        await adapter.close()


@router.post("/{file_id}/refresh", response_model=FileSchema)
async def refresh_file(file_id: str, storage: AsyncStorage = Depends(get_storage)):
    fs_service = FileService(storage)
    file = await fs_service.get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account_service = AccountService(storage)
    account = await account_service.get_account_with_session(file["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Owner account not found")

    from app.adapters.mega import MEGAAdapter

    adapter = MEGAAdapter(email=account["email"])
    if account.get("encrypted_session"):
        session = decrypt_value(account["encrypted_session"])
        restored = await adapter.restore_session(session)
        if not restored:
            raise HTTPException(status_code=400, detail="Account session expired")

    try:
        fs = await adapter.get_filesystem(force=True)
        await storage.save_filesystem(account["id"], fs)
        await storage.replace_files_for_account(account["id"], fs)

        updated = await fs_service.get_file(file_id)
        if updated:
            return updated
        raise HTTPException(status_code=404, detail="File no longer exists on remote MEGA account")
    finally:
        await adapter.close()


@router.post("/batch/delete", status_code=200)
async def batch_delete(
    file_ids: str = Query(..., description="Comma-separated file IDs"),
    storage: AsyncStorage = Depends(get_storage),
):
    ids = [f.strip() for f in file_ids.split(",") if f.strip()]
    results = {"deleted": 0, "failed": 0, "errors": []}

    for fid in ids:
        try:
            fs_service = FileService(storage)
            file = await fs_service.get_file(fid)
            if not file:
                results["failed"] += 1
                results["errors"].append({"id": fid, "error": "Not found"})
                continue

            account = await AccountService(storage).get_account_with_session(file["account_id"])
            if not account:
                results["failed"] += 1
                results["errors"].append({"id": fid, "error": "Account not found"})
                continue

            from app.adapters.mega import MEGAAdapter
            adapter = MEGAAdapter(email=account["email"])
            try:
                if account.get("encrypted_session"):
                    session = decrypt_value(account["encrypted_session"])
                    await adapter.restore_session(session)
                try:
                    await adapter.delete_node_by_id(fid)
                except Exception:
                    pass
                await storage.delete_file_record(file["account_id"], fid)
                results["deleted"] += 1
            finally:
                await adapter.close()
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({"id": fid, "error": str(e)})

    return results


@router.post("/batch/favorite", status_code=200)
async def batch_favorite(
    file_ids: str = Query(..., description="Comma-separated file IDs"),
    favorite: bool = Query(True),
    storage: AsyncStorage = Depends(get_storage),
):
    ids = [f.strip() for f in file_ids.split(",") if f.strip()]
    count = 0
    for fid in ids:
        await storage.set_file_favorite(fid, favorite)
        count += 1
    return {"favorited": count}


@router.post("/batch/move", status_code=200)
async def batch_move(
    file_ids: str = Query(..., description="Comma-separated file IDs"),
    target_folder: str = Query(..., min_length=1),
    storage: AsyncStorage = Depends(get_storage),
):
    ids = [f.strip() for f in file_ids.split(",") if f.strip()]
    results = {"moved": 0, "failed": 0, "errors": []}

    for fid in ids:
        try:
            fs_service = FileService(storage)
            file = await fs_service.get_file(fid)
            if not file:
                results["failed"] += 1
                results["errors"].append({"id": fid, "error": "Not found"})
                continue

            account = await AccountService(storage).get_account_with_session(file["account_id"])
            if not account:
                results["failed"] += 1
                continue

            from app.adapters.mega import MEGAAdapter
            adapter = MEGAAdapter(email=account["email"])
            try:
                if account.get("encrypted_session"):
                    session = decrypt_value(account["encrypted_session"])
                    await adapter.restore_session(session)
                new_path = f"{target_folder.rstrip('/')}/{file['name']}"
                ok = await adapter.rename_file(file["remote_path"], new_path)
                if ok:
                    fs = await adapter.get_filesystem(force=True)
                    await storage.save_filesystem(account["id"], fs)
                    await storage.replace_files_for_account(account["id"], fs)
                    results["moved"] += 1
                else:
                    results["failed"] += 1
            finally:
                await adapter.close()
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({"id": fid, "error": str(e)})

    return results


@router.post("/{file_id}/trash", status_code=200)
async def trash_file(file_id: str, storage: AsyncStorage = Depends(get_storage)):
    file = await FileService(storage).get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account = await AccountService(storage).get_account_with_session(file["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Owner account not found")

    from app.adapters.mega import MEGAAdapter
    adapter = MEGAAdapter(email=account["email"])
    try:
        if account.get("encrypted_session"):
            session = decrypt_value(account["encrypted_session"])
            await adapter.restore_session(session)
        ok = await adapter.trash_node_by_id(file_id)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to move file to trash")
        await storage.delete_file_record(file["account_id"], file_id)
        await storage.log_activity("trash", "file", file_id, file.get("name"), file["account_id"])
        return {"message": "Moved to trash"}
    finally:
        await adapter.close()


@router.post("/{file_id}/restore", status_code=200)
async def restore_file(file_id: str, storage: AsyncStorage = Depends(get_storage)):
    file = await FileService(storage).get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account = await AccountService(storage).get_account_with_session(file["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Owner account not found")

    from app.adapters.mega import MEGAAdapter
    adapter = MEGAAdapter(email=account["email"])
    try:
        if account.get("encrypted_session"):
            session = decrypt_value(account["encrypted_session"])
            await adapter.restore_session(session)
        ok = await adapter.restore_node_from_trash(file_id)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to restore from trash")
        await storage.log_activity("restore", "file", file_id, file.get("name"), file["account_id"])

        fs = await adapter.get_filesystem(force=True)
        await storage.save_filesystem(account["id"], fs)
        await storage.replace_files_for_account(account["id"], fs)
        return {"message": "Restored from trash"}
    finally:
        await adapter.close()


@router.post("/trash/empty", status_code=200)
async def empty_trash(
    account_id: str = Query(...),
    storage: AsyncStorage = Depends(get_storage),
):
    account = await AccountService(storage).get_account_with_session(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from app.adapters.mega import MEGAAdapter
    adapter = MEGAAdapter(email=account["email"])
    try:
        if account.get("encrypted_session"):
            session = decrypt_value(account["encrypted_session"])
            await adapter.restore_session(session)
        ok = await adapter.empty_trash()
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to empty trash")
        return {"message": "Trash emptied"}
    finally:
        await adapter.close()


@router.post("/{file_id}/share", status_code=200)
async def create_share_link(file_id: str, storage: AsyncStorage = Depends(get_storage)):
    file = await FileService(storage).get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account = await AccountService(storage).get_account_with_session(file["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Owner account not found")

    from app.adapters.mega import MEGAAdapter
    adapter = MEGAAdapter(email=account["email"])
    try:
        if account.get("encrypted_session"):
            session = decrypt_value(account["encrypted_session"])
            await adapter.restore_session(session)
        link = await adapter.get_public_link(file_id)
        if not link:
            raise HTTPException(status_code=500, detail="Failed to create share link")
        await storage.log_activity("share", "file", file_id, file.get("name"), file["account_id"])
        return {"share_link": link}
    finally:
        await adapter.close()


@router.post("/{file_id}/unshare", status_code=200)
async def remove_share_link(file_id: str, storage: AsyncStorage = Depends(get_storage)):
    file = await FileService(storage).get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account = await AccountService(storage).get_account_with_session(file["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Owner account not found")

    from app.adapters.mega import MEGAAdapter
    adapter = MEGAAdapter(email=account["email"])
    try:
        if account.get("encrypted_session"):
            session = decrypt_value(account["encrypted_session"])
            await adapter.restore_session(session)
        ok = await adapter.disable_public_link(file_id)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to remove share link")
        return {"message": "Share link removed"}
    finally:
        await adapter.close()


@router.get("/activity/recent", status_code=200)
async def get_recent_activity(
    limit: int = Query(50),
    storage: AsyncStorage = Depends(get_storage),
):
    return await storage.get_recent_activity(limit=limit)
