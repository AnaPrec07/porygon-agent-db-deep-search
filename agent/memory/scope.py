"""Access scope for persisted research (plan §ADR-21).

``private`` records are visible to the actor who created them. ``tenant`` records are visible to everyone in the
tenant. Each record stores one ``access_scope`` string, and retrieval filters on the caller's two values:
``tenant:<tenant_id>`` and ``actor:<actor_id>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Visibility = Literal["private", "tenant"]
VISIBILITIES: tuple[str, ...] = ("private", "tenant")


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    actor_id: str
    visibility: Visibility = "private"

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant_id is required")
        if not self.actor_id:
            raise ValueError("actor_id is required")
        if self.visibility not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {VISIBILITIES}, got {self.visibility!r}")

    @property
    def access_scope(self) -> str:
        return f"tenant:{self.tenant_id}" if self.visibility == "tenant" else f"actor:{self.actor_id}"

    def fields(self) -> dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "visibility": self.visibility,
            "access_scope": self.access_scope,
        }

    def filter_values(self) -> list[str]:
        """Values a reader in this scope may see: the tenant's shared records and this actor's private ones."""
        return [f"tenant:{self.tenant_id}", f"actor:{self.actor_id}"]
