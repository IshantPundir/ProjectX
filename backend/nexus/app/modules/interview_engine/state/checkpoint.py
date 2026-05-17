"""EngineCheckpoint — full in-memory state snapshot for crash recovery."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.models.ledger import SignalLedgerSnapshot
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_engine.state.lifecycle import LifecycleSnapshot
from app.modules.interview_runtime.models import TranscriptEntry


class EngineCheckpoint(BaseModel):
    """Full in-memory engine state for crash recovery and forensic inspection.

    Stored in sessions.engine_checkpoint JSONB. Written every N turns or N seconds
    (whichever first) per ENGINE_CHECKPOINT_TURNS / ENGINE_CHECKPOINT_SECONDS.

    Schema versions:

    * v1 (pre-2026-05-17): ledger + queue + claims + lifecycle only. Crash
      recovery worked for the four sub-state machines but did NOT preserve
      _turn_count, _transcript, _question_utterances — those re-initialized
      on rehydration. Acceptable for crash recovery because the agent was
      reborn fresh.
    * v2 (2026-05-17): adds turn_count, transcript, question_utterances so
      the checkpoint round-trips the orchestrator's full mutable state.
      Required for in-turn snapshot/restore (continuation cancellation),
      where the rollback target IS the exact pre-turn state.

    Backward compatibility: v1 checkpoints persisted in the database load
    cleanly via the safe defaults (turn_count=0, transcript=[],
    question_utterances={}). Migration is opportunistic — the next
    checkpoint write upgrades the row to v2.
    """

    schema_version: int = Field(default=2, ge=1)
    session_id: str
    ledger: SignalLedgerSnapshot
    queue: QuestionQueueSnapshot
    claims: ClaimsPoolSnapshot
    lifecycle: LifecycleSnapshot
    last_audit_seq_flushed: int = Field(ge=0)
    captured_at_ms: int = Field(ge=0)

    # New in v2 — required for in-turn rollback. Crash-recovery code paths
    # that load an older v1 checkpoint get safe defaults.
    turn_count: int = Field(default=0, ge=0)
    transcript: list[TranscriptEntry] = Field(default_factory=list)
    question_utterances: dict[str, str] = Field(default_factory=dict)
