"""
OAuth proxy storage factory for persistent token storage across container restarts.

On Linux (Docker/Kubernetes), fastmcp defaults to an in-memory store with an
ephemeral encryption key. Every pod restart wipes all registered MCP client
records and issued JTIs, forcing users to re-authenticate even though their
MCP client (e.g. Claude Desktop) retains its stored tokens.

This factory provides a stdlib-only async file-based store (no extra deps) so
that OAuth state persists on the volume-mounted ~/.mcp-atlassian directory.

Wire in via:
  ATLASSIAN_OAUTH_CLIENT_STORAGE_MODE=factory
  ATLASSIAN_OAUTH_CLIENT_STORAGE_FACTORY=oauth_storage:create_file_store

See docs/guides/atlassian-cloud-oauth.mdx for full setup instructions.

Tokens are stored as plain JSON files. Protect the storage directory using
OS/Kubernetes volume permissions and RBAC — do not expose it publicly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

_logger = logging.getLogger("mcp-atlassian.oauth-storage")

_PERSISTENT_DIR = Path("/home/app/.mcp-atlassian/oauth-proxy")
_FALLBACK_DIR = Path("/tmp/mcp-atlassian-oauth-proxy")


class _SimpleFileStore:
    """Minimal async key-value store backed by per-key JSON files on disk.

    Uses asyncio.to_thread for all I/O so it never blocks the event loop.
    Implements the AsyncKeyValue interface required by fastmcp's client_storage.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, collection: str, key: str) -> Path:
        digest = hashlib.sha256(f"{collection}\x00{key}".encode()).hexdigest()
        return self._dir / f"{digest}.json"

    def _read_sync(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            expires_at = data.get("expires_at")
            if expires_at is not None and time.time() > expires_at:
                path.unlink(missing_ok=True)
                return None
            return data
        except Exception:
            return None

    def _write_sync(self, path: Path, value: Any, ttl: int | None) -> None:
        entry: dict[str, Any] = {
            "value": value,
            "expires_at": time.time() + ttl if ttl is not None else None,
        }
        path.write_text(json.dumps(entry), encoding="utf-8")

    async def get(self, collection: str, key: str) -> Any | None:
        data = await asyncio.to_thread(self._read_sync, self._path(collection, key))
        return data["value"] if data is not None else None

    async def put(
        self, collection: str, key: str, value: Any, *, ttl: int | None = None
    ) -> None:
        await asyncio.to_thread(self._write_sync, self._path(collection, key), value, ttl)

    async def delete(self, collection: str, key: str) -> bool:
        path = self._path(collection, key)

        def _delete() -> bool:
            if path.exists():
                path.unlink()
                return True
            return False

        return await asyncio.to_thread(_delete)

    async def ttl(self, collection: str, key: str) -> int | None:
        data = await asyncio.to_thread(self._read_sync, self._path(collection, key))
        if data is None or data.get("expires_at") is None:
            return None
        return max(0, int(data["expires_at"] - time.time()))

    async def get_many(self, collection: str, keys: list[str]) -> dict[str, Any]:
        result = {}
        for key in keys:
            val = await self.get(collection, key)
            if val is not None:
                result[key] = val
        return result

    async def put_many(
        self,
        collection: str,
        items: dict[str, Any],
        *,
        ttl: int | None = None,
    ) -> None:
        for key, value in items.items():
            await self.put(collection, key, value, ttl=ttl)

    async def delete_many(self, collection: str, keys: list[str]) -> int:
        count = 0
        for key in keys:
            if await self.delete(collection, key):
                count += 1
        return count

    async def ttl_many(
        self, collection: str, keys: list[str]
    ) -> dict[str, int | None]:
        return {key: await self.ttl(collection, key) for key in keys}


def create_file_store() -> _SimpleFileStore:
    for storage_dir in (_PERSISTENT_DIR, _FALLBACK_DIR):
        try:
            storage_dir.mkdir(parents=True, exist_ok=True)
            if storage_dir == _FALLBACK_DIR:
                _logger.warning(
                    "OAuth proxy storage: cannot write to %s (permission denied). "
                    "Falling back to %s — tokens will not persist across pod restarts. "
                    "Fix: set securityContext.fsGroup to the GID of the app user.",
                    _PERSISTENT_DIR,
                    _FALLBACK_DIR,
                )
            return _SimpleFileStore(storage_dir)
        except PermissionError:
            continue
    # Both paths failed — /tmp is always writable so this should never happen.
    raise RuntimeError(
        f"Cannot create OAuth storage directory at {_PERSISTENT_DIR} or {_FALLBACK_DIR}"
    )
