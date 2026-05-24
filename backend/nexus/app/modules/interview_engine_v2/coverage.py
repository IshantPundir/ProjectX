"""Signal coverage model for the v2 brain (pure — no livekit, no LLM).

Coverage is SIGNAL-based, credited across the whole conversation (doc 09 §1): an answer
that demonstrates any signal updates that signal's state now, regardless of which question
is "active". Thread-satisfaction (doc 09 §4) — not turn-count — drives progress: a thread
is satisfied when its primary signal is `sufficient` OR `failed`/absent OR the candidate
has `tapped_out` (the brain's diminishing-returns judgment) OR a soft probe cap is hit.
The brain PROPOSES a per-signal coverage_delta each turn; this tracker MERGES it
deterministically (`sufficient` is sticky; `failed` is a REVISABLE knockout-candidate —
decision C / re-open-closed-thread) and is the running source of truth fed back into the
next brain prompt as a compact delta.
v2-native (NOT the v1 CoverageState in interview_runtime.results).
"""
from __future__ import annotations

from enum import StrEnum


class CoverageState(StrEnum):
    none = "none"          # no evidence yet
    partial = "partial"    # some evidence, not conclusive
    sufficient = "sufficient"  # enough credible evidence (terminal — thread covered)
    # evidence they lack it; revisable knockout-candidate (re-opened by new evidence)
    failed = "failed"


class ThreadStatus(StrEnum):
    in_progress = "in_progress"
    sufficient = "sufficient"
    tapped_out = "tapped_out"
    absent = "absent"


# rank for the monotonic none->partial->sufficient ladder. `failed` is handled separately
# (reachable from any non-sufficient state, and REVISABLE — decision C / re-open-closed-thread).
_RANK: dict[CoverageState, int] = {
    CoverageState.none: 0,
    CoverageState.partial: 1,
    CoverageState.sufficient: 2,
}
# "thread closed" for preemptive-skip / uncovered_mandatory — NOT "immutable": a `failed` thread
# isn't proactively re-probed, but volunteered new evidence can still re-open it (apply_delta).
_TERMINAL: frozenset[CoverageState] = frozenset({CoverageState.sufficient, CoverageState.failed})


class CoverageTracker:
    """Per-signal coverage + per-question probe counts + thread-satisfaction."""

    def __init__(
        self,
        *,
        signals: list[str],
        mandatory_signals: list[str],
        soft_probe_cap: int = 2,
    ) -> None:
        self._state: dict[str, CoverageState] = {s: CoverageState.none for s in signals}
        self._mandatory: list[str] = list(mandatory_signals)
        self._probe_counts: dict[str, int] = {}
        self._used_follow_ups: dict[str, set[int]] = {}
        self._cap = soft_probe_cap

    def apply_delta(self, delta: dict[str, str]) -> dict[str, CoverageState]:
        """Merge a brain-proposed per-signal delta. Returns only the entries that changed.

        Merge rules (decision C — re-open-closed-thread):
        - `sufficient` is STICKY against WEAKER turns (`partial`/`none`): a covered signal is never
          un-covered by modesty, a bare 'yes', or a follow-up that adds nothing. But an explicit
          `failed` delta DOES flip it (the candidate retracts/contradicts the claim — "actually I
          was lying, I've never used it"). The brain only proposes `failed` on a genuine
          contradiction, so honoring it keeps coverage truthful (d9828b7b talk-test: Workato stayed
          'sufficient' after the candidate confessed they'd lied). `sufficient` and `failed` thus
          flip between each other on explicit evidence; neither is downgraded by `partial`/`none`.
        - `failed` is REVISABLE: it's a knockout *candidate*, not a lock. A later `partial`/
          `sufficient` (the candidate volunteers contradicting evidence) re-opens it; a `none`/
          `failed` delta leaves it failed. (Prevents prematurely locking an absence — the b99d8cc6
          lesson + the research's "evidence credited across the whole conversation", docs 08/09.)
        - From `none`/`partial`: monotonic up the rank ladder, or a jump to `failed` (a discovered
          absence). A lower-rank proposal is ignored (the brain can't silently un-credit a signal).
        """
        applied: dict[str, CoverageState] = {}
        for sig, raw in delta.items():
            new = CoverageState(raw)
            cur = self._state.get(sig, CoverageState.none)
            if cur is CoverageState.sufficient:
                if new is CoverageState.failed:             # explicit retraction flips it down
                    self._state[sig] = new
                    applied[sig] = new
                continue                                    # partial/none/sufficient: stays covered
            if cur is CoverageState.failed:
                if new in (CoverageState.partial, CoverageState.sufficient):
                    self._state[sig] = new  # new evidence re-opens the knockout candidate
                    applied[sig] = new
                continue                                    # 'none'/'failed' leave it failed
            # cur in {none, partial}: monotonic up, or a jump to failed
            if (new is CoverageState.failed or _RANK[new] >= _RANK[cur]) and new is not cur:
                self._state[sig] = new
                applied[sig] = new
        return applied

    def record_probe(self, question_id: str) -> None:
        self._probe_counts[question_id] = self._probe_counts.get(question_id, 0) + 1

    def probe_count(self, question_id: str) -> int:
        return self._probe_counts.get(question_id, 0)

    def at_probe_cap(self, question_id: str) -> bool:
        """True once this question has been probed soft_probe_cap times (diminishing returns)."""
        return self.probe_count(question_id) >= self._cap

    def record_follow_up(self, question_id: str, idx: int) -> None:
        self._used_follow_ups.setdefault(question_id, set()).add(idx)

    def used_follow_ups(self, question_id: str) -> frozenset[int]:
        return frozenset(self._used_follow_ups.get(question_id, set()))

    def state(self, signal: str) -> CoverageState:
        return self._state.get(signal, CoverageState.none)

    def is_covered(self, signal: str) -> bool:
        """True when the thread is closed (sufficient OR failed) — used for preemptive skip."""
        return self.state(signal) in _TERMINAL

    def thread_status(
        self, *, primary_signal: str, question_id: str, tapped_out: bool
    ) -> ThreadStatus:
        st = self.state(primary_signal)
        if st is CoverageState.sufficient:
            return ThreadStatus.sufficient
        if st is CoverageState.failed:
            return ThreadStatus.absent
        if tapped_out or self.probe_count(question_id) >= self._cap:
            return ThreadStatus.tapped_out
        return ThreadStatus.in_progress

    def uncovered_mandatory(self) -> list[str]:
        return [s for s in self._mandatory if not self.is_covered(s)]

    def failed_mandatory(self) -> list[str]:
        """Mandatory signals the candidate has disclaimed/retracted (state == failed).

        Each is a KNOCKOUT trigger: the role requires it, so the screen is over. `is_covered`
        treats `failed` as a closed thread (don't re-probe), which is why a failed MANDATORY signal
        otherwise reads as "satisfied, advance" — surfacing it separately lets the brain finish the
        confirm→knockout_close instead of advancing past it (b33f4ed5). `failed` stays REVISABLE, so
        the confirm step still gives the candidate a chance to correct a mishearing."""
        return [s for s in self._mandatory if self.state(s) is CoverageState.failed]

    def summary_for_prompt(self) -> str:
        """Compact single-line projection for the brain prompt's dynamic suffix (bounded)."""
        return ", ".join(f"{s}={st.value}" for s, st in self._state.items())

    def summary_for_result(self) -> dict[str, str]:
        """Per-signal final state for SessionResult.coverage_summary."""
        return {s: st.value for s, st in self._state.items()}
