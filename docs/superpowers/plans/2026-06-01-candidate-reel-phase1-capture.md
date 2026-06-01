# Candidate Reel — Phase 1 (Live Capture) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture word-level timing for every candidate turn during a live interview and persist it on `sessions.transcript`, so a later "candidate reel" can cut frame-accurate clips and time captions.

**Architecture:** The interview engine (`app/modules/interview_engine/agent.py`) is a LiveKit `Agent`. Today it records each candidate turn as a `TranscriptEntry{role,text,timestamp_ms,question_id}` in `on_user_turn_completed`, reading only `new_message.text_content`. We add an `stt_node` override that tees `FINAL_TRANSCRIPT` `SpeechEvent`s and buffers their per-word timings (LiveKit `SpeechData.words: list[TimedString]` with `text`/`start_time`/`end_time`/`confidence`). At turn commit we attach those words (normalized to **within-turn relative milliseconds**, first word = 0) plus best-effort `start_ms`/`end_ms` anchored to the existing `timestamp_ms`. All timing math lives in a pure, livekit-free helper module so it is unit-testable without the realtime stack.

**Deviation from spec §5.1 (intentional, honest):** the spec proposed per-turn `start_ms`/`end_ms` on the session clock and absolute word times. STT word times are on the *audio-stream clock*, which differs from both the session clock and the recording (egress) clock by an amount only measurable against a **real recording**. Rather than fake that precision, Phase 1 stores **within-turn relative** word offsets (clock-agnostic, exact) + anchors the turn to the existing `timestamp_ms`. **Offset/clock calibration (spec §5.3) moves to Phase 2** — it logically *cannot* be done in Phase 1 because it requires the first real recording to measure against, which is exactly the session the operator runs *after* this phase. The calibration anchors already persist for free: the audit envelope's first event carries the engine run-start `wall_ms`, and LiveKit's egress info carries the egress start time. No new capture is needed for it.

**Tech Stack:** Python 3.13, Pydantic v2, LiveKit Agents (`livekit.agents.Agent`, `stt.SpeechEvent`), pytest.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `app/modules/interview_runtime/models.py` | Wire-format transcript models | **Modify** — add `WordTiming`; extend `TranscriptEntry` |
| `app/modules/interview_engine/transcript_timing.py` | Pure, livekit-free timing math (tuples → relative `WordTiming`; turn bounds) | **Create** |
| `app/modules/interview_engine/agent.py` | LiveKit agent; word buffer + `stt_node` tee + attach at commit | **Modify** |
| `tests/interview_runtime/test_transcript_entry_words.py` | Model round-trip + backward-compat | **Create** |
| `tests/interview_engine/test_transcript_timing.py` | Pure helper unit tests | **Create** |
| `tests/interview_engine/test_word_capture.py` | Event-tee + commit-attach behavior | **Create** |

**Test invocation note:** tests that import `app.modules.interview_engine.agent` pull in `livekit.agents`, which segfaults under `pytest --cov` on Python 3.13 (see backend `CLAUDE.md` → "Coverage in Docker"). Run engine tests **without** the cov plugin, or via the documented `python -m coverage run … -m pytest` workaround. The model test and the `transcript_timing` test are livekit-free and run under plain `pytest`.

---

## Task 1: `WordTiming` model + extend `TranscriptEntry`

**Files:**
- Modify: `app/modules/interview_runtime/models.py`
- Test: `tests/interview_runtime/test_transcript_entry_words.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_runtime/test_transcript_entry_words.py
from app.modules.interview_runtime.models import TranscriptEntry, WordTiming


def test_word_timing_round_trips():
    w = WordTiming(text="hello", start_ms=0, end_ms=320, confidence=0.98)
    assert WordTiming.model_validate(w.model_dump()) == w


def test_transcript_entry_accepts_words_and_bounds():
    entry = TranscriptEntry(
        role="candidate",
        text="hello world",
        timestamp_ms=42000,
        question_id="q1",
        start_ms=41000,
        end_ms=42000,
        words=[
            WordTiming(text="hello", start_ms=0, end_ms=320, confidence=0.98),
            WordTiming(text="world", start_ms=360, end_ms=700, confidence=0.95),
        ],
    )
    dumped = entry.model_dump(mode="json")
    assert dumped["words"][1]["text"] == "world"
    assert TranscriptEntry.model_validate(dumped) == entry


def test_transcript_entry_backward_compatible():
    # Old rows (no words/start_ms/end_ms) must still parse with defaults.
    entry = TranscriptEntry(role="agent", text="Hi.", timestamp_ms=3049)
    assert entry.words is None
    assert entry.start_ms is None
    assert entry.end_ms is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_transcript_entry_words.py -v`
