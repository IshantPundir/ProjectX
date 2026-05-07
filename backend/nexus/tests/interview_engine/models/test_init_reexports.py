"""Verify every model class is reachable from the package root."""
from app.modules.interview_engine import models


def test_judge_models_reexported():
    for name in (
        "NextAction", "CoverageTransition",
        "Observation", "TurnMetadata",
        "AdvancePayload", "ProbePayload", "ClarifyPayload", "RepeatPayload",
        "RedirectPayload", "AcknowledgeNoExperiencePayload",
        "PoliteClosePayload", "EndSessionPayload",
        "JudgeOutput", "JudgeClaimEntry",
    ):
        assert hasattr(models, name), f"{name} not re-exported"


def test_speaker_models_reexported():
    for name in ("InstructionKind", "SpeakerInput"):
        assert hasattr(models, name)


def test_ledger_models_reexported():
    for name in ("CoverageState", "LedgerEntry", "SignalSnapshot", "SignalLedgerSnapshot"):
        assert hasattr(models, name)


def test_queue_models_reexported():
    for name in ("QuestionStatus", "QuestionState", "QuestionQueueSnapshot"):
        assert hasattr(models, name)


def test_claims_models_reexported():
    for name in ("ClaimEntry", "ClaimsPoolSnapshot"):
        assert hasattr(models, name)


def test_canonical_claim_entry_has_capture_metadata():
    """Verify the re-exported ClaimEntry is the canonical (claims.py) one."""
    from app.modules.interview_engine.models import ClaimEntry
    fields = ClaimEntry.model_fields
    assert "captured_at_turn" in fields
    assert "captured_at_seq" in fields


def test_judge_claim_entry_separately_reachable():
    """JudgeClaimEntry is the narrower Judge-emitted shape; rename to avoid clash."""
    from app.modules.interview_engine.models import JudgeClaimEntry
    fields = JudgeClaimEntry.model_fields
    assert "captured_at_turn" not in fields
    assert "captured_at_seq" not in fields
