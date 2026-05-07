"""bank_resolver — pure function: JudgeOutput + queue → bank text + instruction kind.

Phase 3 fully implements this. For Phase 2, the StateEngine handles InstructionKind
resolution itself; this file is reserved for the orchestrator-level resolver.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.interview_engine.models.speaker import InstructionKind


@dataclass(slots=True)
class ResolvedBankText:
    instruction_kind: InstructionKind
    bank_text: str | None
    failed_signal_value: str | None = None


def resolve_bank_text(*args, **kwargs) -> ResolvedBankText:  # pragma: no cover
    raise NotImplementedError("resolve_bank_text is implemented in Phase 3")
