"""The snapshot models live in interview_runtime.results; interview_runtime never imports
the engine module (so build_session_config can't be broken by engine changes)."""
import ast
from pathlib import Path


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


def test_interview_runtime_schemas_does_not_import_interview_engine():
    """Static guard: schemas.py must not import from app.modules.interview_engine
    (CMI-1 end state — so deleting interview_engine in M6 can't break build_session_config)."""
    # anchored to backend/nexus/ regardless of pytest CWD (this file is tests/interview_runtime/<f>)
    src = (Path(__file__).resolve().parents[2] / "app/modules/interview_runtime/schemas.py").read_text()
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.startswith("app.modules.interview_engine"):
            offenders.append(node.module)
    assert offenders == [], f"interview_runtime.schemas still imports v1 models: {offenders}"
