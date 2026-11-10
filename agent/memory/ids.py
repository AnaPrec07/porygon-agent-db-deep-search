"""Deterministic identifiers. Replaying a node must produce the same ids and file paths."""

from __future__ import annotations

import hashlib
import uuid

DEEP_SEARCH_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "deep-search/v1")


def stable_id(*parts: str) -> str:
    return str(uuid.uuid5(DEEP_SEARCH_NAMESPACE, "\x1f".join(parts)))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
