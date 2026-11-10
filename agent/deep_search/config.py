"""Run configuration for the deep-search graph, read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class DeepSearchConfig:
    worker_model: str | None = None
    critic_model: str | None = None
    extraction_model: str | None = None
    max_search_iterations: int = 5

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DeepSearchConfig:
        source = os.environ if env is None else env
        raw_iterations = source.get("DEEP_SEARCH_MAX_SEARCH_ITERATIONS", "5")
        try:
            iterations = int(raw_iterations)
        except ValueError as e:
            raise ValueError(f"DEEP_SEARCH_MAX_SEARCH_ITERATIONS must be an integer, got {raw_iterations!r}") from e
        if iterations < 1:
            raise ValueError("DEEP_SEARCH_MAX_SEARCH_ITERATIONS must be at least 1")
        return cls(
            worker_model=source.get("DEEP_SEARCH_WORKER_MODEL") or None,
            critic_model=source.get("DEEP_SEARCH_CRITIC_MODEL") or None,
            extraction_model=source.get("DEEP_SEARCH_EXTRACTION_MODEL") or None,
            max_search_iterations=iterations,
        )
