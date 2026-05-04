"""Tests for the event-kind constant registry.

Guards against:

- Two constants accidentally mapping to the same string (which would
  silently shadow each other in `ALL_EVENT_KINDS`).
- A constant declared above but forgotten in `ALL_EVENT_KINDS` (the
  registry is what the Phase J docs-generator and observability
  dashboards consume).
- A kind string that violates the lowercase + dot-separated convention
  (which would surface as a tooling smell when payload schemas are
  written).
"""
from __future__ import annotations

import re

from app.modules.interview_engine import event_kinds

_KIND_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


def _module_constants() -> dict[str, str]:
    """Return all module-level UPPER_CASE constants whose value is a
    kind-shaped string (excluding the registry / prefix-only entries)."""
    out: dict[str, str] = {}
    for attr_name in dir(event_kinds):
        if not attr_name.isupper():
            continue
        if attr_name in {"ALL_EVENT_KINDS", "AUDIO_METRICS_PREFIX"}:
            continue
        value = getattr(event_kinds, attr_name)
        if not isinstance(value, str):
            continue
        out[attr_name] = value
    return out


def test_every_constant_is_a_non_empty_string():
    constants = _module_constants()
    assert constants, "Expected at least one event-kind constant"
    for name, value in constants.items():
        assert value, f"{name} is empty"
        assert isinstance(value, str)


def test_no_duplicate_kind_strings():
    """Two constants with the same string is a copy-paste bug — would
    silently merge in the registry."""
    constants = _module_constants()
    seen: dict[str, str] = {}
    for name, value in constants.items():
        if value in seen:
            raise AssertionError(
                f"Duplicate event kind string {value!r}: "
                f"declared by both {seen[value]} and {name}"
            )
        seen[value] = name


def test_every_constant_is_in_all_event_kinds_registry():
    """A constant declared but missing from ALL_EVENT_KINDS would be
    invisible to the docs-generator / dashboards."""
    constants = _module_constants()
    for name, value in constants.items():
        assert value in event_kinds.ALL_EVENT_KINDS, (
            f"{name}={value!r} is declared but missing from ALL_EVENT_KINDS"
        )


def test_all_event_kinds_has_no_orphans():
    """Conversely: every string in the registry must correspond to a
    declared constant — registry-only entries would have no callers."""
    constants_values = set(_module_constants().values())
    orphans = event_kinds.ALL_EVENT_KINDS - constants_values
    assert not orphans, (
        f"ALL_EVENT_KINDS contains strings without a matching named "
        f"constant: {sorted(orphans)}"
    )


def test_kind_strings_follow_namespace_convention():
    """Convention: lowercase, dot-separated, at least one dot.

    Surfaces typos / unintended camelCase / accidental double-dots."""
    constants = _module_constants()
    for name, value in constants.items():
        assert _KIND_NAME_RE.match(value), (
            f"{name}={value!r} violates the lowercase + dot-separated "
            f"convention (expected match {_KIND_NAME_RE.pattern})"
        )


def test_audio_metrics_prefix_terminates_with_dot():
    """The prefix is concatenated with a vendor-defined metric type at
    runtime; trailing dot is load-bearing."""
    assert event_kinds.AUDIO_METRICS_PREFIX.endswith(".")


def test_phase_j_ledger_snapshot_kind_present():
    """The audit envelope's terminal ledger flush event is the seam
    between live agent and Report Builder; declared up-front so the
    Phase B / J implementation can't drift from the documented kind."""
    assert event_kinds.ORCHESTRATOR_LEDGER_SNAPSHOT == "orchestrator.ledger.snapshot"


def test_session_close_kind_matches_existing_emission():
    """`agent.py` emits `session.close` today; the constant must match
    so adopting the constant doesn't silently change the kind string."""
    assert event_kinds.SESSION_CLOSE == "session.close"
