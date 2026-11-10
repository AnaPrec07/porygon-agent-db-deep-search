"""Search and extraction providers.

``TavilyMcpProvider`` calls Tavily through a Databricks external MCP connection (plan §ADR-03). Tool names and
argument names are configurable because they are NOT verified yet (plan assumption A2): PR-1's
``verify_platform.py`` records them from a live workspace. ``TavilyHttpProvider`` is the fallback that calls the
Tavily REST API with a key from a secret resource. ``FakeSearchProvider`` serves fixtures in tests.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class SearchError(RuntimeError):
    """A provider call failed. The pipeline records it and continues with the remaining queries."""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    published_at: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class ExtractedPage:
    url: str
    text: str


class SearchProvider(Protocol):
    def search(self, query: str, *, max_results: int) -> list[SearchHit]: ...

    def extract(self, urls: Sequence[str]) -> list[ExtractedPage]: ...


def parse_search_results(data: Mapping[str, Any]) -> list[SearchHit]:
    """Map a Tavily-style ``{"results": [...]}`` payload to hits. Items without a URL are skipped."""
    hits: list[SearchHit] = []
    for item in data.get("results") or []:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        hits.append(
            SearchHit(
                url=url,
                title=str(item.get("title") or "").strip(),
                snippet=str(item.get("content") or item.get("snippet") or "").strip(),
                published_at=item.get("published_date") or item.get("published_at"),
                score=float(item["score"]) if isinstance(item.get("score"), int | float) else None,
            )
        )
    return hits


def parse_extract_results(data: Mapping[str, Any]) -> list[ExtractedPage]:
    """Map a Tavily-style extract payload to pages. Empty pages are dropped."""
    pages: list[ExtractedPage] = []
    for item in data.get("results") or []:
        url = str(item.get("url") or "").strip()
        text = str(item.get("raw_content") or item.get("content") or "").strip()
        if url and text:
            pages.append(ExtractedPage(url=url, text=text))
    return pages


class FakeSearchProvider:
    """Deterministic provider for tests. Hits and pages are fixtures keyed by query and URL."""

    def __init__(
        self,
        hits: Mapping[str, list[SearchHit]] | None = None,
        pages: Mapping[str, str] | None = None,
        fail_queries: set[str] | None = None,
    ) -> None:
        self._hits = dict(hits or {})
        self._pages = dict(pages or {})
        self._fail = set(fail_queries or ())
        self.queries: list[str] = []
        self.extracted: list[str] = []

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        self.queries.append(query)
        if query in self._fail:
            raise SearchError(f"fake failure for {query!r}")
        return list(self._hits.get(query, []))[:max_results]

    def extract(self, urls: Sequence[str]) -> list[ExtractedPage]:
        self.extracted.extend(urls)
        return [ExtractedPage(url=u, text=self._pages[u]) for u in urls if u in self._pages]


class TavilyHttpProvider:
    """Tavily REST API. Retries 429 and 5xx responses twice with exponential backoff."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.tavily.com",
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=httpx.Timeout(45.0))
        self._sleep = sleep

    def _post(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        last: str = ""
        for attempt in range(3):
            try:
                response = self._client.post(
                    f"{self._base}{path}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._key}"},
                    timeout=timeout,
                )
            except httpx.HTTPError as error:
                last = f"{type(error).__name__}: {error}"
            else:
                if response.status_code < 400:
                    return response.json()
                last = f"HTTP {response.status_code}"
                if response.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < 2:
                self._sleep(2**attempt)
        raise SearchError(f"Tavily {path} failed: {last}")

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        data = self._post("/search", {"query": query, "max_results": max_results}, timeout=20.0)
        return parse_search_results(data)

    def extract(self, urls: Sequence[str]) -> list[ExtractedPage]:
        data = self._post("/extract", {"urls": list(urls)}, timeout=45.0)
        return parse_extract_results(data)


class TavilyMcpProvider:
    """Tavily tools through a Databricks external MCP connection.

    ``client`` is a ``databricks_mcp.DatabricksMCPClient`` (or any object with ``call_tool(name, args)``).
    """

    def __init__(
        self,
        client: Any,
        *,
        search_tool: str = "tavily_search",
        extract_tool: str = "tavily_extract",
    ) -> None:
        self._client = client
        self._search_tool = search_tool
        self._extract_tool = extract_tool

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._client.call_tool(name, arguments)
        except Exception as error:  # noqa: BLE001 - transport errors are provider failures
            raise SearchError(f"MCP tool {name} failed: {type(error).__name__}: {error}") from error
        if getattr(result, "isError", False):
            raise SearchError(f"MCP tool {name} returned an error")
        text = "".join(
            str(getattr(block, "text", "")) for block in getattr(result, "content", []) if getattr(block, "type", "") == "text"
        )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise SearchError(f"MCP tool {name} returned non-JSON text") from error
        if not isinstance(data, dict):
            raise SearchError(f"MCP tool {name} returned a non-object payload")
        return data

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        return parse_search_results(self._call(self._search_tool, {"query": query, "max_results": max_results}))

    def extract(self, urls: Sequence[str]) -> list[ExtractedPage]:
        return parse_extract_results(self._call(self._extract_tool, {"urls": list(urls)}))
