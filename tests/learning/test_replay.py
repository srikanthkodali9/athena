"""Replay validation: positive / negative / contradictory discrimination."""

from __future__ import annotations

from app.learning.replay import (
    ReplayValidator,
    build_fixtures_for,
    default_redis_fixtures,
    evaluate,
    synthesize_fixtures,
)
from app.learning.schemas import (
    RiskLevel,
    ReplayKind,
    ReplayVerdict,
    Skill,
    SkillStatus,
)


def _redis_skill() -> Skill:
    return Skill(
        skill_id="sre.redis.connection-saturation",
        triggers=["redis_saturation_ratio >= 0.9", "api_5xx_rate > baseline"],
        preconditions=["redis_metrics_available", "dependency_graph_contains_api_to_redis"],
        stop_conditions=["redis_healthy_and_other_dependency_explains"],
        risk=RiskLevel.MEDIUM,
    )


def test_positive_negative_contradictory_verdicts():
    skill = _redis_skill()
    fx = {f.kind: f for f in default_redis_fixtures()}
    assert evaluate(skill, fx[ReplayKind.POSITIVE]) is ReplayVerdict.APPLIED
    assert evaluate(skill, fx[ReplayKind.NEGATIVE]) is ReplayVerdict.NOT_APPLICABLE
    assert evaluate(skill, fx[ReplayKind.CONTRADICTORY]) is ReplayVerdict.STOPPED


def test_curated_replay_passes_all_kinds():
    report = ReplayValidator().run(_redis_skill(), default_redis_fixtures())
    assert report.all_passed
    assert report.covers_all_kinds
    assert report.passes == 3


def test_synthesized_fixtures_cover_all_kinds():
    skill = _redis_skill()
    report = ReplayValidator().run(skill, synthesize_fixtures(skill))
    assert report.covers_all_kinds


def test_skill_without_stop_conditions_cannot_prove_contradiction():
    skill = _redis_skill()
    skill.stop_conditions = []
    report = ReplayValidator().run(skill, synthesize_fixtures(skill))
    # No stop condition => contradictory fixture cannot be STOPPED => no coverage.
    assert not report.covers_all_kinds


def test_build_fixtures_prefers_curated_for_redis():
    skill = _redis_skill()
    names = {f.name for f in build_fixtures_for(skill)}
    assert "redis-saturation-match" in names
