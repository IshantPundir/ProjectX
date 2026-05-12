"""Regression gate: assert the deleted orchestrator-side mitigation
mechanisms are NOT re-introduced into orchestrator.py.

Each name below was deleted in 2026-05-12 simplification (PR ref). If
this test fails because someone re-added one of these, read the
2026-05-12-engine-simplification-design.md spec and discuss before
merging.
"""
from __future__ import annotations

import ast
import pathlib

ORCHESTRATOR_PATH = pathlib.Path(__file__).resolve().parents[2] / (
    "app/modules/interview_engine/orchestrator.py"
)
SETTINGS_PATH = pathlib.Path(__file__).resolve().parents[2] / "app/config.py"

# These names were intentionally removed from orchestrator.py.
# Re-introducing any of them means we re-introduced a layered mitigation
# we agreed to delete.
FORBIDDEN_ORCHESTRATOR_NAMES: frozenset[str] = frozenset({
    "_PriorTurnSnapshot",
    "_CoalesceDecision",
    "_should_coalesce",
    "_COALESCIBLE_KINDS",
    "_capture_prior_turn_snapshot",
    "_maybe_coalesce",
    "_derive_sub_context",
    "_is_stale_turn",
    "_buffer_dropped_text",
    "_drain_stale_buffer",
    "_user_resumed_speaking_after",
    "_MUST_DELIVER_JUDGE_ACTIONS",
})

# These config fields were intentionally removed from app/config.py
# (Settings class). Re-introducing them suggests reviving a deleted knob.
FORBIDDEN_SETTINGS_FIELDS: frozenset[str] = frozenset({
    "engine_coalesce_enabled",
    "engine_coalesce_window_ms",
    "engine_stale_turn_threshold_ms",
    "engine_stale_buffer_max",
    "engine_post_judge_resumption_epsilon_ms",
})


def test_orchestrator_does_not_reintroduce_deleted_mitigations() -> None:
    source = ORCHESTRATOR_PATH.read_text()
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in FORBIDDEN_ORCHESTRATOR_NAMES:
                found.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in FORBIDDEN_ORCHESTRATOR_NAMES:
                    found.add(target.id)
    assert not found, (
        f"Reintroduced removed mitigation symbol(s) in orchestrator.py: "
        f"{sorted(found)}. See "
        f"docs/superpowers/specs/2026-05-12-engine-simplification-design.md."
    )


def test_settings_does_not_reintroduce_deleted_engine_knobs() -> None:
    """Asserts the deleted Settings fields are not silently re-added.

    Walks the AST of `app/config.py` and looks for AnnAssign nodes
    (typed attribute declarations) whose target name matches a deleted
    field. Catches accidental re-introduction during merges or refactors.
    """
    source = SETTINGS_PATH.read_text()
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in FORBIDDEN_SETTINGS_FIELDS:
                found.add(node.target.id)
    assert not found, (
        f"Reintroduced removed Settings field(s) in app/config.py: "
        f"{sorted(found)}. See "
        f"docs/superpowers/specs/2026-05-12-engine-simplification-design.md."
    )
