"""
Tests for compute_provenance — §7.1 deterministic provenance pass.

Every branch of the rule is covered, plus all edge-cases called out in the spec:
  1. asked_directly        — own-question support note
  2. cross_credited        — support note from a DIFFERENT question
  3. probed_absent         — own question asked fairly, zero support notes
  4. not_reached (no Q)    — no own-question, no notes
  5. truncated → not_reached — own question asked but truncated (time-cut), no support
  6. disclaim → probed_absent — own question asked (absent closure), only contradicts note
  7. rule-1-wins           — own-question support + other-question support → asked_directly
  8. identity preserved    — all non-provenance fields survive model_copy unchanged
"""

from __future__ import annotations

import pytest

from app.modules.interview_engine.notes import compute_provenance
from app.modules.interview_runtime.evidence import (
    EvidenceNote,
    EvidenceStance,
    EvidenceTexture,
    Provenance,
    QuestionOutcome,
    QuestionRecord,
    SignalEvidence,
    SignalPriority,
    SignalType,
    ThreadClosure,
    TimeSpan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _span() -> TimeSpan:
    return TimeSpan(start_ms=0, end_ms=1000)


def _signal(signal: str = "python", provenance: Provenance = Provenance.not_reached) -> SignalEvidence:
    return SignalEvidence(
        signal=signal,
        signal_type=SignalType.competency,
        weight=2,
        priority=SignalPriority.required,
        knockout=False,
        provenance=provenance,
    )


def _question(
    question_id: str,
    primary_signal: str,
    outcome: QuestionOutcome = QuestionOutcome.asked,
    closure: ThreadClosure | None = ThreadClosure.satisfied,
) -> QuestionRecord:
    return QuestionRecord(
        question_id=question_id,
        primary_signal=primary_signal,
        outcome=outcome,
        closure=closure,
        probes_available=2,
    )


def _note(
    seq: int,
    signal: str,
    stance: EvidenceStance,
    from_question_id: str,
) -> EvidenceNote:
    return EvidenceNote(
        seq=seq,
        turn_ref=f"t-{seq}",
        signal=signal,
        stance=stance,
        texture=EvidenceTexture.concrete,
        quote="some candidate words",
        span=_span(),
        from_question_id=from_question_id,
        via_probe=False,
    )


# ---------------------------------------------------------------------------
# §7.1 — Rule 1: asked_directly
# ---------------------------------------------------------------------------

class TestAskedDirectly:
    def test_support_from_own_question(self) -> None:
        """Signal has an asked own-question and a supports note from that question."""
        sig = _signal("python")
        q = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.satisfied)
        note = _note(1, "python", EvidenceStance.supports, from_question_id="q-1")

        result = compute_provenance(signals=[sig], notes=[note], questions=[q])

        assert len(result) == 1
        assert result[0].provenance == Provenance.asked_directly

    def test_asked_directly_with_tapped_out_closure(self) -> None:
        """closure=tapped_out is also a fair elicitation; support → asked_directly."""
        sig = _signal("python")
        q = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.tapped_out)
        note = _note(1, "python", EvidenceStance.supports, from_question_id="q-1")

        result = compute_provenance(signals=[sig], notes=[note], questions=[q])

        assert result[0].provenance == Provenance.asked_directly


# ---------------------------------------------------------------------------
# §7.1 — Rule 2: cross_credited
# ---------------------------------------------------------------------------

class TestCrossCredited:
    def test_support_only_from_other_question(self) -> None:
        """Signal S has NO own-question; a supports note came from a DIFFERENT question."""
        sig = _signal("python")
        # "q-other" targets a different primary_signal, so it is NOT an own-question for S
        q_other = _question("q-other", "django", outcome=QuestionOutcome.asked, closure=ThreadClosure.satisfied)
        note = _note(1, "python", EvidenceStance.supports, from_question_id="q-other")

        result = compute_provenance(signals=[sig], notes=[note], questions=[q_other])

        assert result[0].provenance == Provenance.cross_credited

    def test_own_question_not_reached_but_support_from_other(self) -> None:
        """Own-question exists but was not_reached; support came from another question → cross_credited."""
        sig = _signal("python")
        q_own = _question("q-1", "python", outcome=QuestionOutcome.not_reached, closure=None)
        q_other = _question("q-other", "django", outcome=QuestionOutcome.asked, closure=ThreadClosure.satisfied)
        note = _note(1, "python", EvidenceStance.supports, from_question_id="q-other")

        result = compute_provenance(signals=[sig], notes=[note], questions=[q_own, q_other])

        assert result[0].provenance == Provenance.cross_credited


# ---------------------------------------------------------------------------
# §7.1 — Rule 3: probed_absent
# ---------------------------------------------------------------------------

class TestProbedAbsent:
    def test_asked_fairly_no_supporting_notes(self) -> None:
        """Own question asked (satisfied closure), no notes at all → real negative."""
        sig = _signal("python")
        q = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.satisfied)

        result = compute_provenance(signals=[sig], notes=[], questions=[q])

        assert result[0].provenance == Provenance.probed_absent

    def test_only_contradicts_note_still_probed_absent(self) -> None:
        """Spec §7.1: a contradicts note is NOT a supporting note → probed_absent."""
        sig = _signal("python")
        q = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.absent)
        contradicts_note = _note(1, "python", EvidenceStance.contradicts, from_question_id="q-1")

        result = compute_provenance(signals=[sig], notes=[contradicts_note], questions=[q])

        assert result[0].provenance == Provenance.probed_absent

    def test_tapped_out_no_notes_probed_absent(self) -> None:
        """tapped_out is a fair elicitation; no support → probed_absent."""
        sig = _signal("python")
        q = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.tapped_out)

        result = compute_provenance(signals=[sig], notes=[], questions=[q])

        assert result[0].provenance == Provenance.probed_absent


