"""Object stores for record files.

Writes are create-only: an existing path is never overwritten. ``UCVolumeObjectStore`` uses the Files API with
``overwrite=False`` against a Unity Catalog external volume, which is backed by S3 (plan §ADR-04). The app
never needs AWS credentials for this. ``LocalObjectStore`` gives the same semantics for development and tests.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Protocol

from databricks.sdk.errors import DatabricksError


class CreateConflict(Exception):
    """The path already exists."""


class ObjectStore(Protocol):
    def create(self, path: str, data: bytes) -> None: ...

    def read(self, path: str) -> bytes: ...

    def list_files(self, prefix: str) -> list[str]: ...

    def delete(self, path: str) -> None: ...


class LocalObjectStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def create(self, path: str, data: bytes) -> None:
        target = self._root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as error:
            raise CreateConflict(path) from error
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)

    def read(self, path: str) -> bytes:
        return (self._root / path).read_bytes()

    def delete(self, path: str) -> None:
        (self._root / path).unlink(missing_ok=True)

    def list_files(self, prefix: str) -> list[str]:
        base = self._root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self._root)).replace(os.sep, "/") for p in base.rglob("*") if p.is_file())


class UCVolumeObjectStore:
    """Files API on a Unity Catalog volume, for example ``/Volumes/deep_search/landing/records``."""

    def __init__(self, workspace: Any, volume_root: str) -> None:
        self._w = workspace
        self._root = volume_root.rstrip("/")

    def _full(self, path: str) -> str:
        return f"{self._root}/{path.strip('/')}"

    def create(self, path: str, data: bytes) -> None:
        try:
            self._w.files.upload(self._full(path), io.BytesIO(data), overwrite=False)
        except DatabricksError as error:
            if getattr(error, "status_code", None) == 409 or type(error).__name__ in ("AlreadyExists", "ResourceAlreadyExists"):
                raise CreateConflict(path) from error
            raise

    def read(self, path: str) -> bytes:
        return self._w.files.download(self._full(path)).contents.read()

    def delete(self, path: str) -> None:
        self._w.files.delete(self._full(path))

    def list_files(self, prefix: str) -> list[str]:
        found: list[str] = []
        stack = [self._full(prefix)]
        while stack:
            directory = stack.pop()
            try:
                entries = list(self._w.files.list_directory_contents(directory))
            except DatabricksError:
                continue
            for entry in entries:
                if entry.is_directory:
                    stack.append(entry.path)
                else:
                    found.append(entry.path[len(self._root) + 1 :])
        return sorted(found)
