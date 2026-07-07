from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from app.config import get_settings

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


class AsyncStorage:
    def __init__(self, db_path: str | Path, data_dir: str | Path):
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._db: aiosqlite.Connection | None = None

    async def connect(self):
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._init_tables()
        await self._migrate_from_json()

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    async def _init_tables(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                email TEXT NOT NULL,
                auth_type TEXT DEFAULT 'password',
                encrypted_session TEXT,
                storage_used INTEGER DEFAULT 0,
                storage_total INTEGER DEFAULT 0,
                is_active_upload_target INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                last_sync_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
                account_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                name TEXT NOT NULL,
                extension TEXT,
                size INTEGER DEFAULT 0,
                hash TEXT,
                folder_path TEXT DEFAULT '/',
                remote_path TEXT NOT NULL,
                mime_type TEXT,
                is_folder INTEGER DEFAULT 0,
                created_at TEXT,
                modified_at TEXT,
                is_deleted INTEGER DEFAULT 0,
                is_favorite INTEGER DEFAULT 0,
                last_seen_at TEXT,
                PRIMARY KEY (account_id, node_id),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_files_account ON files(account_id);
            CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
            CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_path);
            CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension);
            CREATE INDEX IF NOT EXISTS idx_files_deleted ON files(is_deleted);

            CREATE TABLE IF NOT EXISTS file_contents (
                node_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                content TEXT NOT NULL,
                indexed_at TEXT NOT NULL,
                PRIMARY KEY (account_id, node_id),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS file_fts USING fts5(
                node_id UNINDEXED, account_id UNINDEXED, content,
                tokenize='porter unicode61',
                content=''
            );

            CREATE TABLE IF NOT EXISTS sync_jobs (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                entity_name TEXT,
                account_id TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_activity_action ON activity_log(action);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        await self.db.commit()

    async def _migrate_from_json(self):
        accounts_path = self.data_dir / "accounts.json"
        if not accounts_path.exists():
            return

        logger.info("Migrating from JSON storage to SQLite...")
        count = 0

        with open(accounts_path) as f:
            accounts_data = json.load(f)

        for acct_id, acct in accounts_data.items():
            existing = await self.db.execute_fetchall(
                "SELECT 1 FROM accounts WHERE id = ?", (acct_id,)
            )
            if existing:
                continue

            await self.db.execute(
                """INSERT INTO accounts (id, label, email, auth_type, encrypted_session,
                   storage_used, storage_total, is_active_upload_target, status,
                   last_sync_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    acct_id, acct.get("label", ""), acct.get("email", ""),
                    acct.get("auth_type", "password"), acct.get("encrypted_session"),
                    acct.get("storage_used", 0), acct.get("storage_total", 0),
                    1 if acct.get("is_active_upload_target") else 0,
                    acct.get("status", "active"), acct.get("last_sync_at"),
                    acct.get("created_at", _now().isoformat()),
                    acct.get("updated_at", _now().isoformat()),
                ),
            )

            fs_path = self.data_dir / f"fs_{acct_id}.json"
            if fs_path.exists():
                try:
                    from mega.filesystem import UserFileSystem
                    with open(fs_path) as f:
                        fs_data = json.load(f)
                    fs = UserFileSystem.from_dump(fs_data)
                    await self._sync_files_from_fs(acct_id, fs)
                except Exception as e:
                    logger.warning("Failed to migrate fs cache for %s: %s", acct_id, e)

            count += 1

        await self.db.commit()
        accounts_path.rename(accounts_path.with_suffix(".json.bak"))
        for p in self.data_dir.glob("fs_*.json"):
            p.rename(p.with_suffix(".json.bak"))
        logger.info("Migrated %d accounts from JSON to SQLite", count)

    async def _sync_files_from_fs(self, account_id: str, fs):
        from app.adapters.mega import _infer_mime
        await self.db.execute("DELETE FROM files WHERE account_id = ? AND is_deleted = 0", (account_id,))
        for node in fs:
            SKIP_TYPES = (2, 3, 4)  # ROOT_FOLDER, INBOX, TRASH
            if (not node.attributes or not node.attributes.name
                    or (hasattr(node, "type") and getattr(node.type, "value", None) in SKIP_TYPES)):
                continue
            name = node.attributes.name
            ext = name.split(".")[-1].lower() if "." in name else None
            parent_path = fs.absolute_path(node.parent_id) if node.parent_id else Path("/")
            parent_str = str(parent_path).replace("\\", "/")
            await self.db.execute(
                """INSERT OR REPLACE INTO files
                   (account_id, node_id, name, extension, size, hash, folder_path,
                    remote_path, mime_type, is_folder, created_at, modified_at,
                    is_deleted, is_favorite, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)""",
                (
                    account_id, node.id, name, ext,
                    int(node.size) if node.size else 0, node.id,
                    parent_str if parent_str != "/Cloud Drive" else "/",
                    str(fs.relative_path(node.id)).replace("\\", "/"),
                    _infer_mime(name),
                    1 if node.type.value == 1 else 0,
                    str(node.created_at) if node.created_at else None,
                    None,
                    _now().isoformat(),
                ),
            )

    def _row(self, row: aiosqlite.Row | None) -> dict | None:
        if row is None:
            return None
        return dict(row)

    def _rows(self, rows: list[aiosqlite.Row]) -> list[dict]:
        return [dict(r) for r in rows]

    # ── Accounts ──────────────────────────────────────────────

    async def create_account(self, data: dict) -> dict:
        account_id = str(uuid.uuid4())
        now = _now().isoformat()
        await self.db.execute(
            """INSERT INTO accounts (id, label, email, auth_type, encrypted_session,
               storage_used, storage_total, is_active_upload_target, status,
               last_sync_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                account_id, data["label"], data["email"],
                data.get("auth_type", "password"), data.get("encrypted_session"),
                data.get("storage_used", 0), data.get("storage_total", 0),
                0, "active", None, now, now,
            ),
        )
        await self.db.commit()
        return await self.get_account(account_id)

    async def list_accounts(self) -> list[dict]:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM accounts ORDER BY created_at ASC"
        )
        return self._rows(rows)

    async def get_account(self, account_id: str) -> dict | None:
        row = await self.db.execute_fetchall(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        )
        return self._row(row[0]) if row else None

    async def update_account(self, account_id: str, updates: dict) -> dict | None:
        if not updates:
            return await self.get_account(account_id)
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [account_id]
        await self.db.execute(
            f"UPDATE accounts SET {sets}, updated_at = ? WHERE id = ?",
            [*updates.values(), _now().isoformat(), account_id],
        )
        await self.db.commit()
        return await self.get_account(account_id)

    async def delete_account(self, account_id: str) -> bool:
        await self.db.execute("DELETE FROM files WHERE account_id = ?", (account_id,))
        await self.db.execute("DELETE FROM sync_jobs WHERE account_id = ?", (account_id,))
        cursor = await self.db.execute(
            "DELETE FROM accounts WHERE id = ?", (account_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def get_active_upload_target(self) -> dict | None:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM accounts WHERE is_active_upload_target = 1 LIMIT 1"
        )
        return self._row(rows[0]) if rows else None

    async def set_active_upload_target(self, account_id: str) -> dict | None:
        await self.db.execute("UPDATE accounts SET is_active_upload_target = 0")
        await self.db.execute(
            "UPDATE accounts SET is_active_upload_target = 1 WHERE id = ?",
            (account_id,),
        )
        await self.db.commit()
        return await self.get_account(account_id)

    # ── Files ─────────────────────────────────────────────────

    async def replace_files_for_account(self, account_id: str, fs):
        await self._sync_files_from_fs(account_id, fs)
        await self.db.commit()

    async def list_files(self, params) -> list[dict]:
        conditions = []
        values = []
        if not params.include_deleted:
            conditions.append("f.is_deleted = 0")

        if params.account_id:
            conditions.append("f.account_id = ?")
            values.append(params.account_id)
        if params.folder_path:
            fp = params.folder_path.rstrip("/")
            conditions.append("f.folder_path = ?")
            values.append(fp)
        if params.search:
            conditions.append("LOWER(f.name) LIKE ?")
            values.append(f"%{params.search.lower()}%")
        if params.file_types:
            exts = [e.strip().lower() for e in params.file_types.split(",") if e.strip()]
            placeholders = ",".join("?" for _ in exts)
            conditions.append(f"f.extension IN ({placeholders})")
            values.extend(exts)
        elif params.file_type:
            conditions.append("f.extension = ?")
            values.append(params.file_type.lower())
        if params.min_size is not None:
            conditions.append("f.size >= ?")
            values.append(params.min_size)
        if params.max_size is not None:
            conditions.append("f.size <= ?")
            values.append(params.max_size)
        if params.date_from:
            conditions.append("f.created_at >= ?")
            values.append(params.date_from.isoformat() if hasattr(params.date_from, 'isoformat') else params.date_from)
        if params.date_to:
            conditions.append("f.created_at <= ?")
            values.append(params.date_to.isoformat() if hasattr(params.date_to, 'isoformat') else params.date_to)

        sort_col = {
            "name": "LOWER(f.name)", "size": "f.size",
            "account_id": "f.account_id", "created_at": "f.created_at",
            "modified_at": "f.modified_at",
        }.get(params.sort_by, "LOWER(f.name)")
        order = "DESC" if params.sort_order == "desc" else "ASC"

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""SELECT f.node_id AS id, f.account_id, f.name, f.extension, f.size,
                           f.hash, f.folder_path, f.remote_path, f.mime_type,
                           f.created_at, f.modified_at, f.is_deleted, f.is_favorite
                    FROM files f
                    {where_clause}
                    ORDER BY {sort_col} {order}
                    LIMIT ? OFFSET ?"""
        values.extend([params.limit, params.offset])

        rows = await self.db.execute_fetchall(query, values)
        return self._rows(rows)

    async def get_file(self, file_id: str) -> dict | None:
        rows = await self.db.execute_fetchall(
            """SELECT node_id AS id, account_id, name, extension, size,
                      hash, folder_path, remote_path, mime_type,
                      created_at, modified_at, is_deleted, is_favorite
               FROM files WHERE node_id = ?""",
            (file_id,)
        )
        return self._row(rows[0]) if rows else None

    async def get_files_by_account(self, account_id: str) -> list[dict]:
        rows = await self.db.execute_fetchall(
            """SELECT node_id AS id, account_id, name, extension, size,
                      hash, folder_path, remote_path, mime_type,
                      created_at, modified_at, is_deleted, is_favorite
               FROM files WHERE account_id = ? AND is_deleted = 0 ORDER BY name ASC""",
            (account_id,),
        )
        return self._rows(rows)

    async def search_files(self, q: str, limit: int = 50) -> list[dict]:
        rows = await self.db.execute_fetchall(
            """SELECT f.node_id AS id, f.account_id, f.name, f.extension, f.size,
                      f.hash, f.folder_path, f.remote_path, f.mime_type,
                      f.is_deleted, f.is_favorite,
                      a.email AS account_email, a.label AS account_label
               FROM files f
               JOIN accounts a ON a.id = f.account_id
               WHERE f.is_deleted = 0 AND LOWER(f.name) LIKE ?
               ORDER BY f.size DESC
               LIMIT ?""",
            (f"%{q.lower()}%", limit),
        )
        return self._rows(rows)

    async def delete_file_record(self, account_id: str, node_id: str):
        await self.db.execute(
            "UPDATE files SET is_deleted = 1, last_seen_at = ? WHERE account_id = ? AND node_id = ?",
            (_now().isoformat(), account_id, node_id),
        )
        await self.db.commit()

    async def index_file_content(self, node_id: str, account_id: str, content: str):
        now = _now().isoformat()
        await self.db.execute(
            "INSERT OR REPLACE INTO file_contents (node_id, account_id, content, indexed_at) VALUES (?, ?, ?, ?)",
            (node_id, account_id, content, now),
        )
        await self.db.execute(
            "INSERT OR REPLACE INTO file_fts (node_id, account_id, content) VALUES (?, ?, ?)",
            (node_id, account_id, content),
        )
        await self.db.commit()

    async def remove_file_content_index(self, node_id: str, account_id: str):
        await self.db.execute(
            "DELETE FROM file_contents WHERE node_id = ? AND account_id = ?",
            (node_id, account_id),
        )
        await self.db.execute(
            "DELETE FROM file_fts WHERE node_id = ? AND account_id = ?",
            (node_id, account_id),
        )
        await self.db.commit()

    async def remove_all_content_indexes(self, account_id: str):
        await self.db.execute("DELETE FROM file_contents WHERE account_id = ?", (account_id,))
        await self.db.execute("DELETE FROM file_fts WHERE account_id = ?", (account_id,))
        await self.db.commit()

    async def search_file_contents(self, q: str, limit: int = 50) -> list[dict]:
        try:
            rows = await self.db.execute_fetchall(
                """SELECT f.node_id AS id, f.account_id, f.name, f.extension, f.size,
                          f.folder_path, a.email AS account_email, a.label AS account_label,
                          snippet(file_fts, 0, '<mark>', '</mark>', '...', 40) AS highlight
                   FROM file_fts
                   JOIN files f ON f.node_id = file_fts.node_id AND f.account_id = file_fts.account_id
                   JOIN accounts a ON a.id = f.account_id
                   WHERE file_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (q, limit),
            )
            return self._rows(rows)
        except Exception:
            return []

    async def log_activity(self, action: str, entity_type: str, entity_id: str | None = None,
                           entity_name: str | None = None, account_id: str | None = None,
                           details: str | None = None):
        await self.db.execute(
            """INSERT INTO activity_log (action, entity_type, entity_id, entity_name, account_id, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (action, entity_type, entity_id, entity_name, account_id, details, _now().isoformat()),
        )
        await self.db.commit()

    async def get_recent_activity(self, limit: int = 50, action: str | None = None) -> list[dict]:
        query = "SELECT * FROM activity_log"
        params = []
        if action:
            query += " WHERE action = ?"
            params.append(action)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = await self.db.execute_fetchall(query, params)
        return self._rows(rows)

    async def set_file_favorite(self, file_id: str, favorite: bool):
        await self.db.execute(
            "UPDATE files SET is_favorite = ? WHERE node_id = ?",
            (1 if favorite else 0, file_id),
        )
        await self.db.commit()

    async def upsert_file(self, account_id: str, file_data: dict):
        await self.db.execute(
            """INSERT OR REPLACE INTO files
               (account_id, node_id, name, extension, size, hash, folder_path,
                remote_path, mime_type, is_folder, created_at, modified_at,
                is_deleted, is_favorite, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)""",
            (
                account_id, file_data["node_id"], file_data["name"],
                file_data.get("extension"), file_data.get("size", 0),
                file_data.get("hash", file_data["node_id"]),
                file_data.get("folder_path", "/"), file_data.get("remote_path", "/"),
                file_data.get("mime_type"), file_data.get("is_folder", 0),
                file_data.get("created_at"), file_data.get("modified_at"),
                _now().isoformat(),
            ),
        )
        await self.db.commit()

    # ── Filesystem Cache ──────────────────────────────────────

    def _fs_cache_path(self, account_id: str) -> Path:
        return self.data_dir / f"fs_{account_id}.json"

    async def get_filesystem(self, account_id: str):
        from mega.filesystem import UserFileSystem
        path = self._fs_cache_path(account_id)
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            return UserFileSystem.from_dump(data)
        return None

    async def save_filesystem(self, account_id: str, fs):
        data = fs.dump()
        with open(self._fs_cache_path(account_id), "w") as f:
            json.dump(data, f, indent=2, default=str)

    async def invalidate_filesystem_cache(self, account_id: str):
        path = self._fs_cache_path(account_id)
        if path.exists():
            path.unlink()

    # ── Sync Status ───────────────────────────────────────────

    async def set_sync_status(self, account_id: str, status: dict):
        await self.db.execute(
            """INSERT OR REPLACE INTO sync_jobs
               (id, account_id, status, started_at, finished_at, error_message, retry_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                status.get("id", account_id), account_id,
                status.get("status", "unknown"), status.get("started_at"),
                status.get("finished_at"), status.get("error_message"),
                status.get("retry_count", 0),
            ),
        )
        await self.db.commit()

    async def get_sync_status(self, account_id: str) -> dict:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM sync_jobs WHERE account_id = ? ORDER BY started_at DESC LIMIT 1",
            (account_id,),
        )
        return self._row(rows[0]) if rows else {}

    async def get_all_sync_status(self) -> dict:
        rows = await self.db.execute_fetchall(
            """SELECT account_id, status, started_at, finished_at, error_message
               FROM sync_jobs
               WHERE id IN (SELECT MAX(id) FROM sync_jobs GROUP BY account_id)"""
        )
        return {r["account_id"]: dict(r) for r in rows}

    # ── Settings ──────────────────────────────────────────────

    async def get_setting(self, key: str, default: Any = None) -> Any:
        rows = await self.db.execute_fetchall(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        if rows:
            return rows[0]["value"]
        return default

    async def set_setting(self, key: str, value: str):
        await self.db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self.db.commit()

    async def get_all_settings(self) -> dict:
        rows = await self.db.execute_fetchall("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}


_storage: AsyncStorage | None = None


async def init_storage():
    global _storage
    settings = get_settings()
    db_path = Path(settings.data_dir) / "tricera.db"
    _storage = AsyncStorage(db_path=db_path, data_dir=settings.data_dir)
    await _storage.connect()


async def get_storage() -> AsyncStorage:
    if _storage is None:
        await init_storage()
    return _storage