# ---------------------------------------------------------------------------
# §7.1 — Rule 4: not_reached (no data)
# ---------------------------------------------------------------------------

class TestNotReached:
    def test_no_question_no_notes(self) -> None:
        """Signal with no own-question and no notes → not_reached."""
        sig = _signal("python")

        result = compute_provenance(signals=[sig], notes=[], questions=[])

        assert result[0].provenance == Provenance.not_reached

    def test_only_not_reached_question_no_notes(self) -> None:
        """Own question exists but was not_reached and no notes → not_reached."""
        sig = _signal("python")
        q = _question("q-1", "python", outcome=QuestionOutcome.not_reached, closure=None)

        result = compute_provenance(signals=[sig], notes=[], questions=[q])

        assert result[0].provenance == Provenance.not_reached


# ---------------------------------------------------------------------------
# §7.1 — Edge case: truncated → not_reached (time-cut thread is NOT a real negative)
# ---------------------------------------------------------------------------

class TestTruncatedIsNotReached:
    def test_truncated_own_question_no_support_gives_not_reached(self) -> None:
        """Spec says: truncated = time-budget cut; stays not_reached, never probed_absent."""
        sig = _signal("python")
        q = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.truncated)

        result = compute_provenance(signals=[sig], notes=[], questions=[q])

        assert result[0].provenance == Provenance.not_reached

    def test_truncated_with_contradicts_note_not_reached(self) -> None:
        """truncated + only a contradicts note → not_reached (truncated wins over disclaim rule)."""
        sig = _signal("python")
        q = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.truncated)
        contradicts_note = _note(1, "python", EvidenceStance.contradicts, from_question_id="q-1")

        result = compute_provenance(signals=[sig], notes=[contradicts_note], questions=[q])

        assert result[0].provenance == Provenance.not_reached


# ---------------------------------------------------------------------------
# §7.1 — Edge case: disclaim → probed_absent
# ---------------------------------------------------------------------------

class TestDisclaimProbedAbsent:
    def test_own_question_absent_closure_only_contradicts(self) -> None:
        """Spec §7.1 disclaim case: closure=absent + contradicts note → probed_absent."""
        sig = _signal("python")
        q = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.absent)
        contradicts_note = _note(1, "python", EvidenceStance.contradicts, from_question_id="q-1")

        result = compute_provenance(signals=[sig], notes=[contradicts_note], questions=[q])

        assert result[0].provenance == Provenance.probed_absent


# ---------------------------------------------------------------------------
# §7.1 — Rule 1 wins over Rule 2: own-question support beats other-question support
# ---------------------------------------------------------------------------

class TestRule1WinsOverRule2:
    def test_own_question_support_and_other_question_support_gives_asked_directly(self) -> None:
        """When both an own-question support note AND a cross-question support note exist,
        Rule 1 applies (asked_directly) — the first match wins."""
        sig = _signal("python")
        q_own = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.satisfied)
        q_other = _question("q-other", "django", outcome=QuestionOutcome.asked, closure=ThreadClosure.satisfied)
        own_note = _note(1, "python", EvidenceStance.supports, from_question_id="q-1")
        other_note = _note(2, "python", EvidenceStance.supports, from_question_id="q-other")

        result = compute_provenance(signals=[sig], notes=[own_note, other_note], questions=[q_own, q_other])

        assert result[0].provenance == Provenance.asked_directly


# ---------------------------------------------------------------------------
# Identity contract: non-provenance fields survive unchanged; one result per signal
# ---------------------------------------------------------------------------

class TestIdentityAndReturnContract:
    def test_returns_one_result_per_input_signal(self) -> None:
        """compute_provenance returns exactly len(signals) results in the same order."""
        sigs = [_signal("python"), _signal("django"), _signal("sql")]

        result = compute_provenance(signals=sigs, notes=[], questions=[])

        assert len(result) == 3
        assert [r.signal for r in result] == ["python", "django", "sql"]

    def test_identity_fields_preserved(self) -> None:
        """All non-provenance fields on SignalEvidence survive model_copy unchanged."""
        sig = SignalEvidence(
            signal="react",
            signal_type=SignalType.experience,
            weight=3,
            priority=SignalPriority.preferred,
            knockout=True,
            provenance=Provenance.not_reached,  # will be overwritten
        )

        result = compute_provenance(signals=[sig], notes=[], questions=[])

        out = result[0]
        assert out.signal == "react"
        assert out.signal_type == SignalType.experience
        assert out.weight == 3
        assert out.priority == SignalPriority.preferred
        assert out.knockout is True
        # provenance was not_reached and stays not_reached here (no Q/notes)
        assert out.provenance == Provenance.not_reached

    def test_inputs_not_mutated(self) -> None:
        """compute_provenance must not mutate the input signals list."""
        sig = _signal("python", provenance=Provenance.not_reached)
        original_provenance = sig.provenance

        compute_provenance(signals=[sig], notes=[], questions=[])

        assert sig.provenance == original_provenance

    def test_multi_signal_mixed_provenance(self) -> None:
        """Multiple signals each get the correct provenance independently."""
        sig_a = _signal("python")    # will be asked_directly
        sig_b = _signal("django")    # will be not_reached

        q_a = _question("q-1", "python", outcome=QuestionOutcome.asked, closure=ThreadClosure.satisfied)
        note_a = _note(1, "python", EvidenceStance.supports, from_question_id="q-1")

        result = compute_provenance(signals=[sig_a, sig_b], notes=[note_a], questions=[q_a])

        assert result[0].provenance == Provenance.asked_directly
        assert result[1].provenance == Provenance.not_reached
