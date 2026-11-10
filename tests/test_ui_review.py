"""PR-9 tests: plan-review helpers (run under Node), page wiring, and the adapter's research events."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.types import Command

from runtime.adapter import _agent_input, _serialize_events

UI = Path(__file__).resolve().parents[1] / "ui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _node(expression: str):
    script = f"const r = require({json.dumps(str(UI / 'review.js'))}); console.log(JSON.stringify({expression}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# ---------- review helpers ----------


@needs_node
def test_plan_review_resume_payload_matches_the_graph_contract():
    interrupt = {"value": {"type": "plan_review", "plan": "* [RESEARCH] x"}}
    assert _node(f"r.resumePayload('approve', {json.dumps(interrupt)})") == {"resume": {"decision": "approve"}}
    assert _node(f"r.resumePayload('reject', {json.dumps(interrupt)})") == {"resume": {"decision": "cancel"}}


@needs_node
def test_tool_approval_keeps_the_template_contract():
    interrupt = {"value": {"action_requests": [{"name": "send_message"}]}}
    assert _node(f"r.resumePayload('approve', {json.dumps(interrupt)})") == {
        "resume": {"decisions": [{"type": "approve"}]}
    }


@needs_node
def test_plan_review_labels_and_plan_text():
    interrupt = {"value": {"type": "plan_review", "plan": "  * [RESEARCH] Analyze  "}}
    assert _node(f"r.approvalLabels({json.dumps(interrupt)})") == {"approve": "Approve plan", "reject": "Cancel research"}
    assert _node(f"r.planText({json.dumps(interrupt)})") == "* [RESEARCH] Analyze"
    assert _node("r.approvalLabels(null)") == {"approve": "Approve", "reject": "Reject"}


@needs_node
def test_timeline_labels_use_the_node_titles_and_mark_waiting():
    assert _node("r.timelineLabel({agent_name: 'section_researcher', status: 'completed'})") == "Researching"
    assert _node("r.timelineLabel({agent_name: 'plan_review', status: 'waiting'})") == "Waiting for plan approval (waiting)"
    assert _node("r.timelineLabel({agent_name: 'mystery_node'})") == "mystery_node"


# ---------- page wiring (static) ----------


def test_page_loads_the_helpers_before_the_app():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert html.index("review.js") < html.index("app.js")
    assert 'id="research-timeline"' in html and 'id="approval-panel"' in html


def test_app_references_only_elements_the_page_defines():
    html = (UI / "index.html").read_text(encoding="utf-8")
    app = (UI / "app.js").read_text(encoding="utf-8")
    for element_id in ("research-timeline", "approval-panel", "approve-action", "reject-action", "approval-summary"):
        assert f'id="{element_id}"' in html, element_id
    assert "DeepSearchReview.resumePayload" in app and "DeepSearchReview.approvalLabels" in app


@needs_node
def test_app_js_is_valid_javascript():
    result = subprocess.run([NODE, "--check", str(UI / "app.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_timeline_is_cleared_on_a_new_chat():
    app = (UI / "app.js").read_text(encoding="utf-8")
    clear = app[app.index("function clearChat("):]
    assert "#research-timeline" in clear[:300]


# ---------- adapter events ----------


class _Item:
    def __init__(self, value, id_):
        self.value, self.id = value, id_


async def _aiter(events):
    for event in events:
        yield event


async def _collect(stream):
    return [e async for e in _serialize_events(stream)]


@pytest.mark.asyncio
async def test_each_completed_node_emits_a_research_event_and_internal_keys_are_skipped():
    stream = _aiter([
        ("updates", {"intake": {"research_topic": "T"}}),
        ("updates", {"plan_generator": {"research_plan": "* [RESEARCH] x", "messages": []}}),
    ])
    events = await _collect(stream)
    research = [e for e in events if e["type"] == "research_event"]
    assert [e["agent_name"] for e in research] == ["intake", "plan_generator"]
    assert research[1]["title"] == "Drafting the research plan"
    assert all(e["status"] == "completed" for e in research)


@pytest.mark.asyncio
async def test_plan_pause_emits_waiting_then_the_interrupt():
    plan_value = {"type": "plan_review", "agent_name": "interactive_planner_agent", "plan": "* [RESEARCH] x"}
    stream = _aiter([("updates", {"__interrupt__": (_Item(plan_value, "int-1"),)})])
    events = await _collect(stream)
    assert [e["type"] for e in events] == ["research_event", "interrupt"]
    assert events[0] == {"type": "research_event", "agent_name": "plan_review",
                         "title": "Waiting for plan approval", "status": "waiting"}
    assert events[1]["value"] == plan_value


def test_ui_resume_payload_is_accepted_by_the_adapter():
    command = _agent_input({"resume": {"decision": "approve"}})
    assert isinstance(command, Command) and command.resume == {"decision": "approve"}
    cancel = _agent_input({"resume": {"decision": "cancel"}})
    assert cancel.resume == {"decision": "cancel"}


def test_node_titles_cover_every_graph_node():
    from agent.deep_search.graph import build_graph
    from agent.deep_search.deps import Deps
    from runtime.adapter import NODE_TITLES

    nodes = set(build_graph(Deps.stub()).nodes)
    missing = nodes - set(NODE_TITLES) - {"__start__"}
    assert not missing, missing
