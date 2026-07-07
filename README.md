# TriceraDrive API

> Multi-account MEGA cloud manager backend — FastAPI + SQLite + async-mega-py

Manage multiple MEGA.nz accounts from a single API. Upload, download, search, trash, share, and sync files across accounts with automatic failover and quota-aware routing.

## Features

- **Multi-account** — Add unlimited MEGA accounts, each with encrypted session storage
- **Auto-switch upload target** — When an account is full, uploads automatically route to the next available account
- **File operations** — Upload, download, preview, rename, create folders, save edits
- **Trash & restore** — Move files to trash, restore, or permanently delete
- **Share links** — Generate and revoke public MEGA share links
- **Batch operations** — Delete, favorite, and move multiple files at once
- **Full-text search** — FTS5-powered content search across text files (<1MB)
- **Activity log** — Track all file actions (upload, delete, trash, restore, share, rename)
- **Duplicate detection** — Find files with identical names and sizes across accounts
- **Account export** — Export account metadata (without credentials)
- **Sync** — Periodic filesystem sync with content indexing
- **Auth** — PBKDF2 password hashing, JWT tokens (24h expiry)
- **Structured logging** — Request ID per request, JSON-formatted logs

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.14+ |
| Framework | FastAPI >=0.139 |
| Database | SQLite (WAL mode) + FTS5 |
| MEGA SDK | async-mega-py >=2.4.1 |
| Auth | PBKDF2 (SHA-256, 100k iterations) + JWT (HS256) |
| Encryption | Fernet (cryptography) |
| Server | Uvicorn |

## Quick Start

```bash
# clone
git clone https://github.com/TraxDinosaur/triceradrive-api.git
cd triceradrive-api

# install
uv sync
# or: pip install -e .

# run
uv run uvicorn main:app --reload --port 8000
```

API runs at **http://localhost:8000**. Open `http://localhost:8000/docs` for Swagger UI.

## Configuration

Set via environment variables (prefix `MEGA_`) or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `MEGA_APP_NAME` | `MEGA` | Application name |
| `MEGA_DEBUG` | `false` | Enable debug mode |
| `MEGA_DATA_DIR` | `data` | Data directory (DB + fs caches) |
| `MEGA_ENCRYPTION_KEY` | `change-me-...` | JWT + Fernet secret key |
| `MEGA_SYNC_INTERVAL_SECONDS` | `300` | Periodic sync interval |
| `MEGA_CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed CORS origins |

## API Endpoints

### Auth
| Method | Path | Description |
|--------|------|-------------|
| GET | `/auth/status` | Check if setup is required |
| POST | `/auth/setup` | Set initial password |
| POST | `/auth/login` | Login, returns JWT |
| POST | `/auth/verify` | Verify token validity |

### Accounts
| Method | Path | Description |
|--------|------|-------------|
| GET | `/accounts` | List all accounts |
| POST | `/accounts` | Add a MEGA account |
| GET | `/accounts/{id}` | Get account details |
| PATCH | `/accounts/{id}` | Update account |
| DELETE | `/accounts/{id}` | Remove account |
| POST | `/accounts/{id}/test` | Test account connectivity |
| POST | `/accounts/{id}/set-active-upload-target` | Set as upload target |
| POST | `/accounts/{id}/disable` | Disable account |
| POST | `/accounts/{id}/enable` | Re-enable account |
| POST | `/accounts/{id}/re-auth` | Re-authenticate account |
| GET | `/accounts/active/upload-target` | Get current upload target |
| GET | `/accounts/duplicates` | Find duplicate files |
| GET | `/accounts/export` | Export account metadata |

### Files
| Method | Path | Description |
|--------|------|-------------|
| GET | `/files` | List files (filterable) |
| GET | `/files/search` | Search files by name + content |
| GET | `/files/{id}` | Get file details |
| POST | `/files/upload` | Upload file |
| POST | `/files/folder` | Create folder |
| POST | `/files/new` | Create new text file |
| DELETE | `/files/{id}` | Permanently delete |
| POST | `/files/{id}/rename` | Rename file |
| GET\|POST | `/files/{id}/download` | Download file |
| POST | `/files/{id}/save` | Save edited content |
| POST | `/files/{id}/refresh` | Refresh from MEGA |
| POST | `/files/{id}/trash` | Move to trash |
| POST | `/files/{id}/restore` | Restore from trash |
| POST | `/files/{id}/share` | Create share link |
| POST | `/files/{id}/unshare` | Remove share link |
| POST | `/files/batch/delete` | Batch delete |
| POST | `/files/batch/favorite` | Batch favorite/unfavorite |
| POST | `/files/batch/move` | Batch move files |
| POST | `/files/trash/empty` | Empty trash for account |
| GET | `/files/activity/recent` | Recent activity log |

### Sync
| Method | Path | Description |
|--------|------|-------------|
| POST | `/sync/run` | Trigger full sync |
| GET | `/sync/status` | Sync status |
| POST | `/sync/{id}` | Sync single account |
| POST | `/sync/all` | Sync all accounts |

### Settings
| Method | Path | Description |
|--------|------|-------------|
| GET | `/settings` | Get settings |
| PUT | `/settings` | Update settings |

### Health
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |

## Project Structure

```
triceradrive-api/
├── main.py                 # FastAPI app entry point
├── app/
│   ├── auth.py             # JWT + PBKDF2 auth
│   ├── config.py           # Settings via pydantic-settings
│   ├── database.py         # SQLite (AsyncStorage) — all queries
│   ├── exceptions.py       # Custom exceptions
│   ├── adapters/
│   │   ├── base.py         # Abstract adapter interface
│   │   └── mega.py         # MEGA.nz adapter (async-mega-py)
│   ├── routers/
│   │   ├── accounts.py     # Account CRUD + management
│   │   ├── auth.py         # Auth endpoints
│   │   ├── files.py        # File operations
│   │   ├── settings.py     # Settings endpoints
│   │   └── sync.py         # Sync endpoints
│   ├── schemas/
│   │   ├── account.py      # Account Pydantic models
│   │   ├── file.py         # File Pydantic models
│   │   └── sync.py         # Sync Pydantic models
│   ├── services/
│   │   ├── account.py      # Account business logic
│   │   ├── file.py         # File business logic
│   │   ├── scheduler.py    # Periodic sync scheduler
│   │   └── sync.py         # Sync orchestration + FTS indexing
│   └── utils/
│       ├── encryption.py   # Fernet encryption helpers
│       └── logging.py      # Structured logging + RequestLogMiddleware
└── pyproject.toml
```

## License

CC BY-SA 4.0 — see [LICENSE](LICENSE).