Expected: FAIL — `ImportError: cannot import name 'WordTiming'`.

- [ ] **Step 3: Write minimal implementation**

In `app/modules/interview_runtime/models.py`, add the `WordTiming` model and three optional fields to `TranscriptEntry`:

```python
class WordTiming(BaseModel):
    """One spoken word, timed RELATIVE to the start of its turn (first word = 0).

    Relative offsets are clock-agnostic and exact: they need no audio-stream /
    session / recording clock reconciliation. Absolute placement on the video
    timeline is resolved later (Phase 2) by anchoring the turn to its
    ``timestamp_ms`` and applying the calibrated recording offset.
    """

    text: str
    start_ms: int = Field(ge=0, description="Ms from the turn's first word.")
    end_ms: int = Field(ge=0, description="Ms from the turn's first word.")
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class TranscriptEntry(BaseModel):
    """A single utterance in the interview transcript."""

    role: Literal["agent", "candidate"]
    text: str
    timestamp_ms: int = Field(
        ge=0,
        description="Milliseconds since session start (turn commit anchor).",
    )
    question_id: str | None = None
    # Word-level timing (candidate turns only; agent turns are re-voiced, not
    # clipped, so they stay None). Added Phase 1 for the candidate reel.
    start_ms: int | None = Field(
        default=None, ge=0,
        description="Best-effort turn speech start on the session clock "
                    "(= timestamp_ms - spoken duration). None when unknown.",
    )
    end_ms: int | None = Field(
        default=None, ge=0,
        description="Best-effort turn speech end on the session clock "
                    "(= timestamp_ms). None when unknown.",
    )
    words: list[WordTiming] | None = None
```

Add the `Field` import if missing (it is already imported in this file).

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_transcript_entry_words.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_runtime/models.py tests/interview_runtime/test_transcript_entry_words.py
git commit -m "feat(reel): word-level timing fields on TranscriptEntry"
```

---

## Task 2: Pure helper — STT word tuples → relative `WordTiming`

**Files:**
- Create: `app/modules/interview_engine/transcript_timing.py`
- Test: `tests/interview_engine/test_transcript_timing.py`

This helper is **livekit-free** (takes plain tuples), so it runs under plain `pytest`.

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_engine/test_transcript_timing.py
from app.modules.interview_engine.transcript_timing import relative_words
from app.modules.interview_runtime.models import WordTiming


def test_relative_words_anchors_to_first_word():
    # (text, start_seconds, end_seconds, confidence) on the STT stream clock.
    raw = [
        ("six", 12.40, 12.72, 0.99),
        ("years", 12.80, 13.30, 0.97),
    ]
    out = relative_words(raw)
    assert out == [
        WordTiming(text="six", start_ms=0, end_ms=320, confidence=0.99),
        WordTiming(text="years", start_ms=400, end_ms=900, confidence=0.97),
    ]


def test_relative_words_empty():
    assert relative_words([]) == []


def test_relative_words_clamps_negative_drift_to_zero():
    # A later fragment whose stream time precedes the first word (clock jitter)
    # must never produce a negative offset.
    raw = [("a", 5.00, 5.10, 0.9), ("b", 4.98, 5.20, 0.9)]
    out = relative_words(raw)
    assert out[0].start_ms == 0
    assert out[1].start_ms == 0  # clamped, not -20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_transcript_timing.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.interview_engine.transcript_timing`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/modules/interview_engine/transcript_timing.py
"""Pure transcript-timing math for the candidate reel (Phase 1).

Deliberately livekit-free: it takes plain ``(text, start_s, end_s, confidence)``
tuples so it is unit-testable without the realtime stack. The engine's
``stt_node`` tee extracts those tuples from LiveKit ``TimedString`` words; this
module turns them into within-turn-relative ``WordTiming`` and computes the
turn's best-effort speech bounds.
"""
from __future__ import annotations

