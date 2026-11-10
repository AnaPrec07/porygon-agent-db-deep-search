"""Debounced trigger for the refresh job (plan §G.4). A failed trigger never fails a research run."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class RefreshTrigger:
    def __init__(
        self,
        workspace: Any,
        job_id: int,
        *,
        debounce_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._w = workspace
        self._job_id = job_id
        self._debounce = debounce_s
        self._clock = clock
        self._last: float | None = None

    def __call__(self) -> bool:
        now = self._clock()
        if self._last is not None and now - self._last < self._debounce:
            return False
        try:
            self._w.jobs.run_now(job_id=self._job_id)
        except Exception as error:  # noqa: BLE001 - durable records are already written; the schedule catches up
            logger.warning("could not start refresh job %s: %s", self._job_id, error)
            return False
        self._last = now
        return True
