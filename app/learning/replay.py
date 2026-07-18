"""Deterministic replay validation (FR8-06).

Replay is the *non-similarity* validation the PRD demands: a candidate skill is
exercised against fixtures and judged on its DISCRIMINATION, not on how close a
vector looks. A trustworthy skill must:

* **APPLY** on a matching (positive) fixture — triggers + preconditions hold and
  no stop condition fires, so it concludes.
* **ABSTAIN** on a non-matching (negative) fixture — triggers/preconditions do
  not hold, so it correctly declines (``NOT_APPLICABLE``).
* **STOP** on a contradictory fixture — the triggers superficially match but a
  stop condition fires (e.g. "Redis healthy, another dependency explains"), so it
  correctly refuses to conclude (``STOPPED``).

A skill only earns promotion when it passes at least one fixture of each kind.
Everything here is pure and deterministic (no models, no I/O), so replay is
reproducible under reset (ADR-0002 §6).

Trigger / stop grammar is intentionally tiny: ``"<metric> <op> <value>"`` where
``<value>`` is a number or the token ``baseline`` (resolved against the fixture's
baseline map or a ``<metric>_baseline`` signal). Anything unparseable is treated
as UNMET — a skill can never accidentally "apply" on a rule it could not check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.learning.schemas import (
    ReplayKind,
    ReplayRecord,
    ReplayVerdict,
    Skill,
)

_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


@dataclass(frozen=True)
class ReplayFixture:
    """One deterministic scenario a skill is replayed against.

    ``signals`` are objective metric readings. ``preconditions_met`` names the
    precondition tokens that hold. ``active_stop_conditions`` names stop-condition
    tokens that fire (used for the contradictory case). ``kind`` sets the expected
    behaviour the verdict is scored against.
    """

    name: str
    kind: ReplayKind
    signals: dict[str, float] = field(default_factory=dict)
    baselines: dict[str, float] = field(default_factory=dict)
    preconditions_met: frozenset[str] = frozenset()
    active_stop_conditions: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


def _resolve_rhs(token: str, lhs: str, fixture: ReplayFixture) -> Optional[float]:
    token = token.strip()
    if token == "baseline":
        if lhs in fixture.baselines:
            return fixture.baselines[lhs]
        alt = f"{lhs}_baseline"
        if alt in fixture.signals:
            return fixture.signals[alt]
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _trigger_met(rule: str, fixture: ReplayFixture) -> bool:
    """Evaluate one ``"<metric> <op> <value>"`` rule; unparseable -> False."""
    parts = rule.strip().split()
    if len(parts) != 3:
        return False
    lhs, op, rhs = parts
    if op not in _OPS or lhs not in fixture.signals:
        return False
    rhs_val = _resolve_rhs(rhs, lhs, fixture)
    if rhs_val is None:
        return False
    return bool(_OPS[op](fixture.signals[lhs], rhs_val))


def evaluate(skill: Skill, fixture: ReplayFixture) -> ReplayVerdict:
    """Decide what the skill does on the fixture (pure, deterministic)."""
    triggers_ok = bool(skill.triggers) and all(
        _trigger_met(t, fixture) for t in skill.triggers
    )
    preconds_ok = all(p in fixture.preconditions_met for p in skill.preconditions)

    if not (triggers_ok and preconds_ok):
        return ReplayVerdict.NOT_APPLICABLE

    # Triggers + preconditions hold. A firing stop condition overrides.
    if any(sc in fixture.active_stop_conditions for sc in skill.stop_conditions):
        return ReplayVerdict.STOPPED
    return ReplayVerdict.APPLIED


_EXPECTED = {
    ReplayKind.POSITIVE: ReplayVerdict.APPLIED,
    ReplayKind.NEGATIVE: ReplayVerdict.NOT_APPLICABLE,
    ReplayKind.CONTRADICTORY: ReplayVerdict.STOPPED,
}


@dataclass
class ReplayReport:
    records: list[ReplayRecord]

    @property
    def passes(self) -> int:
        return sum(1 for r in self.records if r.passed)

    @property
    def all_passed(self) -> bool:
        return bool(self.records) and all(r.passed for r in self.records)

    @property
    def covers_all_kinds(self) -> bool:
        kinds = {r.kind for r in self.records if r.passed}
        return {ReplayKind.POSITIVE, ReplayKind.NEGATIVE, ReplayKind.CONTRADICTORY} <= kinds


class ReplayValidator:
    """Runs a candidate skill against a fixture set and records history."""

    def run(self, skill: Skill, fixtures: list[ReplayFixture]) -> ReplayReport:
        records: list[ReplayRecord] = []
        for fx in fixtures:
            verdict = evaluate(skill, fx)
            expected = _EXPECTED[fx.kind]
            passed = verdict is expected
            records.append(
                ReplayRecord(
                    fixture_name=fx.name,
                    kind=fx.kind,
                    verdict=verdict,
                    passed=passed,
                    detail=(
                        f"expected {expected.value}, got {verdict.value}"
                        if not passed
                        else f"{verdict.value} as expected"
                    ),
                )
            )
        return ReplayReport(records=records)


# ---------------------------------------------------------------------------
# Default Redis-family fixtures (the incident-one skill under test)
# ---------------------------------------------------------------------------


def _satisfying_value(rule: str, satisfy: bool) -> Optional[tuple[str, float, dict]]:
    """Return ``(metric, signal_value, extra_signals)`` that (dis)satisfies a rule.

    ``extra_signals`` carries a synthesized ``*_baseline`` reading when the rule
    compares against ``baseline``. Returns ``None`` for unparseable rules.
    """
    parts = rule.strip().split()
    if len(parts) != 3:
        return None
    lhs, op, rhs = parts
    if op not in _OPS:
        return None
    extra: dict[str, float] = {}
    if rhs == "baseline":
        base = 1.0
        extra[f"{lhs}_baseline"] = base
        target = base
    else:
        try:
            target = float(rhs)
        except ValueError:
            return None
    # Pick a value on the desired side of the comparison.
    if op in (">", ">="):
        value = target + 1 if satisfy else target - 1
    elif op in ("<", "<="):
        value = target - 1 if satisfy else target + 1
    elif op == "==":
        value = target if satisfy else target + 1
    else:  # !=
        value = target + 1 if satisfy else target
    return lhs, value, extra


def synthesize_fixtures(skill: Skill) -> list[ReplayFixture]:
    """Derive positive/negative/contradictory fixtures from a skill's own rules.

    Deterministic and family-agnostic. A skill with no triggers or no stop
    conditions cannot produce a full discriminating set and will therefore fail
    coverage — the intended conservative default (a promotable skill must define
    both what fires it and when to stop).
    """
    pos_signals: dict[str, float] = {}
    for t in skill.triggers:
        resolved = _satisfying_value(t, satisfy=True)
        if resolved is None:
            continue
        lhs, value, extra = resolved
        pos_signals[lhs] = value
        pos_signals.update(extra)
    preconds = frozenset(skill.preconditions)

    fixtures = [
        ReplayFixture(
            name="synthetic-positive",
            kind=ReplayKind.POSITIVE,
            signals=dict(pos_signals),
            preconditions_met=preconds,
        ),
        ReplayFixture(
            name="synthetic-negative",
            kind=ReplayKind.NEGATIVE,
            signals={},  # no triggering signals present -> NOT_APPLICABLE
            preconditions_met=frozenset(),
        ),
        ReplayFixture(
            name="synthetic-contradictory",
            kind=ReplayKind.CONTRADICTORY,
            signals=dict(pos_signals),
            preconditions_met=preconds,
            active_stop_conditions=frozenset(skill.stop_conditions),
        ),
    ]
    return fixtures


def build_fixtures_for(skill: Skill) -> list[ReplayFixture]:
    """Curated fixtures for known families; synthesized otherwise."""
    if skill.skill_id.startswith("sre.redis"):
        return default_redis_fixtures()
    return synthesize_fixtures(skill)


def default_redis_fixtures() -> list[ReplayFixture]:
    """Positive / negative / contradictory scenarios for the Redis skill.

    These mirror the canonical trigger vocabulary drafted by
    :mod:`app.learning.service` so the promoted skill is provably discriminating.
    """
    return [
        # Positive: Redis saturated (9900/10000 = 0.99) + API 5xx above baseline.
        ReplayFixture(
            name="redis-saturation-match",
            kind=ReplayKind.POSITIVE,
            signals={"redis_saturation_ratio": 0.99, "api_5xx_rate": 0.18, "api_5xx_rate_baseline": 0.01},
            preconditions_met=frozenset(
                {"redis_metrics_available", "dependency_graph_contains_api_to_redis"}
            ),
        ),
        # Negative: Redis healthy (ratio well below 0.9) — skill must abstain.
        ReplayFixture(
            name="unrelated-cpu-spike",
            kind=ReplayKind.NEGATIVE,
            signals={"redis_saturation_ratio": 0.20, "api_5xx_rate": 0.02, "api_5xx_rate_baseline": 0.01},
            preconditions_met=frozenset({"redis_metrics_available"}),
        ),
        # Contradictory: signals fire but Redis is healthy and another dependency
        # explains the errors — skill must STOP rather than conclude.
        ReplayFixture(
            name="redis-healthy-other-dependency",
            kind=ReplayKind.CONTRADICTORY,
            signals={"redis_saturation_ratio": 0.95, "api_5xx_rate": 0.15, "api_5xx_rate_baseline": 0.01},
            preconditions_met=frozenset(
                {"redis_metrics_available", "dependency_graph_contains_api_to_redis"}
            ),
            active_stop_conditions=frozenset(
                {"redis_healthy_and_other_dependency_explains"}
            ),
        ),
    ]
