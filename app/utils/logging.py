from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_CREDENTIAL_PATTERNS = [
    re.compile(r"(password|passwd|secret|token|session)\s*[:=]\s*\S+", re.IGNORECASE),
]


def redact_credentials(msg: str) -> str:
    for pattern in _CREDENTIAL_PATTERNS:
        msg = pattern.sub(r"\1=***", msg)
    return msg


class CredentialFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_credentials(record.msg)
        return True


def setup_logging() -> None:
    root = logging.getLogger()
    if not root.hasHandlers():
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        handler.addFilter(CredentialFilter())
        root.addHandler(handler)
        root.setLevel(logging.INFO)


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = str(uuid.uuid4())[:8]
        request.state.request_id = req_id
        start = datetime.now(timezone.utc)

        logger = logging.getLogger("access")
        logger.info("[%s] → %s %s", req_id, request.method, request.url.path)

        try:
            response: Response = await call_next(request)
        except Exception as e:
            logger.error("[%s] ✗ %s %s — %s", req_id, request.method, request.url.path, str(e))
            raise

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info("[%s] ← %s %s — %s (%.3fs)", req_id, request.method, request.url.path, response.status_code, elapsed)
        response.headers["X-Request-ID"] = req_id
        return response
