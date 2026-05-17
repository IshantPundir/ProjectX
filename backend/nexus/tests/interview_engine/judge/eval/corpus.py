"""Eval-fixture corpus loader + assertion framework.

A fixture is a JSON file describing one Judge input + the expected
output shape. Assertions are field-level (next_action exact match,
turn_metadata subset, observation shape constraints, reasoning soft
checks).

Spec: docs/superpowers/specs/2026-05-17-interview-engine-v2-design.md §4.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.modules.interview_engine.judge.input_builder import JudgeInputPayload
from app.modules.interview_engine.models.judge import JudgeOutput, NextAction


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@dataclass
class ExpectedAssertions:
    next_action: NextAction | None = None
    forbidden_actions: list[NextAction] = field(default_factory=list)
    turn_metadata_subset: dict[str, bool] = field(default_factory=dict)
    forbidden_meta_flags: list[str] = field(default_factory=list)
    observations_min_count: int | None = None
    observations_max_count: int | None = None
    expected_signals_subset: list[str] = field(default_factory=list)
    forbidden_failure_observations: bool = False
    expected_reasoning_substrings: list[str] = field(default_factory=list)


@dataclass
class EvalFixture:
    id: str
    description: str
    tags: list[str]
    judge_input: JudgeInputPayload
    expected: ExpectedAssertions
    source: str
    labeled_by: str
    labeled_at: str


@dataclass
class EvalResult:
    fixture_id: str
    output: JudgeOutput | None
    error: str | None
    passed: bool
    failures: list[str]
    soft_warnings: list[str]
    latency_ms: int
    cost_estimate_usd: float


def load_all_fixtures() -> list[EvalFixture]:
    """Load every *.json file under fixtures/ as an EvalFixture."""
    fixtures: list[EvalFixture] = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
        fixtures.append(_parse_fixture(raw, source_path=path))
    return fixtures


def _parse_fixture(raw: dict[str, Any], *, source_path: Path) -> EvalFixture:
    expected_raw = raw["expected"]
    expected = ExpectedAssertions(
        next_action=NextAction(expected_raw["next_action"]) if "next_action" in expected_raw else None,
        forbidden_actions=[NextAction(a) for a in expected_raw.get("forbidden_actions", [])],
        turn_metadata_subset=expected_raw.get("turn_metadata", {}),
        forbidden_meta_flags=expected_raw.get("forbidden_meta_flags", []),
        observations_min_count=expected_raw.get("observations_min_count"),
        observations_max_count=expected_raw.get("observations_max_count"),
        expected_signals_subset=expected_raw.get("expected_signals_subset", []),
        forbidden_failure_observations=expected_raw.get("forbidden_failure_observations", False),
        expected_reasoning_substrings=expected_raw.get("expected_reasoning_substrings", []),
    )
    return EvalFixture(
        id=raw["id"],
        description=raw.get("description", ""),
        tags=raw.get("tags", []),
        judge_input=JudgeInputPayload.model_validate(raw["judge_input"]),
        expected=expected,
        source=raw.get("source", source_path.name),
        labeled_by=raw.get("labeled_by", "unknown"),
        labeled_at=raw.get("labeled_at", "unknown"),
    )


def assert_output(output: JudgeOutput, expected: ExpectedAssertions) -> tuple[list[str], list[str]]:
    """Run all assertions against the JudgeOutput. Returns (hard_failures, soft_warnings)."""
    failures: list[str] = []
    warnings: list[str] = []

    if expected.next_action is not None:
        if output.next_action != expected.next_action:
            failures.append(
                f"next_action expected={expected.next_action.value!r} "
                f"got={output.next_action.value!r}"
            )

    for forbidden in expected.forbidden_actions:
        if output.next_action == forbidden:
            failures.append(f"forbidden next_action={forbidden.value!r} was emitted")

    md = output.turn_metadata.model_dump()
    for key, expected_value in expected.turn_metadata_subset.items():
        actual = md.get(key)
        if actual != expected_value:
            failures.append(
                f"turn_metadata.{key} expected={expected_value} got={actual}"
            )

    for forbidden_flag in expected.forbidden_meta_flags:
        if md.get(forbidden_flag) is True:
            failures.append(f"forbidden meta flag {forbidden_flag!r} was set to True")

    obs_count = len(output.observations)
    if expected.observations_min_count is not None and obs_count < expected.observations_min_count:
        failures.append(f"observations count {obs_count} < min {expected.observations_min_count}")
    if expected.observations_max_count is not None and obs_count > expected.observations_max_count:
        failures.append(f"observations count {obs_count} > max {expected.observations_max_count}")

    observed_signals = {o.signal_value for o in output.observations}
    for required_signal in expected.expected_signals_subset:
        if required_signal not in observed_signals:
            failures.append(
                f"expected signal {required_signal!r} not in observed signals {sorted(observed_signals)!r}"
            )

    if expected.forbidden_failure_observations:
        for o in output.observations:
            if "failed" in o.coverage_transition.value and o.anchor_id >= 0:
                failures.append(
                    f"illegal failure obs: anchor_id={o.anchor_id} "
                    f"transition={o.coverage_transition.value} on signal {o.signal_value!r}"
                )

    for substring in expected.expected_reasoning_substrings:
        if substring.lower() not in output.reasoning.lower():
            warnings.append(
                f"reasoning substring {substring!r} not found "
                f"(reasoning length: {len(output.reasoning)} chars)"
            )

    return failures, warnings
