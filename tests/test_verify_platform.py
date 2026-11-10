"""Unit tests for scripts/verify_platform.py reporting logic (no workspace required)."""

import importlib.util
import pathlib
import sys

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_platform", pathlib.Path(__file__).parent.parent / "scripts" / "verify_platform.py"
)
vp = importlib.util.module_from_spec(_SPEC)
sys.modules["verify_platform"] = vp  # dataclasses needs the module registered
_SPEC.loader.exec_module(vp)


def test_missing_workspace_is_skip_not_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)
    results = {r.id: r for r in vp.run_checks([vp.check_a1_model_services, vp.check_a13_embedding_endpoint])}
    assert results["A1"].status == vp.SKIP
    assert results["A13"].status == vp.SKIP


def test_crashing_check_is_fail_with_assumption_id() -> None:
    def boom() -> vp.Result:
        raise ValueError("bad")

    boom.__name__ = "check_a2_tavily_mcp_tools"
    result = vp.run_checks([boom])[0]
    assert (result.id, result.status) == ("A2", vp.FAIL)
    assert "ValueError: bad" in result.evidence


def test_a7_records_reranker_correction() -> None:
    result = vp.check_a7_reranker_fields()
    assert result.status == vp.PASS
    assert "Correction recorded" in result.evidence


def test_every_assumption_is_covered_exactly_once() -> None:
    automated = {"A1", "A1b", "A2", "A3", "A13", "A6-sig", "A7", "A9"}
    manual = {m[0] for m in vp.MANUAL_CHECKS}
    assert automated.isdisjoint(manual)
    # Assumptions A1-A13 from the plan (L.2); A6 is split into an automated signature check and a manual semantics check.
    assert {"A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12", "A13"} <= automated | manual


def test_render_counts_and_exit_logic() -> None:
    results = [vp.Result("X", "t", vp.PASS, "ok"), vp.Result("Y", "t", vp.FAIL, "no")]
    text = vp.render(results)
    assert "PASS=1 FAIL=1 SKIP=0 MANUAL=0" in text
