"""Group a session transcript into logical answers ("answer runs").

The interview engine commits one spoken answer as MULTIPLE transcript turns
(continuation segments). A reel clip must be able to reference the whole answer,
not one fragment. An **answer run** is a maximal sequence of consecutive candidate
turns with NO agent turn between them, which gives three properties at once:

  * contiguous video — no agent audio falls inside the run (a single clean cut),
  * one logical answer — consecutive candidate turns share a ``question_id``,
  * a continuous word-index space across the run (the Director selects over it).

Reads the gen-3 ``SessionEvidence.transcript`` shape: each turn carries ``speaker``
("agent"/"candidate"), ``turn_ref`` (str), ``span: {start_ms, end_ms}`` (session-
relative ms) and ``words`` (turn-relative ms, first word=0). Each ``RunWord``
remembers its origin turn (``turn_ref`` + that turn's ``turn_start_ms``), so the
renderer maps every word to video via ``video_ms = turn_start_ms + rel + offset``
and cuts one contiguous range from the first word's video time to the last's.

Pure + lean (no heavy imports); consumed by both the Director and the renderer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Sentinel ``gap_before_ms`` at a turn boundary: the real inter-turn pause is not
# knowable in transcript space, but it is DEFINITELY a pause.
TURN_BOUNDARY_GAP = -1


@dataclass
class RunWord:
    idx: int                # continuous index across the run
    text: str
    turn_ref: str           # the word's turn (for video mapping + boundary grouping)
    turn_start_ms: int      # the word's turn's span.start_ms (session-relative)
    rel_start_ms: int       # turn-relative (first word of its turn = 0)
    rel_end_ms: int
    gap_before_ms: int      # pause before this word; TURN_BOUNDARY_GAP at a turn edge


@dataclass
class AnswerRun:
    ref: int                # sequential 0-based RUN INDEX — the Director's source_turn_ref
    question_id: str | None
    turns: list[str] = field(default_factory=list)   # member turn_refs, in order
    words: list[RunWord] = field(default_factory=list)


def is_pause_before(word: RunWord, *, threshold_ms: int = 400) -> bool:
    """Whether a natural pause precedes ``word`` (turn boundary or a long gap)."""
    return word.gap_before_ms < 0 or word.gap_before_ms >= threshold_ms


def answer_runs(transcript: list[dict]) -> list[AnswerRun]:
    """Group the gen-3 transcript into answer runs (see module docstring)."""
    runs: list[AnswerRun] = []
    cur: AnswerRun | None = None
    idx = 0
    prev_end: int | None = None   # previous word's rel_end within the SAME turn

    for turn in transcript:
        if turn.get("speaker") != "candidate":
            cur = None             # agent turn ends the contiguous run
            continue
        turn_ref = turn.get("turn_ref")
        if turn_ref is None:
            continue
        turn_ref = str(turn_ref)
        turn_start_ms = int((turn.get("span") or {}).get("start_ms") or 0)
        if cur is None:
            cur = AnswerRun(ref=len(runs), question_id=turn.get("question_id"))
            runs.append(cur)
            idx = 0
        cur.turns.append(turn_ref)

        first_in_turn = True
        for w in turn.get("words") or []:
            rel_start, rel_end = int(w["start_ms"]), int(w["end_ms"])
            if not cur.words:
                gap = 0                       # very first word of the run
            elif first_in_turn:
                gap = TURN_BOUNDARY_GAP       # continuation turn boundary
            else:
                gap = rel_start - prev_end
            cur.words.append(RunWord(idx=idx, text=str(w["text"]), turn_ref=turn_ref,
                                     turn_start_ms=turn_start_ms,
                                     rel_start_ms=rel_start, rel_end_ms=rel_end,
                                     gap_before_ms=gap))
            idx += 1
            prev_end = rel_end
            first_in_turn = False

    return runs


def questions_by_run(transcript: list[dict]) -> list[str | None]:
    """Interviewer question text immediately preceding each answer run, in run
    order (aligned to ``answer_runs`` ``ref``). Consecutive agent turns before a
    run are joined; a run with no preceding agent turn yields ``None``."""
    out: list[str | None] = []
    pending: list[str] = []
    in_run = False
    for turn in transcript:
        if turn.get("speaker") != "candidate":
            txt = (turn.get("text") or
                   " ".join(str(w.get("text", "")) for w in (turn.get("words") or []))).strip()
            if txt:
                pending.append(txt)
            in_run = False
            continue
        if turn.get("turn_ref") is None:
            continue
        if not in_run:
            out.append(" ".join(pending).strip() or None)
            pending = []
            in_run = True
    return out
