"""Group a session transcript into logical answers ("answer runs").

The interview engine commits one spoken answer as MULTIPLE transcript turns
(continuation segments). A reel clip must be able to reference the whole answer,
not one fragment. An **answer run** is a maximal sequence of consecutive candidate
turns with NO agent turn between them, which gives three properties at once:

  * contiguous video — no agent audio falls inside the run (a single clean cut),
  * one logical answer — consecutive candidate turns share a ``question_id``,
  * a continuous word-index space across the run (the Director selects over it).

Each word remembers its origin turn (``turn_commit``) and turn-relative timing, so
the renderer maps every word to video via that turn's VAD span (``timing.answer_span``)
and cuts one contiguous range from the first word's video time to the last's.

Pure + lean (no heavy imports); consumed by both the Director and the renderer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Sentinel ``gap_before_ms`` at a turn boundary: the real inter-turn pause is not
# knowable in transcript space (commit-lagged), but it is DEFINITELY a pause.
TURN_BOUNDARY_GAP = -1


@dataclass
class RunWord:
    idx: int                # continuous index across the run
    text: str
    turn_commit: int        # the committing turn (for video mapping)
    rel_start_ms: int       # turn-relative (first word of its turn = 0)
    rel_end_ms: int
    gap_before_ms: int      # pause before this word; TURN_BOUNDARY_GAP at a turn edge


@dataclass
class AnswerRun:
    ref: int                # first turn's commit — the Director's source_turn_ref
    question_id: str | None
    turns: list[int] = field(default_factory=list)   # member commits, in order
    words: list[RunWord] = field(default_factory=list)


def is_pause_before(word: RunWord, *, threshold_ms: int = 400) -> bool:
    """Whether a natural pause precedes ``word`` (turn boundary or a long gap)."""
    return word.gap_before_ms < 0 or word.gap_before_ms >= threshold_ms


def answer_runs(transcript: list[dict]) -> list[AnswerRun]:
    """Group the transcript into answer runs (see module docstring)."""
    runs: list[AnswerRun] = []
    cur: AnswerRun | None = None
    idx = 0
    prev_end: int | None = None   # previous word's rel_end within the SAME turn

    for turn in transcript:
        if turn.get("role") != "candidate":
            cur = None             # agent turn ends the contiguous run
            continue
        commit = turn.get("timestamp_ms")
        if commit is None:
            continue
        commit = int(commit)
        if cur is None:
            cur = AnswerRun(ref=commit, question_id=turn.get("question_id"))
            runs.append(cur)
            idx = 0
        cur.turns.append(commit)

        first_in_turn = True
        for w in turn.get("words") or []:
            rel_start, rel_end = int(w["start_ms"]), int(w["end_ms"])
            if not cur.words:
                gap = 0                       # very first word of the run
            elif first_in_turn:
                gap = TURN_BOUNDARY_GAP       # continuation turn boundary
            else:
                gap = rel_start - prev_end
            cur.words.append(RunWord(idx=idx, text=str(w["text"]), turn_commit=commit,
                                     rel_start_ms=rel_start, rel_end_ms=rel_end,
                                     gap_before_ms=gap))
            idx += 1
            prev_end = rel_end
            first_in_turn = False

    return runs
