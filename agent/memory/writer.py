"""Idempotent, create-only writes with bounded retries.

Rules (plan §F.4):
- A write that already exists with the same bytes succeeds (replay after a crash).
- A write that exists with different bytes goes to ``-r1``, ``-r2`` ... so no record is ever overwritten.
- Transient store errors are retried with backoff. Exhausted retries raise ``PersistenceError``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel

from agent.memory.ids import sha256_hex
from agent.memory.object_store import CreateConflict, ObjectStore

logger = logging.getLogger(__name__)
MAX_CONFLICT_SUFFIX = 3


class PersistenceError(RuntimeError):
    """A record could not be written after retries."""


@dataclass(frozen=True)
class WrittenFile:
    path: str
    record_type: str
    records: int
    sha256: str
    bytes: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _with_suffix(path: str, suffix: str) -> str:
    stem, dot, ext = path.rpartition(".")
    return f"{stem}{suffix}.{ext}" if dot else f"{path}{suffix}"


class PersistenceWriter:
    def __init__(
        self,
        store: ObjectStore,
        *,
        retries: int = 5,
        base_delay: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._store = store
        self._retries = retries
        self._base_delay = base_delay
        self._sleep = sleep

    def write_bytes(self, path: str, record_type: str, data: bytes, records: int = 1) -> WrittenFile:
        digest = sha256_hex(data)
        for suffix in range(MAX_CONFLICT_SUFFIX + 1):
            candidate = path if suffix == 0 else _with_suffix(path, f"-r{suffix}")
            if self._create(candidate, data, digest):
                return WrittenFile(candidate, record_type, records, digest, len(data))
        raise PersistenceError(f"{path}: content conflicts exist for {MAX_CONFLICT_SUFFIX} suffixes")

    def write_json(self, path: str, record_type: str, model: BaseModel | dict[str, Any]) -> WrittenFile:
        payload = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
        return self.write_bytes(path, record_type, _json_bytes(payload), records=1)

    def write_jsonl(self, path: str, record_type: str, rows: list[BaseModel]) -> WrittenFile:
        lines = [_json_bytes(row.model_dump(mode="json")) for row in rows]
        return self.write_bytes(path, record_type, b"\n".join(lines) + b"\n", records=len(rows))

    def _create(self, path: str, data: bytes, digest: str) -> bool:
        """True when the file now holds ``data``. False when a different file already holds the path."""
        attempt = 0
        while True:
            try:
                self._store.create(path, data)
                return True
            except CreateConflict:
                try:
                    existing = self._store.read(path)
                except Exception as error:  # noqa: BLE001 - treated as a transient store error
                    last = error
                else:
                    return sha256_hex(existing) == digest or False
                if not self._retry(attempt, last, path):
                    raise PersistenceError(f"{path}: could not read existing file") from last
            except Exception as error:  # noqa: BLE001 - transient store errors are retried
                if not self._retry(attempt, error, path):
                    raise PersistenceError(f"{path}: write failed after {self._retries} retries: {error}") from error
            attempt += 1

    def _retry(self, attempt: int, error: Exception, path: str) -> bool:
        if attempt >= self._retries:
            return False
        delay = self._base_delay * (2**attempt)
        logger.warning("persistence retry %d for %s after %s", attempt + 1, path, type(error).__name__)
        self._sleep(delay)
        return True
