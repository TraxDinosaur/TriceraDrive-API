from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Optional

from mega.client import MegaNzClient
from mega.vault import MegaVault
from mega.errors import RequestError
from mega.crypto import (
    a32_to_bytes,
    b64_to_a32,
    b64_url_decode,
    b64_url_encode,
    decrypt_key,
    decrypt_rsa_key,
    generate_v1_hash,
    mpi_to_int,
    prepare_v1_key,
    str_to_a32,
)

from app.adapters.base import QuotaInfo, RemoteFile, StorageAdapter
from app.exceptions import MFARequiredError


mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/yaml", ".yaml")
mimetypes.add_type("text/yaml", ".yml")


def _infer_mime(name: str) -> str | None:
    mime, _ = mimetypes.guess_type(name)
    return mime


class MEGAAdapter(StorageAdapter):

    def __init__(self, email: str = "", password: str = "", session_data: str = ""):
        self.email = email
        self.password = password
        self.session_data = session_data
        self._client: MegaNzClient | None = None
        self._authenticated = False

    async def _get_client(self) -> MegaNzClient:
        if self._client is None:
            self._client = MegaNzClient()
        return self._client

    async def _ensure_fs(self):
        client = await self._get_client()
        return await client.get_filesystem()

    async def get_filesystem(self, force: bool = False):
        """Get the MegaFilesystem object (cached in-memory by the library).
        
        Returns the MegaFilesystem which can be dumped to JSON for persistence
        via fs.dump() and restored via MegaFilesystem.from_dump().
        """
        client = await self._get_client()
        if force:
            fs = await client.get_filesystem(force=True)
        else:
            fs = await client.get_filesystem()
        return fs

    async def authenticate(self, email: str, password: str) -> str:
        client = await self._get_client()
        try:
            await client.login(email, password)
        except RequestError as e:
            if hasattr(e, "code") and e.code == -26:
                raise MFARequiredError(email=email) from e
            raise
        self._authenticated = True
        self.email = email
        session = json.dumps({
            "session_id": client._api.session_id,
            "master_key": list(client._core.vault.master_key),
        })
        return session

    async def authenticate_with_mfa(self, email: str, password: str, mfa_code: str) -> str:
        client = await self._get_client()
        email = email.lower()

        account = await client._api.post({"a": "us0", "user": email})
        version = account["v"]
        salt = account.get("s")

        if version == 2 and salt:
            derived_key = hashlib.pbkdf2_hmac(
                hash_name="sha512",
                password=password.encode(),
                salt=b64_url_decode(salt),
                iterations=100_000,
                dklen=32,
            )
            aes_key = str_to_a32(derived_key[:16])
            user_hash = b64_url_encode(derived_key[-16:])
        elif version == 1:
            aes_key = prepare_v1_key(password)
            user_hash = generate_v1_hash(email, aes_key)
        else:
            raise RuntimeError(f"Account version not supported: {version = }")

        credentials = await client._api.post({
            "a": "us",
            "user": email,
            "uh": user_hash,
            "mfa": mfa_code,
        })

        master_key = decrypt_key(b64_to_a32(credentials["k"]), aes_key)
        private_key = a32_to_bytes(decrypt_key(b64_to_a32(credentials["privk"]), master_key))

        rsa_key = decrypt_rsa_key(private_key)
        encrypted_sid = mpi_to_int(b64_url_decode(credentials["csid"]))
        decrypted_sid = int(rsa_key._decrypt(encrypted_sid))
        sid_bytes = decrypted_sid.to_bytes((decrypted_sid.bit_length() + 7) // 8 or 1, "big")
        session_id = b64_url_encode(sid_bytes[:43])

        client._api.session_id = session_id
        client._core.vault = MegaVault(master_key)
        client._core.filesystem = await client._core._prepare_filesystem()

        self._authenticated = True
        self.email = email
        session_data = json.dumps({
            "session_id": session_id,
            "master_key": list(master_key),
        })
        return session_data

    async def restore_session(self, session_data: str) -> bool:
        try:
            data = json.loads(session_data)
            client = await self._get_client()
            client._api.session_id = data.get("session_id")
            client._core.vault = MegaVault(master_key=tuple(data.get("master_key", [])))
            await client.get_filesystem()
            self._authenticated = True
            self.session_data = session_data
            return True
        except Exception:
            self._authenticated = False
            return False

    async def test_connection(self) -> bool:
        if not self._authenticated or not self._client:
            return False
        try:
            await self._client.get_user()
            return True
        except RequestError:
            self._authenticated = False
            return False
        except Exception:
            return False

    async def list_files(self, folder_path: str = "/") -> list[RemoteFile]:
        client = await self._get_client()
        fs = await self._ensure_fs()
        results: list[RemoteFile] = []

        for node in fs:
            if node.type.value in (2, 3, 4):
                continue
            if not node.attributes.name:
                continue

            is_folder = node.type.value == 1
            rel_path = fs.relative_path(node.id)
            parent = fs.absolute_path(node.parent_id) if node.parent_id else Path("/")
            parent_str = str(parent).replace("\\", "/")

            results.append(RemoteFile(
                name=node.attributes.name,
                size=int(node.size) if node.size else 0,
                remote_path=str(rel_path).replace("\\", "/"),
                folder_path=parent_str if parent_str != "/Cloud Drive" else "/",
                hash=node.id,
                mime_type=_infer_mime(node.attributes.name),
                created_at=str(node.created_at) if node.created_at else None,
                modified_at=None,
                is_folder=is_folder,
            ))

        return results

    async def _get_root_node_id(self) -> str:
        fs = await self._ensure_fs()
        for node in fs:
            if node.type.value == 2:
                return node.id
        raise RuntimeError("Root folder not found")

    async def create_folder(self, name: str, parent_path: str = "/") -> RemoteFile:
        client = await self._get_client()
        fs = await self._ensure_fs()

        parent_clean = parent_path.strip("/")
        full_path = f"/{parent_clean}/{name}" if parent_clean else f"/{name}"
        new_node = await client.create_folder(full_path)
        rel_path = fs.relative_path(new_node.id)
        parent = fs.absolute_path(new_node.parent_id) if new_node.parent_id else Path("/")
        return RemoteFile(
            name=new_node.attributes.name,
            size=0,
            remote_path=str(rel_path).replace("\\", "/"),
            folder_path=str(parent).replace("\\", "/") if str(parent) != "/Cloud Drive" else "/",
            hash=new_node.id,
            mime_type=None,
            created_at=None,
            modified_at=None,
            is_folder=True,
        )

    async def upload_file(self, local_path: str, remote_path: str) -> RemoteFile:
        client = await self._get_client()
        root_id = await self._get_root_node_id()
        file_name = Path(local_path).name

        await client._core.upload(local_path, root_id)

        fs = await client.get_filesystem(force=True)
        for node in fs.files:
            if node.attributes.name == file_name:
                rel_path = fs.relative_path(node.id)
                parent = fs.absolute_path(node.parent_id) if node.parent_id else Path("/")
                parent_str = str(parent).replace("\\", "/")
                return RemoteFile(
                    name=node.attributes.name,
                    size=int(node.size) if node.size else 0,
                    remote_path=str(rel_path).replace("\\", "/"),
                    folder_path=parent_str if parent_str != "/Cloud Drive" else "/",
                    hash=node.id,
                    mime_type=_infer_mime(node.attributes.name),
                    created_at=str(node.created_at) if node.created_at else None,
                    modified_at=None,
                    is_folder=False,
                )

        raise RuntimeError(f"Uploaded file '{file_name}' not found after upload")

    async def _resolve_folder_id(self, folder_path: str) -> str:
        fs = await self._ensure_fs()
        target = folder_path.rstrip("/")
        for node in fs:
            if node.type.value != 1:
                continue
            node_path = str(fs.absolute_path(node.id)).replace("\\", "/").rstrip("/")
            if node_path == target:
                return node.id
        raise FileNotFoundError(f"Folder '{folder_path}' not found on MEGA")

    async def upload_to_folder(self, local_path: str, file_name: str, folder_path: str) -> RemoteFile:
        client = await self._get_client()
        parent_id = await self._resolve_folder_id(folder_path)
        await client._core.upload(local_path, parent_id)
        fs = await client.get_filesystem(force=True)
        for node in fs.files:
            if node.attributes.name == file_name:
                rel_path = fs.relative_path(node.id)
                parent = fs.absolute_path(node.parent_id) if node.parent_id else Path("/")
                parent_str = str(parent).replace("\\", "/")
                return RemoteFile(
                    name=node.attributes.name,
                    size=int(node.size) if node.size else 0,
                    remote_path=str(rel_path).replace("\\", "/"),
                    folder_path=parent_str if parent_str != "/Cloud Drive" else "/",
                    hash=node.id,
                    mime_type=_infer_mime(node.attributes.name),
                    created_at=str(node.created_at) if node.created_at else None,
                    modified_at=None,
                    is_folder=False,
                )
        raise RuntimeError(f"Uploaded file '{file_name}' not found after upload")

    async def download_file(self, remote_path: str, local_path: str) -> str:
        client = await self._get_client()
        fs = await self._ensure_fs()
        name = os.path.basename(remote_path)
        dir_path = os.path.dirname(local_path) or "."

        target = None
        for node in fs.files:
            if node.attributes.name == name:
                target = node
                break

        if not target:
            raise FileNotFoundError(f"File '{name}' not found on MEGA")

        result = await client.download(target, dir_path)
        return str(result)

    async def download_file_by_id(self, node_id: str, dest_dir: str = "") -> str:
        client = await self._get_client()
        fs = await self._ensure_fs()
        try:
            node = fs[node_id]
        except KeyError:
            raise FileNotFoundError(f"Node '{node_id}' not found")
        if not node.is_file:
            raise ValueError(f"Node '{node_id}' is not a file")
        out_dir = dest_dir or tempfile.mkdtemp()
        result = await client.download(node, out_dir)
        return str(result)

    async def _get_rubbish_node_id(self) -> str:
        fs = await self._ensure_fs()
        for node in fs:
            if node.type.value == 4:
                return node.id
        raise RuntimeError("Rubbish bin not found")

    async def delete_node_by_id(self, node_id: str) -> bool:
        client = await self._get_client()
        try:
            await client.destroy(node_id)
            return True
        except Exception:
            return False

    async def trash_node_by_id(self, node_id: str) -> bool:
        client = await self._get_client()
        try:
            rubbish_id = await self._get_rubbish_node_id()
            fs = await self._ensure_fs()
            if node_id in fs:
                await client.move(node_id, rubbish_id)
                return True
            return False
        except Exception:
            return False

    async def restore_node_from_trash(self, node_id: str, target_parent_id: str | None = None) -> bool:
        client = await self._get_client()
        try:
            if target_parent_id is None:
                root_id = await self._get_root_node_id()
                target_parent_id = root_id
            await client.move(node_id, target_parent_id)
            return True
        except Exception:
            return False

    async def empty_trash(self) -> bool:
        client = await self._get_client()
        try:
            await client.empty_trash()
            return True
        except Exception:
            return False

    async def get_public_link(self, node_id: str) -> str | None:
        client = await self._get_client()
        try:
            fs = await self._ensure_fs()
            if node_id not in fs:
                return None
            node = fs[node_id]
            link = await client.get_public_link(node)
            return link
        except Exception:
            return None

    async def disable_public_link(self, node_id: str) -> bool:
        client = await self._get_client()
        try:
            fs = await self._ensure_fs()
            if node_id not in fs:
                return False
            node = fs[node_id]
            await client.export(node, enabled=False)
            return True
        except Exception:
            return False

    async def delete_file(self, remote_path: str) -> bool:
        client = await self._get_client()
        fs = await self._ensure_fs()
        name = os.path.basename(remote_path)
        for node in fs.files:
            if node.attributes.name == name:
                await client.destroy(node.id)
                return True
        return False

    async def rename_file(self, old_path: str, new_path: str) -> bool:
        client = await self._get_client()
        fs = await self._ensure_fs()
        old_name = os.path.basename(old_path)
        new_name = os.path.basename(new_path)
        for node in fs.files:
            if node.attributes.name == old_name:
                await client.rename(node, new_name)
                return True
        return False

    async def get_quota(self) -> QuotaInfo:
        if not self._authenticated or not self._client:
            return QuotaInfo(used=0, total=0)
        try:
            stats = await self._client.get_account_stats()
            return QuotaInfo(
                used=int(stats.storage.used),
                total=int(stats.storage.max),
            )
        except Exception:
            return QuotaInfo(used=0, total=0)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
