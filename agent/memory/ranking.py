"""Eligibility, recency decay, deduplication and diversification for memory facts (plan §G.5 to §G.7).

Pure functions. Every rule that decides whether a remembered fact may influence new research lives here, so it
can be tested directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

QUALITY_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.0}
HALF_LIFE_DAYS: dict[str, float | None] = {"static": None, "slow": 365.0, "volatile": 30.0}
STALE_DECAY = 0.25
NEAR_DUPLICATE = 0.9
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Policy:
    min_quality: str = "medium"  # high | medium
    max_volatile_age_days: float = 60.0
    max_results_per_domain: int = 4
    top_k: int = 12


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def age_days(value: str | None, now: datetime) -> float | None:
    when = parse_time(value)
    return None if when is None else max(0.0, (now - when).total_seconds() / 86400.0)


def decay(age: float | None, sensitivity: str) -> float:
    half_life = HALF_LIFE_DAYS.get(sensitivity)
    if half_life is None or age is None:
        return 1.0
    return 0.5 ** (age / half_life)


def is_stale(fact: dict[str, Any], now: datetime, policy: Policy) -> bool:
    age = age_days(fact.get("last_verified_at"), now)
    if age is None:
        return False
    if fact.get("time_sensitivity") == "volatile" and age > policy.max_volatile_age_days:
        return True
    return decay(age, fact.get("time_sensitivity", "static")) < STALE_DECAY


def eligibility(fact: dict[str, Any], now: datetime, policy: Policy) -> str | None:
    """None when the fact may be used. Otherwise the reason it may not."""
    if fact.get("status") != "active":
        return "not_active"
    if fact.get("fact_kind", "source_fact") != "source_fact":
        return "not_source_fact"
    allowed = {"high"} if policy.min_quality == "high" else {"high", "medium"}
    if fact.get("evidence_quality") not in allowed:
        return "quality_below_policy"
    if is_stale(fact, now, policy):
        return "stale"
    return None


def combined_score(fact: dict[str, Any], retrieval_score: float, now: datetime) -> float:
    quality = QUALITY_WEIGHT.get(fact.get("evidence_quality", ""), 0.0)
    return retrieval_score * quality * decay(age_days(fact.get("last_verified_at"), now), fact.get("time_sensitivity", "static"))


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def jaccard(a: str, b: str) -> float:
    left, right = _words(a), _words(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def dedupe(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per canonical key, then drop near-duplicate texts. Input order is preserved for the survivors."""
    best: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = item["canonical_fact_key"]
        if key not in best or item["score"] > best[key]["score"]:
            best[key] = item
    kept: list[dict[str, Any]] = []
    for item in sorted(best.values(), key=lambda c: c["score"], reverse=True):
        if any(jaccard(item["fact_text"], other["fact_text"]) >= NEAR_DUPLICATE for other in kept):
            continue
        kept.append(item)
    return kept


def domain_of(url: str | None) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def diversify(candidates: list[dict[str, Any]], max_per_domain: int) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    chosen: list[dict[str, Any]] = []
    for item in sorted(candidates, key=lambda c: c["score"], reverse=True):
        domain = domain_of(item.get("source_url"))
        if counts.get(domain, 0) >= max_per_domain:
            continue
        counts[domain] = counts.get(domain, 0) + 1
        chosen.append(item)
    return chosen
