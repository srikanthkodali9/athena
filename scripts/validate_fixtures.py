#!/usr/bin/env python3
"""Validate Athena's synthetic demo fixture catalog using the standard library."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "edge_cases"
MANIFEST = FIXTURES / "manifest.json"

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9]{24,}\b"),
}


class FixtureValidationError(Exception):
    """Raised when one or more fixture invariants fail."""


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[Any]:
    records: list[Any] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise FixtureValidationError(
                    f"{path.relative_to(ROOT)}:{line_number}: invalid JSONL: {error}"
                ) from error
    return records


def parse_timestamp(value: str, location: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise FixtureValidationError(f"{location}: invalid RFC 3339 timestamp {value!r}") from error


def resolve_manifest_path(value: str) -> Path:
    candidate = (ROOT / value).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as error:
        raise FixtureValidationError(f"manifest path escapes repository: {value}") from error
    if not candidate.is_file():
        raise FixtureValidationError(f"manifest path does not exist: {value}")
    return candidate


def collect_evidence_ids(value: Any) -> set[str]:
    evidence_ids: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"evidence_id", "diff_evidence_id"} and isinstance(item, str):
                evidence_ids.add(item)
            evidence_ids.update(collect_evidence_ids(item))
    elif isinstance(value, list):
        for item in value:
            evidence_ids.update(collect_evidence_ids(item))
    return evidence_ids


def collect_file_evidence_ids(path: Path) -> set[str]:
    if path.suffix == ".json":
        return collect_evidence_ids(load_json(path))
    if path.suffix == ".jsonl":
        return collect_evidence_ids(load_jsonl(path))
    if path.suffix in {".yaml", ".yml"}:
        text = path.read_text(encoding="utf-8")
        return set(re.findall(r"athena\.fixture/diff-evidence-id:\s*([A-Za-z0-9_.-]+)", text))
    return set()


def validate_json_catalog(errors: list[str]) -> tuple[int, int]:
    json_count = 0
    jsonl_record_count = 0
    for path in sorted(FIXTURES.rglob("*")):
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(FIXTURES).parts):
            continue
        try:
            if path.suffix == ".json":
                load_json(path)
                json_count += 1
            elif path.suffix == ".jsonl":
                jsonl_record_count += len(load_jsonl(path))
        except (json.JSONDecodeError, FixtureValidationError) as error:
            errors.append(f"{path.relative_to(ROOT)}: {error}")
    return json_count, jsonl_record_count


def validate_metric_order(path: Path, errors: list[str]) -> None:
    data = load_json(path)
    for series in data.get("series", []):
        timestamps = [
            parse_timestamp(point["timestamp"], f"{path}:{series.get('evidence_id')}")
            for point in series.get("points", [])
        ]
        if timestamps != sorted(timestamps):
            errors.append(f"{path.relative_to(ROOT)}: normal metric points are not ordered")
        if len(timestamps) != len(set(timestamps)):
            errors.append(f"{path.relative_to(ROOT)}: normal metric points contain duplicates")
        for point in series.get("points", []):
            if not isinstance(point.get("value"), (int, float)):
                errors.append(
                    f"{path.relative_to(ROOT)}:{series.get('evidence_id')}: metric value is not numeric"
                )


def validate_core_scenarios(manifest: dict[str, Any], errors: list[str]) -> None:
    scenarios = manifest.get("core_scenarios", [])
    if [item.get("scenario_id") for item in scenarios] != ["redis-001", "redis-002"]:
        errors.append("manifest core scenarios must be ordered as redis-001 then redis-002")

    outcomes: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        scenario_id = scenario.get("scenario_id", "<missing>")
        try:
            incident_path = resolve_manifest_path(scenario["incident"])
            expected_path = resolve_manifest_path(scenario["expected_outcome"])
            dataset_paths = [resolve_manifest_path(value) for value in scenario.get("datasets", [])]
            incident = load_json(incident_path)
            expected = load_json(expected_path)
        except (KeyError, FixtureValidationError, json.JSONDecodeError) as error:
            errors.append(f"{scenario_id}: {error}")
            continue

        outcomes[scenario_id] = expected
        if incident.get("scenario_id") != scenario_id:
            errors.append(f"{scenario_id}: incident scenario_id does not match manifest")
        if expected.get("scenario_id") != scenario_id:
            errors.append(f"{scenario_id}: expected outcome scenario_id does not match manifest")
        if expected.get("test_oracle_only") is not True:
            errors.append(f"{scenario_id}: expected outcome must be marked test_oracle_only")
        if expected_path in dataset_paths:
            errors.append(f"{scenario_id}: test oracle must not appear in runtime datasets")

        runtime_evidence = collect_evidence_ids(incident)
        for dataset_path in dataset_paths:
            runtime_evidence.update(collect_file_evidence_ids(dataset_path))
        missing = set(expected.get("required_evidence_ids", [])) - runtime_evidence
        if missing:
            errors.append(f"{scenario_id}: required evidence has no runtime source: {sorted(missing)}")

        metrics_path = next(
            (path for path in dataset_paths if path.name == "metrics.json"),
            None,
        )
        if metrics_path is None:
            errors.append(f"{scenario_id}: metrics.json is missing")
        else:
            validate_metric_order(metrics_path, errors)

    if set(outcomes) == {"redis-001", "redis-002"}:
        first = outcomes["redis-001"]
        learned = outcomes["redis-002"]
        improvements = 0
        if learned["measurement_targets"]["investigation_time_seconds_max"] < first["measurement_targets"]["investigation_time_seconds_max"]:
            improvements += 1
        if learned["measurement_targets"]["tool_calls_max"] < first["measurement_targets"]["tool_calls_max"]:
            improvements += 1
        if learned["ground_truth"]["minimum_confidence"] > first["ground_truth"]["minimum_confidence"]:
            improvements += 1
        if learned["measurement_targets"]["unsupported_hypotheses_max"] < first["measurement_targets"]["unsupported_hypotheses_max"]:
            improvements += 1
        if improvements < learned["measurement_targets"]["required_improvement_count"]:
            errors.append("redis-002 does not encode at least two measurable improvements")


def validate_manifest_references(manifest: dict[str, Any], errors: list[str]) -> None:
    for value in manifest.get("edge_cases", []):
        try:
            resolve_manifest_path(value)
        except FixtureValidationError as error:
            errors.append(str(error))

    security = manifest.get("security_scenario", {})
    for value in security.get("datasets", []):
        try:
            resolve_manifest_path(value)
        except FixtureValidationError as error:
            errors.append(str(error))

    if len(manifest.get("edge_cases", [])) < 8:
        errors.append("manifest must include at least eight edge-case datasets")


def validate_no_real_secrets(errors: list[str]) -> None:
    for path in sorted(FIXTURES.rglob("*")):
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(FIXTURES).parts):
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{path.relative_to(ROOT)}: possible real {label}")


def validate_repository() -> dict[str, int]:
    errors: list[str] = []
    if not MANIFEST.is_file():
        raise FixtureValidationError("fixtures/edge_cases/manifest.json is missing")

    try:
        manifest = load_json(MANIFEST)
    except json.JSONDecodeError as error:
        raise FixtureValidationError(f"fixtures/edge_cases/manifest.json is invalid: {error}") from error

    json_count, jsonl_record_count = validate_json_catalog(errors)
    validate_manifest_references(manifest, errors)
    validate_core_scenarios(manifest, errors)
    validate_no_real_secrets(errors)

    if manifest.get("synthetic_data") is not True:
        errors.append("fixture catalog must be explicitly marked synthetic_data=true")

    if errors:
        raise FixtureValidationError("\n".join(f"- {error}" for error in errors))

    return {
        "json_files": json_count,
        "jsonl_records": jsonl_record_count,
        "core_scenarios": len(manifest.get("core_scenarios", [])),
        "edge_cases": len(manifest.get("edge_cases", [])),
    }


def main() -> int:
    try:
        counts = validate_repository()
    except FixtureValidationError as error:
        print(f"Fixture validation failed:\n{error}", file=sys.stderr)
        return 1

    print(
        "Fixture validation passed: "
        f"{counts['core_scenarios']} core scenarios, "
        f"{counts['edge_cases']} edge cases, "
        f"{counts['json_files']} JSON files, "
        f"{counts['jsonl_records']} JSONL records"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
