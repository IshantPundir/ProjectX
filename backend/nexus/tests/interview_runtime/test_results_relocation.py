"""CMI-1: the snapshot models live in interview_runtime.results; the old engine paths
re-export them (shims); interview_runtime no longer imports interview_engine."""
import ast
import importlib
from pathlib import Path

import pytest


def test_snapshots_importable_from_results():
    from app.modules.interview_runtime.results import (  # noqa: F401
        ClaimEntry,
        ClaimsPoolSnapshot,
        CoverageState,
        LedgerEntry,
        QuestionQueueSnapshot,
        QuestionState,
        QuestionStatus,
        SignalLedgerSnapshot,
        SignalSnapshot,
    )
    # closure intact: a snapshot composes its members
    snap = SignalLedgerSnapshot(
        entries=[LedgerEntry(seq=1, turn_id="t1", signal_value="s", anchor_id=0,
                             evidence_quote="q", coverage_before=CoverageState.none,
                             coverage_after=CoverageState.partial, recorded_at_ms=0)],
        snapshots={"s": SignalSnapshot(signal_value="s", coverage=CoverageState.partial)},
    )
    assert snap.entries[0].coverage_after is CoverageState.partial


@pytest.mark.parametrize("modpath", [
    "app.modules.interview_engine.models.ledger",
    "app.modules.interview_engine.models.queue",
    "app.modules.interview_engine.models.claims",
])
def test_old_engine_paths_still_resolve_via_shim(modpath):
    """v1's deep importers (state/, judge/) must keep working byte-stable."""
    mod = importlib.import_module(modpath)
    from app.modules.interview_runtime import results
    # the shim re-exports the SAME class object, not a copy
    if modpath.endswith("ledger"):
        assert mod.SignalLedgerSnapshot is results.SignalLedgerSnapshot
        assert mod.CoverageState is results.CoverageState
    elif modpath.endswith("queue"):
        assert mod.QuestionQueueSnapshot is results.QuestionQueueSnapshot
    else:
        assert mod.ClaimsPoolSnapshot is results.ClaimsPoolSnapshot


def test_interview_runtime_schemas_does_not_import_interview_engine():
    """Static guard: schemas.py must not import from app.modules.interview_engine
    (CMI-1 end state — so deleting interview_engine in M6 can't break build_session_config)."""
    src = Path("app/modules/interview_runtime/schemas.py").read_text()
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.startswith("app.modules.interview_engine"):
            offenders.append(node.module)
    assert offenders == [], f"interview_runtime.schemas still imports v1 models: {offenders}"
