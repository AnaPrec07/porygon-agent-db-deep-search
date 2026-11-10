"""PR-6 tests: record writing, idempotency, manifests, failure handling, and reconstruction.

Hermetic: local object store in a temp directory, a fake Unity Catalog workspace, and the stub graph.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from databricks.sdk.errors import AlreadyExists
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.deep_search.config import DeepSearchConfig
from agent.deep_search.deps import Deps
from agent.deep_search.graph import build_graph
from agent.memory.ids import sha256_hex, stable_id
from agent.memory.object_store import CreateConflict, LocalObjectStore, UCVolumeObjectStore
from agent.memory.reconstruct import ReconstructError, reconstruct, write_outputs
from agent.memory.recorder import Persistence
from agent.memory.redaction import redact_text
from agent.memory.records import TraceEventRecord
from agent.memory.writer import PersistenceError, PersistenceWriter


def _writer(store, retries: int = 1) -> PersistenceWriter:
    return PersistenceWriter(store, retries=retries, base_delay=0, sleep=lambda s: None)


def _graph(deps: Deps, store, *, strict: bool = True):
    persistence = Persistence(_writer(store), strict=strict)
    return build_graph(deps, persistence=persistence).compile(checkpointer=InMemorySaver())


def _thread() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


def _stub_deps(**overrides) -> Deps:
    return replace(Deps.stub(DeepSearchConfig()), **overrides)


def _session_id(graph, cfg) -> str:
    return graph.get_state(cfg).values["research_session_id"]


# ---------- ids, redaction, store semantics ----------


def test_ids_are_deterministic():
    assert stable_id("a", "b") == stable_id("a", "b")
    assert stable_id("a", "b") != stable_id("ab")
    assert sha256_hex(b"x") == sha256_hex(b"x")


def test_redaction_removes_secrets_and_emails_and_truncates():
    text = redact_text("token Bearer abc.def-ghi mail me@example.com key dapi" + "a" * 32, limit=500)
    assert "abc.def" not in text and "me@example.com" not in text and "dapi" + "a" * 32 not in text
    assert redact_text("x" * 600) == "x" * 500
    assert redact_text(None) is None


def test_local_store_is_create_only(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.create("a/b.json", b"one")
    with pytest.raises(CreateConflict):
        store.create("a/b.json", b"two")
    assert store.read("a/b.json") == b"one"
    assert store.list_files("a/") == ["a/b.json"]


def test_identical_replay_is_idempotent(tmp_path):
    writer = _writer(LocalObjectStore(tmp_path))
    first = writer.write_bytes("x/y.json", "fact", b"same")
    second = writer.write_bytes("x/y.json", "fact", b"same")
    assert first.path == second.path == "x/y.json"


def test_different_content_at_same_path_gets_a_suffix(tmp_path):
    writer = _writer(LocalObjectStore(tmp_path))
    writer.write_bytes("x/y.jsonl", "fact", b"one")
    written = writer.write_bytes("x/y.jsonl", "fact", b"two")
    assert written.path == "x/y-r1.jsonl"
    assert (tmp_path / "x/y.jsonl").read_bytes() == b"one"


class _FlakyStore:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.inner = {}

    def create(self, path, data):
        if self.failures:
            self.failures -= 1
            raise OSError("transient")
        if path in self.inner:
            raise CreateConflict(path)
        self.inner[path] = data

    def read(self, path):
        return self.inner[path]

    def list_files(self, prefix):
        return [p for p in self.inner if p.startswith(prefix)]


def test_transient_store_errors_are_retried():
    store = _FlakyStore(failures=2)
    writer = PersistenceWriter(store, retries=3, base_delay=0, sleep=lambda s: None)
    writer.write_bytes("p.json", "fact", b"ok")
    assert store.inner["p.json"] == b"ok"


def test_exhausted_retries_raise_persistence_error():
    writer = PersistenceWriter(_FlakyStore(failures=10), retries=2, base_delay=0, sleep=lambda s: None)
    with pytest.raises(PersistenceError, match="failed after 2 retries"):
        writer.write_bytes("p.json", "fact", b"x")


class _FakeFiles:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload(self, path, contents, *, overwrite=None):
        if path in self.objects and overwrite is False:
            raise AlreadyExists("exists")
        self.objects[path] = contents.read()

    def download(self, path):
        data = self.objects[path]
        return type("R", (), {"contents": type("C", (), {"read": lambda self_: data})()})()

    def list_directory_contents(self, directory):
        seen = set()
        for path in self.objects:
            if path.startswith(directory + "/"):
                rest = path[len(directory) + 1 :]
                head = rest.split("/")[0]
                if head not in seen:
                    seen.add(head)
                    is_dir = "/" in rest
                    yield type("E", (), {"path": f"{directory}/{head}", "is_directory": is_dir})()


def test_uc_volume_store_maps_conflicts_and_lists_recursively():
    workspace = type("W", (), {"files": _FakeFiles()})()
    store = UCVolumeObjectStore(workspace, "/Volumes/c/s/v")
    store.create("records/v1/a.json", b"1")
    with pytest.raises(CreateConflict):
        store.create("records/v1/a.json", b"2")
    store.create("records/v1/fact/dt=x/s/part.jsonl", b"3")
    assert store.read("records/v1/a.json") == b"1"
    assert store.list_files("records/") == ["records/v1/a.json", "records/v1/fact/dt=x/s/part.jsonl"]


# ---------- end to end ----------


def test_completed_session_is_persisted_and_reconstructs_byte_identical(tmp_path):
    store = LocalObjectStore(tmp_path)
    graph = _graph(_stub_deps(), store)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Grid storage"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    values = graph.get_state(cfg).values
    session_id = values["research_session_id"]

    manifests = store.list_files("records/v1/session_manifest/")
    assert len(manifests) == 1 and session_id in manifests[0]
    manifest = json.loads(store.read(manifests[0]))
    assert manifest["status"] == "completed" and manifest["response_ids"]
    for entry in manifest["files"]:
        assert sha256_hex(store.read(entry["path"])) == entry["sha256"]
    types = {entry["record_type"] for entry in manifest["files"]}
    assert {"research_session", "trace_event", "final_response", "report"} <= types

    result = reconstruct(store, session_id)
    assert result["complete"] is True
    assert result["report_sha256_verified"] is True
    assert result["report"] == values["final_report_with_citations"]
    assert result["session"]["status"] == "completed"
    sequences = [event["sequence"] for event in result["timeline"]]
    assert sequences == sorted(sequences) and len(sequences) >= 6
    assert any(event["event_type"] == "plan_generated" for event in result["timeline"])
    assert any(event["event_type"] == "plan_decision" for event in result["timeline"])


def test_reconstruction_writes_readable_outputs(tmp_path):
    store = LocalObjectStore(tmp_path / "store")
    graph = _graph(_stub_deps(), store)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    result = reconstruct(store, _session_id(graph, cfg))
    write_outputs(result, tmp_path / "out")
    assert (tmp_path / "out/report.md").read_text(encoding="utf-8") == result["report"]
    assert "plan_generated" in (tmp_path / "out/timeline.md").read_text(encoding="utf-8")
    assert "report" not in json.loads((tmp_path / "out/session.json").read_text(encoding="utf-8"))


def test_cancelled_session_commits_a_manifest_without_a_response(tmp_path):
    store = LocalObjectStore(tmp_path)
    graph = _graph(_stub_deps(), store)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic"}]}, cfg)
    graph.invoke(Command(resume={"decision": "cancel"}), cfg)
    session_id = _session_id(graph, cfg)
    result = reconstruct(store, session_id)
    assert result["manifest_status"] == "cancelled"
    assert result["responses"] == [] and result["report"] is None


def test_awaiting_approval_session_is_partial_without_manifest(tmp_path):
    store = LocalObjectStore(tmp_path)
    graph = _graph(_stub_deps(), store)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic"}]}, cfg)
    result = reconstruct(store, _session_id(graph, cfg))
    assert result["complete"] is False
    assert result["session"]["status"] == "awaiting_approval"
    assert result["report"] is None


def test_node_failure_is_recorded_and_propagates(tmp_path):
    store = LocalObjectStore(tmp_path)

    def broken(state):
        raise RuntimeError("model unavailable")

    graph = _graph(_stub_deps(outline=broken), store)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic"}]}, cfg)
    with pytest.raises(RuntimeError, match="model unavailable"):
        graph.invoke(Command(resume={"decision": "approve"}), cfg)

    session_id = _session_id(graph, cfg)
    failed_sessions = [json.loads(store.read(p)) for p in store.list_files("records/v1/research_session/") if session_id in p]
    assert max(failed_sessions, key=lambda r: r["session_version"])["status"] == "failed"
    traces = [TraceEventRecord(**json.loads(line)) for p in store.list_files("records/v1/trace_event/") if session_id in p
              for line in store.read(p).decode().splitlines() if line.strip()]
    errors = [t for t in traces if t.event_type == "error"]
    assert any("model unavailable" in (t.error or "") for t in errors)


class _BrokenStore:
    def create(self, path, data):
        raise OSError("disk unavailable")

    def read(self, path):
        raise OSError("disk unavailable")

    def list_files(self, prefix):
        return []


def test_strict_persistence_failure_stops_the_run():
    graph = _graph(_stub_deps(), _BrokenStore(), strict=True)
    with pytest.raises(PersistenceError):
        graph.invoke({"messages": [{"role": "user", "content": "Topic"}]}, _thread())


def test_non_strict_persistence_failure_is_recorded_and_the_run_continues():
    graph = _graph(_stub_deps(), _BrokenStore(), strict=False)
    cfg = _thread()
    result = graph.invoke({"messages": [{"role": "user", "content": "Topic"}]}, cfg)
    assert "__interrupt__" in result  # the approval pause is reached despite the store failure
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    values = graph.get_state(cfg).values
    assert values["phase"] == "done"
    assert any(e["stage"] == "persistence" for e in values["errors"])


def test_reconstruction_detects_a_modified_file(tmp_path):
    store = LocalObjectStore(tmp_path)
    graph = _graph(_stub_deps(), store)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    session_id = _session_id(graph, cfg)
    manifest_path = store.list_files("records/v1/session_manifest/")[0]
    manifest = json.loads(store.read(manifest_path))
    target = next(f["path"] for f in manifest["files"] if f["record_type"] == "report")
    (tmp_path / target).write_text("tampered", encoding="utf-8")
    with pytest.raises(ReconstructError, match="checksum mismatch"):
        reconstruct(store, session_id)


def test_reconstruct_script_round_trip(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("reconstruct_session", Path(__file__).parent.parent / "scripts/reconstruct_session.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    store = LocalObjectStore(tmp_path / "store")
    graph = _graph(_stub_deps(), store)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    session_id = _session_id(graph, cfg)

    code = module.main(["--root", str(tmp_path / "store"), "--session", session_id, "--out", str(tmp_path / "out")])
    assert code == 0
    assert "report_verified=True" in capsys.readouterr().out
    assert (tmp_path / "out" / "report.md").exists()
    assert module.main(["--root", str(tmp_path / "store"), "--session", "missing", "--out", str(tmp_path / "x")]) == 1
