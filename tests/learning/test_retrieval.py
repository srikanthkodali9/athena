"""Retrieval contract: only promoted, non-expired, non-quarantined skills."""

from __future__ import annotations

from datetime import timedelta

from app.learning.retrieval import SkillRetriever
from app.learning.schemas import SkillStatus, utcnow
from app.learning.service import LearningService

from tests.learning.conftest import make_redis_cycle, redis_evidence


def _promote_redis(service):
    return service.learn_from_cycle(
        make_redis_cycle(), verified=True, evidence=redis_evidence()
    )


def test_promoted_skill_is_retrievable_with_citation(service, store):
    _promote_redis(service)
    retriever = SkillRetriever(store)

    match = retriever.match(
        signals={"redis_saturation_ratio": 0.98, "api_5xx_rate": 0.2, "api_5xx_rate_baseline": 0.01},
        preconditions_met={"redis_metrics_available", "dependency_graph_contains_api_to_redis"},
    )
    assert match is not None
    skill, citation = match
    assert skill.skill_id == "sre.redis.connection-saturation"
    # Incident two can cite EXACTLY what it reused (FR-14).
    assert citation.skill_id == skill.skill_id
    assert citation.version == skill.version
    assert citation.reused_evidence_ids  # supporting evidence carried forward
    assert set(citation.matched_triggers) == set(skill.triggers)


def test_non_matching_signals_return_none(service, store):
    _promote_redis(service)
    retriever = SkillRetriever(store)
    assert retriever.match(signals={"cpu": 0.9}) is None


def test_quarantined_skill_not_retrievable(store, tmp_path):
    from tests.learning.conftest import QuarantineGateway

    svc = LearningService(store=store, security=QuarantineGateway(), skills_dir=str(tmp_path / "s"))
    svc.learn_from_cycle(make_redis_cycle(), verified=True, evidence=redis_evidence())
    retriever = SkillRetriever(store)
    assert retriever.latest_promoted("sre.redis.connection-saturation") is None
    assert retriever.match(
        signals={"redis_saturation_ratio": 0.98, "api_5xx_rate": 0.2, "api_5xx_rate_baseline": 0.01},
        preconditions_met={"redis_metrics_available", "dependency_graph_contains_api_to_redis"},
    ) is None


def test_deprecated_skill_not_retrievable(service, store):
    result = _promote_redis(service)
    skill = store.get_skill(result.skill_id, result.skill_version)
    skill.status = SkillStatus.DEPRECATED
    store.save_skill(skill)
    retriever = SkillRetriever(store)
    assert retriever.latest_promoted(result.skill_id) is None


def test_expired_skill_not_retrievable(service, store):
    result = _promote_redis(service)
    skill = store.get_skill(result.skill_id, result.skill_version)
    skill.expires_at = utcnow() - timedelta(days=1)
    store.save_skill(skill)
    retriever = SkillRetriever(store)
    assert retriever.latest_promoted(result.skill_id) is None


def test_only_validated_but_not_promoted_is_not_retrievable(service, store):
    # A skill retained pending approval is VALIDATED, never returned by retrieval.
    result = service.learn_from_cycle(
        make_redis_cycle(), verified=True, evidence=redis_evidence(),
        requires_approval_override=True,
    )
    assert result.skill_status is SkillStatus.VALIDATED
    retriever = SkillRetriever(store)
    assert retriever.latest_promoted(result.skill_id) is None
