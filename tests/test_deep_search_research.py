"""PR-5 tests: search providers, quote verification, source registry, and the research pipeline.

Hermetic: fake search provider, httpx MockTransport for Tavily HTTP, fake chat model. No network.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import agent as agent_module
from agent.deep_search.citations import replace_citations
from agent.deep_search.config import DeepSearchConfig
from agent.deep_search.deps import Deps
from agent.deep_search.graph import build_graph
from agent.deep_search.llm import StructuredOutputError
from agent.deep_search.research.evidence import normalize_text, verify_quote
from agent.deep_search.research.pipeline import ResearchServices, parse_plan_goals
from agent.deep_search.research.providers import (
    FakeSearchProvider,
    SearchError,
    SearchHit,
    TavilyHttpProvider,
    TavilyMcpProvider,
)
from agent.deep_search.research.sources import canonical_url, register_hits
from tests.fakes import FakeChat

PAGE_A = (
    "Battery storage prices fell sharply in 2024. Installers in Texas reported steady demand for "
    "grid-scale batteries, with system costs near 200 dollars per kilowatt hour."
)
PAGE_B = "Lithium supply remains concentrated.  Three producers control most\nof the refined output."


def _plan_dict() -> dict:
    return {
        "goals": [
            {"task_type": "RESEARCH", "tags": [], "text": "Analyze battery costs"},
            {"task_type": "RESEARCH", "tags": [], "text": "Identify lithium supply risks"},
            {"task_type": "DELIVERABLE", "tags": ["IMPLIED"], "text": "Create a short summary"},
        ]
    }


def _outline_dict() -> dict:
    return {"sections": [{"title": f"Part {i}", "overview": f"Overview {i}."} for i in range(1, 5)]}


def _extract(*facts: dict) -> dict:
    return {"facts": list(facts)}


PASS = {"grade": "pass", "comment": "Adequate.", "follow_up_queries": None}


def _config(**overrides) -> DeepSearchConfig:
    return replace(DeepSearchConfig(), **overrides)


def _run(deps: Deps, services: ResearchServices | None = None) -> dict:
    graph = build_graph(deps).compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"messages": [{"role": "user", "content": "Grid storage"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    return graph.get_state(cfg).values


# ---------- plan goal ids ----------


def test_parse_plan_goals_assigns_stable_ids():
    plan = (
        "* [RESEARCH] Analyze costs\n"
        "* [RESEARCH][MODIFIED] Identify risks\n"
        "* [DELIVERABLE][IMPLIED] Create a table\n"
        "* [RESEARCH][NEW] Check policy"
    )
    goals = parse_plan_goals(plan)
    assert [(g.id, g.task_type, g.text) for g in goals] == [
        ("g1", "RESEARCH", "Analyze costs"),
        ("g2", "RESEARCH", "Identify risks"),
        ("d1", "DELIVERABLE", "Create a table"),
        ("g3", "RESEARCH", "Check policy"),
    ]


# ---------- URLs and sources ----------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("HTTPS://Example.COM:443/Path/", "https://example.com/Path"),
        ("https://example.com/a?utm_source=x&b=2&a=1#frag", "https://example.com/a?a=1&b=2"),
        ("http://example.com:8080/", "http://example.com:8080/"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com/p?gclid=abc", "https://example.com/p"),
    ],
)
def test_canonical_url(url, expected):
    assert canonical_url(url) == expected


def test_register_hits_keeps_ids_stable_and_accumulates_queries():
    sources: dict = {}
    url_map: dict = {}
    first = register_hits(sources, url_map, [SearchHit("https://a.example/x", "Alpha", "s")], "q1")
    second = register_hits(
        sources,
        url_map,
        [SearchHit("https://A.example/x?utm_medium=y", "Alpha again", "s"), SearchHit("https://b.example", "", "s")],
        "q2",
    )
    assert first == [("src-1", "https://a.example/x")]
    assert second[0][0] == "src-1"
    assert second[1][0] == "src-2"
    assert sources["src-1"]["queries"] == ["q1", "q2"]
    assert sources["src-2"]["title"] == "b.example"  # domain used when the title is empty
    assert sources["src-2"]["domain"] == "b.example"


# ---------- quote verification ----------


def test_verify_exact_quote():
    check = verify_quote("costs near 200 dollars", PAGE_A)
    assert (check.verified, check.method) == (True, "exact")


def test_verify_normalized_quote_ignores_case_spacing_and_nbsp():
    check = verify_quote("LITHIUM SUPPLY REMAINS CONCENTRATED. Three producers control most of the refined output.", PAGE_B)
    assert (check.verified, check.method) == (True, "normalized")


def test_verify_fuzzy_quote_tolerates_a_small_edit():
    # One word changed ("reported" -> "noted"); the quote is otherwise verbatim.
    check = verify_quote(
        "Installers in Texas noted steady demand for grid-scale batteries, with system costs near 200 dollars per kilowatt hour",
        PAGE_A,
    )
    assert check.verified and check.method == "fuzzy" and check.score >= 0.9


def test_fabricated_quote_is_not_verified():
    check = verify_quote("Prices fell to 10 dollars per kilowatt hour in 2030.", PAGE_A)
    assert not check.verified and check.method == "none"


def test_empty_quote_is_not_verified():
    assert not verify_quote("   ", PAGE_A).verified
    assert normalize_text("  A B  ") == "a b"


# ---------- providers ----------


def test_tavily_http_parses_search_and_extract():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"url": "https://a.example", "title": "A", "content": "snip", "score": 0.7}, {"title": "no url"}]})
        return httpx.Response(200, json={"results": [{"url": "https://a.example", "raw_content": PAGE_A}, {"url": "https://x.example", "raw_content": ""}]})

    provider = TavilyHttpProvider("k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    hits = provider.search("q", max_results=3)
    pages = provider.extract(["https://a.example"])
    assert [h.url for h in hits] == ["https://a.example"] and hits[0].snippet == "snip"
    assert [p.url for p in pages] == ["https://a.example"]
    assert seen[0].headers["Authorization"] == "Bearer k"


def test_tavily_http_retries_rate_limits_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"results": []})

    provider = TavilyHttpProvider(
        "k", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append
    )
    assert provider.search("q", max_results=1) == []
    assert calls["n"] == 2 and sleeps == [1]


def test_tavily_http_does_not_retry_client_errors():
    provider = TavilyHttpProvider(
        "k", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400))), sleep=lambda s: None
    )
    with pytest.raises(SearchError, match="HTTP 400"):
        provider.search("q", max_results=1)


def _mcp_result(payload: str, *, error: bool = False):
    return SimpleNamespace(isError=error, content=[SimpleNamespace(type="text", text=payload)])


class _FakeMcp:
    def __init__(self, response):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_tavily_mcp_parses_json_text_and_uses_configured_tool_names():
    client = _FakeMcp(_mcp_result('{"results": [{"url": "https://a.example", "title": "A", "content": "s"}]}'))
    provider = TavilyMcpProvider(client, search_tool="my_search", extract_tool="my_extract")
    hits = provider.search("battery", max_results=2)
    assert hits[0].url == "https://a.example"
    assert client.calls == [("my_search", {"query": "battery", "max_results": 2})]


def test_tavily_mcp_reports_tool_errors_and_bad_payloads():
    with pytest.raises(SearchError, match="returned an error"):
        TavilyMcpProvider(_FakeMcp(_mcp_result("{}", error=True))).search("q", max_results=1)
    with pytest.raises(SearchError, match="non-JSON"):
        TavilyMcpProvider(_FakeMcp(_mcp_result("not json"))).search("q", max_results=1)
    with pytest.raises(SearchError, match="failed"):
        TavilyMcpProvider(_FakeMcp(RuntimeError("connection reset"))).search("q", max_results=1)


# ---------- pipeline through the graph ----------


def _services(search: FakeSearchProvider, chat: FakeChat, config: DeepSearchConfig) -> ResearchServices:
    return ResearchServices(search=search, query_llm=chat, extract_llm=chat, deliver_llm=chat, config=config)


def _search_fixture() -> FakeSearchProvider:
    a = SearchHit("https://news.example/batteries", "Battery prices", "")
    b = SearchHit("https://lithium.example/supply", "Lithium supply", "")
    return FakeSearchProvider(
        hits={"battery costs": [a], "lithium risks": [a, b]},
        pages={"https://news.example/batteries": PAGE_A, "https://lithium.example/supply": PAGE_B},
    )


def _queries(*pairs) -> dict:
    return {"goals": [{"goal_id": gid, "queries": [{"search_query": q}]} for gid, q in pairs]}


def test_pipeline_builds_verified_facts_findings_and_sources():
    config = _config()
    search = _search_fixture()
    chat = FakeChat(
        structured_results=[
            _plan_dict(),
            _outline_dict(),
            _queries(("g1", "battery costs"), ("g2", "lithium risks")),
            _extract(
                {"goal_id": "g1", "fact_text": "System costs were near 200 dollars per kilowatt hour.",
                 "quote": "system costs near 200 dollars per kilowatt hour", "time_sensitivity": "volatile"},
                {"goal_id": "g1", "fact_text": "Prices fell to 10 dollars.",
                 "quote": "Prices fell to 10 dollars per kilowatt hour in 2030."},
            ),
            _extract(
                {"goal_id": "g2", "fact_text": "Three producers control most refined lithium.",
                 "quote": "Three producers control most of the refined output."},
            ),
            PASS,
        ],
        texts=[
            "Deliverable: a short summary.",
            "Summary: batteries are cheap <cite source=\"src-1\" /> and lithium is concentrated <cite source=\"src-2\" />"
            " <cite source=\"src-9\" />.",
        ],
    )
    state = _run(Deps.from_models(config, worker=chat, critic=chat, services=_services(search, chat, config)))

    assert search.queries == ["battery costs", "lithium risks"]
    assert [f["source_id"] for f in state["facts"]] == ["src-1", "src-2"]
    assert state["facts"][0]["quote_match"] == "exact"
    assert state["research_stats"]["unverified_quotes_dropped"] == 1
    assert '<cite source="src-1" />' in state["section_research_findings"]
    assert "## Analyze battery costs" in state["section_research_findings"]
    assert "No verified facts" not in state["section_research_findings"].split("## Identify")[0]
    assert set(state["sources"]) == {"src-1", "src-2"}
    assert state["budget"] == {"searches": 2, "extracts": 2}
    assert "[Battery prices](https://news.example/batteries)" in state["final_report_with_citations"]
    assert state["citation_stats"] == {"total": 3, "valid": 2, "dropped": 1}


def test_refinement_merges_follow_up_facts_and_keeps_earlier_ones():
    config = _config()
    search = _search_fixture()
    search._hits["follow-up question"] = [SearchHit("https://c.example/tariffs", "Tariffs", "")]
    search._pages["https://c.example/tariffs"] = "New tariffs add 25 percent to imported cells in 2025."
    follow_up = {"grade": "fail", "comment": "Needs tariffs.", "follow_up_queries": [{"search_query": "follow-up question"}]}
    chat = FakeChat(
        structured_results=[
            _plan_dict(),
            _outline_dict(),
            _queries(("g1", "battery costs"), ("g2", "lithium risks")),
            _extract({"goal_id": "g1", "fact_text": "System costs were near 200 dollars per kilowatt hour.",
                      "quote": "system costs near 200 dollars per kilowatt hour"}),
            _extract({"goal_id": "g2", "fact_text": "Three producers control most refined lithium.",
                      "quote": "Three producers control most of the refined output."}),
            follow_up,
            _extract({"goal_id": "g1", "fact_text": "New tariffs add 25 percent to imported cells.",
                      "quote": "New tariffs add 25 percent to imported cells in 2025."}),
            PASS,
        ],
        texts=["Deliverable one.", "Deliverable two.", "Final report."],
    )
    state = _run(Deps.from_models(config, worker=chat, critic=chat, services=_services(search, chat, config)))

    assert [f["source_id"] for f in state["facts"]] == ["src-1", "src-2", "src-3"]
    assert "New tariffs add 25 percent" in state["section_research_findings"]
    assert "Three producers control" in state["section_research_findings"]
    assert state["budget"]["searches"] == 3
    assert state["final_report_with_citations"] == "Final report."


def test_search_budget_stops_further_searches():
    config = _config(max_total_searches=1)
    search = _search_fixture()
    chat = FakeChat(
        structured_results=[
            _plan_dict(), _outline_dict(),
            _queries(("g1", "battery costs"), ("g2", "lithium risks")),
            _extract({"goal_id": "g1", "fact_text": "System costs were near 200 dollars per kilowatt hour.",
                      "quote": "system costs near 200 dollars per kilowatt hour"}),
            PASS,
        ],
        texts=["Deliverable.", "Report."],
    )
    state = _run(Deps.from_models(config, worker=chat, critic=chat, services=_services(search, chat, config)))
    assert search.queries == ["battery costs"]
    assert any(e["message"] == "search budget exhausted" for e in state["errors"])


def test_failed_search_is_recorded_and_the_run_continues():
    config = _config()
    search = _search_fixture()
    search._fail = {"battery costs"}
    chat = FakeChat(
        structured_results=[
            _plan_dict(), _outline_dict(),
            _queries(("g1", "battery costs"), ("g2", "lithium risks")),
            _extract({"goal_id": "g2", "fact_text": "Three producers control most refined lithium.",
                      "quote": "Three producers control most of the refined output."}),
            _extract({"goal_id": "g2", "fact_text": "Three producers control most refined lithium.",
                      "quote": "Three producers control most of the refined output."}),
            PASS,
        ],
        texts=["Deliverable.", "Report."],
    )
    state = _run(Deps.from_models(config, worker=chat, critic=chat, services=_services(search, chat, config)))
    assert any(e["stage"] == "search" and "battery costs" in e["query"] for e in state["errors"])
    assert state["phase"] == "done"
    assert len(state["facts"]) == 1  # the duplicate from page B is not stored twice


def test_repeated_extraction_failure_is_recorded_not_raised():
    config = _config()
    search = _search_fixture()
    bad = {"facts": [{"nonsense": True}]}
    chat = FakeChat(
        structured_results=[
            _plan_dict(), _outline_dict(),
            _queries(("g1", "battery costs")),
            bad, bad, bad,  # three invalid answers exhaust the retries
            PASS,
        ],
        texts=["Deliverable.", "Report."],
    )
    state = _run(Deps.from_models(config, worker=chat, critic=chat, services=_services(search, chat, config)))
    assert any(e["stage"] == "extract" and "StructuredOutputError" not in e["message"] for e in state["errors"])
    assert state["facts"] == []
    assert state["phase"] == "done"


def test_findings_render_unknown_goal_ids_under_additional_findings():
    from agent.deep_search.research.pipeline import render_findings

    goals = parse_plan_goals("* [RESEARCH] Only goal")
    facts = [{"goal_id": "general", "status": "active", "fact_text": "A fact.", "source_id": "src-1"}]
    text = render_findings(goals, facts)
    assert "## Additional findings" in text
    assert '- A fact. <cite source="src-1" />' in text


def test_replace_citations_drops_unknown_ids_from_pipeline_output():
    text, stats = replace_citations('x<cite source="src-5" />', {})
    assert stats["dropped"] == 1 and "src-5" not in text


# ---------- wiring and configuration ----------


def test_search_provider_config_is_validated(monkeypatch):
    monkeypatch.setenv("DEEP_SEARCH_SEARCH_PROVIDER", "tavily_mcp")
    monkeypatch.delenv("DEEP_SEARCH_TAVILY_MCP_URL", raising=False)
    with pytest.raises(ValueError, match="DEEP_SEARCH_TAVILY_MCP_URL"):
        agent_module._build_search_provider(DeepSearchConfig.from_env(), workspace=object())

    monkeypatch.setenv("DEEP_SEARCH_SEARCH_PROVIDER", "tavily_http")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        agent_module._build_search_provider(DeepSearchConfig.from_env(), workspace=object())


def test_unknown_search_provider_is_rejected():
    with pytest.raises(ValueError, match="DEEP_SEARCH_SEARCH_PROVIDER"):
        DeepSearchConfig.from_env({"DEEP_SEARCH_SEARCH_PROVIDER": "google"})


@pytest.mark.asyncio
async def test_create_agent_graph_warns_when_search_is_not_configured(monkeypatch, caplog):
    monkeypatch.setenv("DEEP_SEARCH_WORKER_MODEL", "system.ai.test")
    monkeypatch.delenv("DEEP_SEARCH_SEARCH_PROVIDER", raising=False)
    monkeypatch.setattr(agent_module, "_RoutedChatDatabricks", lambda **kw: FakeChat())
    monkeypatch.setattr(agent_module, "_app_workspace_client", lambda: object())
    await agent_module.create_agent_graph("actor-1")
    assert "research and refinement use stub nodes" in caplog.text
