"""Source registry: canonical URLs, stable ``src-N`` ids within a session, and provenance per source."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from agent.deep_search.research.providers import SearchHit

_DEFAULT_PORTS = {"http": 80, "https": 443}
_TRACKING_PARAMS = {"gclid", "fbclid", "mc_cid", "mc_eid"}


def canonical_url(url: str) -> str:
    """Normalize a URL for deduplication: lowercase scheme and host, default port removed, fragment removed,
    tracking parameters removed, query sorted, trailing slash removed except at the root."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port in (None, _DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
        )
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def content_key(text: str, url: str) -> str:
    """Key used to deduplicate a fact across sessions and within one session."""
    normalized = " ".join(text.split()).casefold()
    return hashlib.sha256(f"{normalized}|{canonical_url(url)}".encode()).hexdigest()


def register_hits(
    sources: dict[str, dict[str, Any]],
    url_to_id: dict[str, str],
    hits: list[SearchHit],
    query: str,
) -> list[tuple[str, str]]:
    """Add hits to the registry. Returns (short_id, url) for every hit, in order. Mutates both dicts."""
    registered: list[tuple[str, str]] = []
    for hit in hits:
        key = canonical_url(hit.url)
        if key in url_to_id:
            short_id = url_to_id[key]
            query_ids = sources[short_id]["queries"]
            if query not in query_ids:
                query_ids.append(query)
        else:
            short_id = f"src-{len(url_to_id) + 1}"
            url_to_id[key] = short_id
            domain = domain_of(hit.url)
            sources[short_id] = {
                "short_id": short_id,
                "title": hit.title or domain,
                "url": hit.url,
                "canonical_url": key,
                "domain": domain,
                "published_at": hit.published_at,
                "snippet": hit.snippet[:500],
                "origin": "web",
                "queries": [query],
            }
        registered.append((short_id, hit.url))
    return registered