from app.modules.interview_runtime.models import WordTiming

RawWord = tuple[str, float, float, float]  # (text, start_s, end_s, confidence)


def relative_words(raw: list[RawWord]) -> list[WordTiming]:
    """Convert STT word tuples (seconds, stream clock) into WordTiming whose
    offsets are milliseconds relative to the first word (first word start = 0).
    Negative offsets from clock jitter are clamped to 0.
    """
    if not raw:
        return []
    base = raw[0][1]
    out: list[WordTiming] = []
    for text, start_s, end_s, conf in raw:
        start_ms = max(0, round((start_s - base) * 1000))
        end_ms = max(start_ms, round((end_s - base) * 1000))
        out.append(
            WordTiming(text=text, start_ms=start_ms, end_ms=end_ms, confidence=conf)
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_transcript_timing.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/transcript_timing.py tests/interview_engine/test_transcript_timing.py
git commit -m "feat(reel): relative_words timing helper"
```

---

## Task 3: Pure helper — turn speech bounds from anchor + words

**Files:**
- Modify: `app/modules/interview_engine/transcript_timing.py`
- Test: `tests/interview_engine/test_transcript_timing.py`

- [ ] **Step 1: Write the failing test** (append to the same test file)

```python
from app.modules.interview_engine.transcript_timing import turn_bounds


def test_turn_bounds_anchors_end_to_commit_and_back_off_duration():
    words = relative_words([("six", 12.40, 12.72, 0.99), ("years", 12.80, 13.30, 0.97)])
    # Commit (timestamp_ms) is the anchor; duration = last word end (900ms).
    start_ms, end_ms = turn_bounds(anchor_ms=42000, words=words)
    assert end_ms == 42000
    assert start_ms == 42000 - 900


def test_turn_bounds_no_words_returns_anchor_for_both():
    assert turn_bounds(anchor_ms=42000, words=[]) == (42000, 42000)


def test_turn_bounds_never_negative():
    words = relative_words([("x", 0.0, 50.0, 0.9)])  # 50s "word" (pathological)
    start_ms, end_ms = turn_bounds(anchor_ms=1000, words=words)
    assert start_ms == 0
    assert end_ms == 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_transcript_timing.py -v`
Expected: FAIL — `ImportError: cannot import name 'turn_bounds'`.

- [ ] **Step 3: Write minimal implementation** (append to `transcript_timing.py`)

```python
def turn_bounds(*, anchor_ms: int, words: list[WordTiming]) -> tuple[int, int]:
    """Best-effort turn speech bounds on the session clock.

    ``anchor_ms`` is the turn's commit timestamp (the existing ``timestamp_ms``).
    We treat it as the turn END and walk back by the spoken duration
    (last word's relative end). With no words, both bounds collapse to the
    anchor. Never returns a negative start.

    This is intentionally approximate (the commit fires after the endpointing
    silence, so the true speech end is slightly earlier). The reel render adds
    a safety pad and Phase 2 refines the absolute mapping against a real
    recording — see this plan's header.
    """
    if not words:
        return anchor_ms, anchor_ms
    duration_ms = words[-1].end_ms
    start_ms = max(0, anchor_ms - duration_ms)
    return start_ms, anchor_ms
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_transcript_timing.py -v`
Expected: PASS (6 passed total).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/transcript_timing.py tests/interview_engine/test_transcript_timing.py
git commit -m "feat(reel): turn_bounds timing helper"
```

---

## Task 4: Buffer STT words via an `stt_node` tee

**Files:**
- Modify: `app/modules/interview_engine/agent.py`
- Test: `tests/interview_engine/test_word_capture.py`

The candidate turn is committed in `on_user_turn_completed` (agent.py:388), which only sees
`new_message.text_content`. Word timings live on the STT `SpeechEvent`. We tee them by overriding
`stt_node`: it delegates to `Agent.default.stt_node` and, per event, hands each `FINAL_TRANSCRIPT`'s
words to a pure collector method that buffers raw tuples. The collector is unit-tested directly (no
async stream needed); the override is a thin wrapper.

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_engine/test_word_capture.py
import types

from app.modules.interview_engine.agent import _MouthAgent


def _bare_agent() -> _MouthAgent:
    # Bypass __init__ (it needs the full engine wiring); exercise only the
    # word-buffer logic on an otherwise-empty instance.
    a = _MouthAgent.__new__(_MouthAgent)
    a._pending_words = []
    return a


def _final_event(words):
    # Mimic stt.SpeechEvent: type==FINAL_TRANSCRIPT, alternatives[0].words[*]
    # carries .text/.start_time/.end_time/.confidence.
    word_objs = [
        types.SimpleNamespace(text=t, start_time=s, end_time=e, confidence=c)
        for (t, s, e, c) in words
    ]
    alt = types.SimpleNamespace(words=word_objs)
    return types.SimpleNamespace(type="final_transcript", alternatives=[alt])


def test_collect_appends_words_from_final_transcript():
    a = _bare_agent()
    a._collect_words_from_event(_final_event([("hi", 1.0, 1.2, 0.9)]))
    assert a._pending_words == [("hi", 1.0, 1.2, 0.9)]


def test_collect_ignores_non_final_and_empty():
    a = _bare_agent()
    interim = types.SimpleNamespace(type="interim_transcript", alternatives=[])
    a._collect_words_from_event(interim)
    a._collect_words_from_event(_final_event([]))  # final, but no words
    assert a._pending_words == []


def test_collect_accumulates_across_multiple_finals():
    a = _bare_agent()
    a._collect_words_from_event(_final_event([("a", 1.0, 1.1, 0.9)]))
    a._collect_words_from_event(_final_event([("b", 2.0, 2.1, 0.9)]))
    assert [w[0] for w in a._pending_words] == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_word_capture.py -v -p no:cacheprovider`
Expected: FAIL — `AttributeError: _MouthAgent has no attribute '_collect_words_from_event'`.

- [ ] **Step 3: Write minimal implementation**

In `agent.py`, import the STT event type near the other livekit imports:

```python
from livekit.agents import stt as lk_stt
```

In `_MouthAgent.__init__` (after `self._result_transcript: list[TranscriptEntry] = []`, ~agent.py:277) add the buffer:

```python
        self._pending_words: list[tuple[str, float, float, float]] = []  # raw STT words for the in-progress turn
```

Add the collector + `stt_node` override as methods on `_MouthAgent` (place them just above `on_user_turn_completed`, ~agent.py:388):

```python
    def _collect_words_from_event(self, event: object) -> None:
        """Tee point: buffer per-word timings from a FINAL_TRANSCRIPT SpeechEvent.

        Robust to the event/word shapes via getattr so the unit test can use
        lightweight stand-ins. Non-final events and word-less finals are no-ops.
        """
        etype = getattr(event, "type", None)
        if etype != lk_stt.SpeechEventType.FINAL_TRANSCRIPT and etype != "final_transcript":
            return
        alts = getattr(event, "alternatives", None) or []
        if not alts:
            return
        for w in getattr(alts[0], "words", None) or []:
            text = getattr(w, "text", None)
            start = getattr(w, "start_time", None)
            end = getattr(w, "end_time", None)
            if text is None or start is None or end is None:
                continue
            conf = getattr(w, "confidence", 1.0)
            self._pending_words.append((str(text), float(start), float(end), float(conf)))

    async def stt_node(self, audio, model_settings):
        """Default STT, with a tee that buffers word timings for the reel."""
        async for ev in Agent.default.stt_node(self, audio, model_settings):
            self._collect_words_from_event(ev)
            yield ev
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_word_capture.py -v -p no:cacheprovider`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/agent.py tests/interview_engine/test_word_capture.py
git commit -m "feat(reel): tee STT word timings via stt_node"
```

---

## Task 5: Attach buffered words to the candidate turn at commit

**Files:**
- Modify: `app/modules/interview_engine/agent.py`
- Test: `tests/interview_engine/test_word_capture.py`

The candidate `TranscriptEntry` is appended at agent.py:400-404. We enrich it from the buffer
(converting to relative `WordTiming` + computing bounds), then **clear the buffer** so the next turn
starts fresh. Factor the enrichment into a small method so it is unit-testable without driving the
full async turn.

- [ ] **Step 1: Write the failing test** (append to `tests/interview_engine/test_word_capture.py`)

```python
def test_build_candidate_entry_attaches_words_and_clears_buffer():
    a = _bare_agent()
    a._collect_words_from_event(_final_event([("six", 12.40, 12.72, 0.99),
                                              ("years", 12.80, 13.30, 0.97)]))
    entry = a._build_candidate_entry(text="six years", timestamp_ms=42000, question_id="q1")

    assert entry.role == "candidate"
    assert entry.question_id == "q1"
    assert entry.timestamp_ms == 42000
    assert entry.end_ms == 42000
    assert entry.start_ms == 42000 - 900           # back off spoken duration
    assert [w.text for w in entry.words] == ["six", "years"]
    assert entry.words[0].start_ms == 0            # relative to first word
    assert a._pending_words == []                  # buffer drained


def test_build_candidate_entry_without_words_is_backward_compatible():
    a = _bare_agent()
    entry = a._build_candidate_entry(text="ok", timestamp_ms=1000, question_id=None)
    assert entry.words is None
    assert entry.start_ms is None and entry.end_ms is None
    assert entry.timestamp_ms == 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_word_capture.py -v -p no:cacheprovider`
Expected: FAIL — `AttributeError: _MouthAgent has no attribute '_build_candidate_entry'`.

- [ ] **Step 3: Write minimal implementation**

Add the import near the top of `agent.py` (with the other interview_engine imports):

```python
from app.modules.interview_engine.transcript_timing import relative_words, turn_bounds
```

Add the builder method to `_MouthAgent` (just above `on_user_turn_completed`):

```python
    def _build_candidate_entry(
        self, *, text: str, timestamp_ms: int, question_id: str | None
    ) -> TranscriptEntry:
        """Build the candidate TranscriptEntry, enriching it with the buffered
        word timings (if any) and draining the buffer. No words → the legacy
        shape (words/start_ms/end_ms stay None)."""
        words = relative_words(self._pending_words)
        self._pending_words = []
        if not words:
            return TranscriptEntry(
                role="candidate", text=text,
                timestamp_ms=timestamp_ms, question_id=question_id,
            )
        start_ms, end_ms = turn_bounds(anchor_ms=timestamp_ms, words=words)
        return TranscriptEntry(
            role="candidate", text=text,
            timestamp_ms=timestamp_ms, question_id=question_id,
            start_ms=start_ms, end_ms=end_ms, words=words,
        )
```

Now replace the inline candidate append in `on_user_turn_completed` (agent.py:400-404). Change:

```python
            self._result_transcript.append(
                TranscriptEntry(
                    role="candidate", text=text, timestamp_ms=self._t_ms(),
                    question_id=self._brain.active_question_id,
                ))
```

to:

```python
            self._result_transcript.append(
                self._build_candidate_entry(
                    text=text, timestamp_ms=self._t_ms(),
                    question_id=self._brain.active_question_id,
                ))
```

> **Buffer-clearing invariant:** `_build_candidate_entry` is the only drain site, and it runs on
> the `text.strip()` branch (agent.py:398). If a committed turn is whitespace-only (no entry
> appended), the buffer would carry stale words into the next turn. Guard it: in the `else` of that
> branch (whitespace-only commit), also clear — add `else: self._pending_words = []` after the
> existing `if text.strip():` block so every commit drains the buffer exactly once.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_word_capture.py -v -p no:cacheprovider`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/agent.py tests/interview_engine/test_word_capture.py
git commit -m "feat(reel): attach word timings to candidate transcript turns"
```

---

## Task 6: Persistence round-trip + downstream-safety guard

**Files:**
- Test: `tests/interview_runtime/test_transcript_entry_words.py` (append)

`record_session_result` persists via `[t.model_dump(mode="json") for t in result.full_transcript]`
(`interview_runtime/service.py:406`) — so enriched entries serialize automatically. And
`recording._build_transcript` reads only `role`/`text`/`timestamp_ms` (recording.py:65-81), so the
new fields are ignored by the existing report player (no change needed there). Lock both facts with
a test so a future edit can't silently break them.

- [ ] **Step 1: Write the failing test** (append to `tests/interview_runtime/test_transcript_entry_words.py`)

```python
def test_enriched_transcript_survives_jsonb_round_trip():
    # Mimics record_session_result's model_dump(mode="json") -> JSONB -> reload.
    import json
    from app.modules.interview_runtime.models import TranscriptEntry, WordTiming

    entry = TranscriptEntry(
        role="candidate", text="six years", timestamp_ms=42000, question_id="q1",
        start_ms=41100, end_ms=42000,
        words=[WordTiming(text="six", start_ms=0, end_ms=320, confidence=0.99)],
    )
    blob = json.loads(json.dumps(entry.model_dump(mode="json")))  # JSONB hop
    assert TranscriptEntry.model_validate(blob) == entry


def test_recording_build_transcript_ignores_word_fields():
    from app.modules.session.recording import _build_transcript

    raw = [{
        "role": "candidate", "text": "six years", "timestamp_ms": 42000,
        "question_id": "q1", "start_ms": 41100, "end_ms": 42000,
        "words": [{"text": "six", "start_ms": 0, "end_ms": 320, "confidence": 0.99}],
    }]
    segs = _build_transcript(raw)
    assert len(segs) == 1
    assert segs[0].text == "six years"
    assert segs[0].t_ms == 42000
```

- [ ] **Step 2: Run test to verify it fails-then-passes**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_transcript_entry_words.py -v`
Expected: these two PASS immediately (no production change needed — they are regression locks). If
either fails, the model or `_build_transcript` regressed; fix before continuing.

- [ ] **Step 3: (no implementation needed)** — these guard existing behavior.

- [ ] **Step 4: Commit**

```bash
git add tests/interview_runtime/test_transcript_entry_words.py
git commit -m "test(reel): lock transcript word-field persistence + player safety"
```

---

## Task 7: Full-suite check + manual capture verification

**Files:** none (verification only)

- [ ] **Step 1: Run the touched test subtrees**

Run (livekit-free):
`docker compose run --rm nexus pytest tests/interview_runtime/test_transcript_entry_words.py tests/interview_engine/test_transcript_timing.py -v`
Expected: all PASS.

Run (livekit-touching, no cov to avoid the PyO3 segfault):
`docker compose run --rm nexus pytest tests/interview_engine/test_word_capture.py -v -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 2: Module-boundary + lint**

Run: `docker compose run --rm nexus pytest tests/test_module_boundaries.py -q && docker compose run --rm nexus ruff check app/modules/interview_engine/transcript_timing.py app/modules/interview_engine/agent.py app/modules/interview_runtime/models.py`
Expected: PASS / no lint errors. (`transcript_timing` imports `interview_runtime.models` — an allowed cross-module `models` import per the boundary test.)

- [ ] **Step 3: Manual live-capture verification (the real gate)**

This is the operator-run check (per the manual-agent-testing preference — no automated eval).

1. Rebuild + restart the engine: `docker compose up -d --force-recreate nexus-engine`.
2. Run a fresh interview session end-to-end (a strong-candidate run).
3. Inspect the persisted transcript:

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -t -A -c \
"SELECT jsonb_pretty(elem) FROM sessions, jsonb_array_elements(transcript) elem \
 WHERE id='<NEW_SESSION_ID>' AND elem->>'role'='candidate' \
 AND elem ? 'words' LIMIT 1;"
```

Expected: a candidate turn with a non-empty `words` array (each `{text,start_ms,end_ms,confidence}`,
first word `start_ms=0`) plus `start_ms`/`end_ms`. Confidence values look sane (0–1); word order
matches the text.

- [ ] **Step 4: Final commit (if any verification fixups were needed)**

```bash
git add -A && git commit -m "chore(reel): phase-1 capture verified on live session"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §5.1 (word timings + per-turn bounds) → Tasks 1–5. §5.2 (capture in engine, clock handling) → Tasks 2–5 + header deviation note. §5.3 (offset calibration) → **explicitly deferred to Phase 2** with rationale (needs the first real recording; anchors already persist). §5.4 (optional event-log word stream) → **dropped from Phase 1** (best-effort/optional in spec; the pipeline reads `sessions.transcript`, not the event log).
- **Type consistency:** `WordTiming(text,start_ms,end_ms,confidence)`, `relative_words(list[tuple]) -> list[WordTiming]`, `turn_bounds(anchor_ms, words) -> (int,int)`, `_collect_words_from_event(event)`, `_build_candidate_entry(text,timestamp_ms,question_id)` used consistently across Tasks 1–6.
- **No placeholders:** every code/test step shows full content and an exact run command with expected result.
