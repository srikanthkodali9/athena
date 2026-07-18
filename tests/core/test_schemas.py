"""Schema tests: plan bounds, one-revision bound, fact/interpretation split."""

from __future__ import annotations

import pytest

from app.athena.schemas import (
    Decision,
    Evidence,
    Finding,
    InvestigationPlan,
    RoundBudget,
    ToolResult,
)


def _plan(**overrides) -> InvestigationPlan:
    base = dict(
        plan_id="plan_1",
        max_specialists=2,
        max_tool_calls=10,
        max_tokens=14000,
        deadline_seconds=75,
        first_round=RoundBudget(max_tokens=4000),
        second_round=RoundBudget(max_tokens=4000),
        platform_question="original question",
    )
    base.update(overrides)
    return InvestigationPlan(**base)


def test_decision_enum_has_all_values():
    names = {d.value for d in Decision}
    assert names == {
        "WAIT",
        "INVESTIGATE",
        "ESCALATE",
        "CONTINUE",
        "RETRY",
        "LEARN",
        "REQUEST_APPROVAL",
        "CONTAIN",
    }


def test_plan_bounds_ok():
    assert _plan().bounds_ok() is True


def test_plan_bounds_reject_when_rounds_exceed_total():
    plan = _plan(
        max_tokens=6000,
        first_round=RoundBudget(max_tokens=4000),
        second_round=RoundBudget(max_tokens=4000),
    )
    assert plan.bounds_ok() is False


def test_plan_specialist_bound_enforced():
    with pytest.raises(ValueError):
        _plan(max_specialists=3)  # ge/le=2 on the field


def test_one_revision_bound():
    plan = _plan()
    plan.revise_platform_question("revised question", reason="new evidence")
    assert plan.revision_used is True
    assert plan.original_platform_question == "original question"
    assert plan.platform_question == "revised question"
    with pytest.raises(ValueError):
        plan.revise_platform_question("again", reason="second attempt")


def test_evidence_is_immutable():
    ev = Evidence(id="ev_1", type="metric", source="prometheus")
    with pytest.raises(Exception):
        ev.data = {"mutated": True}  # frozen model


def test_tool_result_exposes_evidence_ids():
    result = ToolResult(
        tool="query_metrics",
        status="success",
        evidence=[Evidence(id="ev_1", type="metric", source="prometheus")],
    )
    assert result.evidence_ids == ["ev_1"]


def test_finding_keeps_hypothesis_and_citations_separate():
    finding = Finding(
        id="find_1",
        role="observability",
        hypothesis="saturation",
        confidence=0.5,
        cited_evidence_ids=["ev_1"],
        missing_evidence=["logs"],
    )
    # A finding is an interpretation citing evidence; it carries no raw facts.
    assert finding.cited_evidence_ids == ["ev_1"]
    assert finding.missing_evidence == ["logs"]
    assert not hasattr(finding, "data")
