# Intro Prefetch + Cache Integrity + TTS Resilience

**Status:** Draft for review
**Date:** 2026-05-10
**Author:** Ishant + Claude (collaborative)
**Builds on:** `2026-05-10-opener-prefetch-architecture-design.md` (Phase 9.8 opener prefetch)
**Scope:** Three structurally related fixes shipped as one sequenced spec — (A) repeat-cache integrity contract, (B) per-session persona-intro pre-cache (mirror of opener prefetch), (C) TTS cache-build resilience with bounded retry. Plus a residual prompt brevity tightening for `deliver_first_question`.

---

## 1. Context and problem statement

Three bugs surfaced together in session `a998073a-3007-4cd5-9cb2-c1b8267777e8` (2026-05-10 14:48Z, gpt-5.4-nano + reasoning=none + opener prefetch). The session became unrecoverable after turn 7: the agent went silent, the candidate said "Hello? Am I audible?" three times, the agent kept replaying empty audio, and the candidate ended the session.

### Bug A — Cache corruption from interrupted/empty Speaker calls (CRITICAL)

When the Speaker LLM is interrupted by the candidate's voice on a question-bearing kind (`deliver_question`, `deliver_first_question`, `deliver_probe`, `push_back`, `clarify`), `_handle_interrupted_speaker` calls:

```python
self._state.register_agent_utterance(
    turn_id=turn_id, text="",
    instruction_kind=speaker_input.instruction_kind,
)
```

`register_agent_utterance` writes to `_question_utterances[turn_id] = ""` because the instruction kind is in `_QUESTION_KINDS`. The next time the Judge picks `repeat`, `_resolve_repeat` returns the LAST entry from `_question_utterances` — the empty string. The orchestrator then plays an empty TTS, so the candidate hears silence.

The session's audit envelope contains **three consecutive `speaker.cached` events with `final_utterance: ""`** — the exact mechanism by which the candidate became deaf to the agent.

The `_handle_empty_speaker_output` path has a similar but milder issue: it writes the *fallback* text to the cache. For push_back the fallback is `f"Let me restate that. {bank_text}"` which is non-empty but wrong content for the cache (the cache should hold the LAST GOOD agent question, not a recovery utterance).

The bug is structural: `register_agent_utterance` conflates two responsibilities — the audit transcript AND the repeat cache — into a single call. The transcript SHOULD record `""` (the agent emitted nothing). The repeat cache should NOT.

### Bug B — Persona intro lives in the repeat cache

`deliver_first_question.txt` instructs the Speaker LLM to emit greeting + question as one utterance:

> `"Hi, I'm Sam. Walk me through how you would design or refactor a Jira project to match a client workflow, and how you'd package it as a reusable template."`

`register_agent_utterance` caches this verbatim. When the candidate asks "repeat" on turn 2, `_resolve_repeat` replays the entire blob — intro included. Sounds robotic and confuses candidates ("am I supposed to introduce myself again?").

The Phase 9.8 opener prefetch architecture already solved the analogous problem for `push_back`, `clarify`, `redirect`, `polite_close`, etc. by playing pre-cached opener audio in parallel with a content-only Speaker LLM call. `deliver_first_question` was excluded from Phase 9.8 because the persona intro is dynamic (per-tenant `persona_name`) — pre-caching at engine boot would not have worked for a multi-tenant deployment. This spec extends the same pattern to `deliver_first_question` via a **per-session** intro cache built once at agent entrypoint.

### Bug C — TTS cache build is brittle to transient DNS / network failures

During engine boot, `build_opener_cache` fans out ~30 concurrent OpenAI TTS requests via `asyncio.gather`. In session `a998073a` boot, one variant ("Got it. And —") hit a `httpcore.ConnectError: [Errno -5] No address associated with hostname` — transient DNS resolution failure. `_synthesize_variant` swallowed the exception into the `BuildReport.failed_variants` list. The variant has no audio for the rest of the worker process lifetime; runtime falls back to text-only TTS for that opener (which the orchestrator handles cleanly), but the failure is permanent until next worker restart.

A single retry-with-backoff at the `_synthesize_variant` layer would have recovered this in <500ms.

### Bug D (residual) — Speaker still leaks rubric components

After the prompt tightening commit `f88e804`, the first question shrank from 75 words to 31, but still contains two enumerated criteria: "design or refactor" and "package as a reusable template." Below the 30-word cap by one word, but still seeding the rubric. nano-class models obey few-shot examples better than abstract rules — needs (a) a tighter cap, (b) one explicit negative example, and (c) shorter positive examples that demonstrate single-verb single-object output.

---

## 2. Goals and non-goals

### Goals

- **Repeat path is correct under all empty/interrupted/error conditions.** The repeat cache only ever holds the most recent successful agent question utterance.
- **First-question repeat replays only the question.** No persona intro on replay.
- **TTS cache build is resilient to transient network failures.** Bounded retry-with-backoff per variant; permanent failures still degrade gracefully.
- **First-question Speaker output is uniformly tight.** Hard ≤ 20-word cap, single open-ended ask, no rubric enumeration.
- **No regression of Phase 9.8 invariants.** Opener prefetch keeps working unchanged for the 6 kinds that already use it.
- **Composition tests prove the new contracts.** No production behavior change ships without a test that fails before the change and passes after.

### Non-goals

- Bank text quality (the question_bank generator emits rubric-shaped text). Out of scope; separate workstream.
- Speaker latency for non-first-question kinds plateauing at 2–2.5s. Separate investigation.
- Multi-tenant pre-cache sharing of intros (e.g., LRU across persona names). YAGNI for v1.
- Re-prompting the Speaker LLM when output exceeds the cap (post-hoc validation). Spec evaluates and defers — prompt-only fix first; revisit if data shows the prompt-only fix doesn't hold.

---

## 3. Architecture overview

Three orthogonal changes, sequenced for review safety:

```
Phase 1 (Bug A)   → split register_agent_utterance into two intents:
                    transcript-record (always) and cache-update
                    (only on non-empty success). Audit handlers stop
                    poisoning the repeat cache.

Phase 2 (Bug C)   → bounded retry-with-backoff in _synthesize_variant.
                    Same surface, more resilient. Lowest blast radius;
                    ships independently of Phase 1.

Phase 3 (Bug B)   → per-session persona intro cache. Agent entrypoint
                    synthesizes one OpenerVariant per session after
                    state_engine.set_persona_name(...). Orchestrator
                    holds it as self._intro_variant. _stream_speaker_and_say
                    routes deliver_first_question through the existing
                    opener-prefetch path using that variant. Cache stores
                    only the question.

Phase 4 (Bug D)   → tighten deliver_first_question.txt: cap drops to
                    20 words, examples shortened, one anti-pattern
                    example added.
```

Phases 1 and 2 are independently deployable. Phase 3 depends on Phase 1 (the new cache contract) and on the per-session synth helper from Phase 2 being factored cleanly. Phase 4 is a pure prompt change — can ship anywhere in the sequence.

---

## 4. Components

### 4.1 Cache integrity contract (Phase 1)

**Current shape** (`state/engine.py:805–816`):

```python
def register_agent_utterance(
    self, *, turn_id: str, text: str, instruction_kind: InstructionKind,
) -> None:
    self._transcript.append(TranscriptEntry(role="agent", text=text, ...))
    if instruction_kind in self._QUESTION_KINDS:
        self._question_utterances[turn_id] = text   # ← writes "" on interrupt
```

**Target shape:**

```python
def register_agent_utterance(
    self, *, turn_id: str, text: str, instruction_kind: InstructionKind,
) -> None:
    """Record an agent utterance to the transcript. ALWAYS appends to
    transcript regardless of text length — empty text is a valid
    historical fact (the agent emitted nothing on this turn).

    Does NOT update the repeat-cache. Use ``register_agent_question_for_repeat``
    for that — the two intents are separate by design (Phase 9.9, this spec).
    """
    self._transcript.append(TranscriptEntry(role="agent", text=text, ...))


def register_agent_question_for_repeat(
    self, *, turn_id: str, text: str, instruction_kind: InstructionKind,
) -> None:
    """Update the repeat-cache. Only call this when the agent
    SUCCESSFULLY emitted a question-bearing utterance. Empty text or
    non-question kinds are no-ops.

    The repeat cache holds the most recent good question text for the
    State Engine's NextAction.repeat path. Empty entries would cause
    silent-agent replays — strictly forbidden by the new contract.
    """
    if not text.strip():
        return
    if instruction_kind not in self._QUESTION_KINDS:
        return
    self._question_utterances[turn_id] = text
```

**Call site changes:**

| Caller | Old call | New call |
|---|---|---|
| `_stream_speaker_and_say` (success path) | `register_agent_utterance(text=final_text, ...)` | `register_agent_utterance(text=final_text, ...)` + `register_agent_question_for_repeat(text=final_text, ...)` |
| `_handle_interrupted_speaker` | `register_agent_utterance(text="", ...)` | `register_agent_utterance(text="", ...)` only — NO cache write |
| `_handle_empty_speaker_output` | `register_agent_utterance(text=fallback, ...)` | `register_agent_utterance(text=fallback, ...)` only — NO cache write (the fallback is not the question) |
| Recovery exception path | `register_agent_utterance(text=RECOVERY_TEXT, ...)` | `register_agent_utterance(text=RECOVERY_TEXT, ...)` only — NO cache write |

**Why split rather than add an `empty=False` flag:** The two intents (transcript-record vs cache-update) are conceptually different and have different invariants. Splitting the methods makes the call-site decision explicit at the type level — a future caller can't forget the flag and accidentally re-introduce the bug. It also makes the audit trail clearer: greppable for "which call sites think they're updating the repeat cache?"

### 4.2 TTS cache-build retry (Phase 2)

**Current shape** (`openers/cache.py:35–55`):

```python
async def _synthesize_variant(variant, tts) -> tuple[OpenerVariant, Exception | None]:
    try:
        frames = []
        async with tts.synthesize(variant.text) as stream:
            async for ev in stream:
                ...
        return variant, None
    except Exception as exc:
        return variant, exc
```

**Target shape:**

```python
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 0.2  # 200ms, 400ms, 800ms backoff

async def _synthesize_variant(variant, tts) -> tuple[OpenerVariant, Exception | None]:
    """Synthesize one variant with bounded exponential-backoff retry on
    transient errors. Returns (variant, None) on success or (variant,
    last_error) after all retries exhausted.

    Retried errors: APIConnectionError, asyncio.TimeoutError, OSError
    (DNS failures bubble up as OSError via httpcore). Non-retried:
    everything else (HTTP 4xx, 401, 429 should not be hidden).
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            frames = []
            async with tts.synthesize(variant.text) as stream:
                async for ev in stream:
                    frame = getattr(ev, "frame", None)
                    if frame is not None:
                        frames.append(frame)
            if not frames:
                return variant, RuntimeError("empty audio stream")
            variant.audio_frames = frames
            return variant, None
        except (asyncio.TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_RETRY_BASE_DELAY_S * (2 ** attempt))
                continue
            return variant, exc
        except Exception as exc:  # noqa: BLE001
            return variant, exc  # non-transient, no retry
    return variant, last_exc
```

The retry classification follows established practice for `httpx`-bound clients:

- `OSError` covers `httpcore.ConnectError` (which inherits from `OSError`), DNS failures (`gaierror` is `OSError` subclass), TCP resets.
- `asyncio.TimeoutError` covers the rare slow-but-eventually-succeeds case.
- `openai.APIConnectionError` wraps these with extra context but `OSError` catches the underlying cause first.
- Non-retried: 4xx, auth failures, content filter rejections, schema errors.

Retry budget is bounded (3 attempts, max 600ms total backoff) to keep cache build under a human-perceivable bound (~2s P95 at 30 variants in parallel).

### 4.3 Per-session persona intro cache (Phase 3)

The Phase 9.8 spec deliberately scoped opener prefetch to static phrases (no parameterization). Persona intros are dynamic: `f"Hi, I'm {persona_name}. To start —"`. We extend the existing `OpenerVariant` mechanism with a per-session variant carried on the orchestrator.

**Helper factored out of `build_opener_cache`** (`openers/cache.py`):

```python
async def synth_one(*, text: str, tts: TTS) -> list[Any] | None:
    """Synthesize a single text into audio frames with the same retry
    policy as the cache builder. Used by build_opener_cache for each
    variant AND by the engine entrypoint for the per-session intro.
    Returns None on permanent failure (caller falls back to text-only).
    """
    variant = OpenerVariant(text=text)
    _, exc = await _synthesize_variant(variant, tts)
    if exc is not None:
        return None
    return variant.audio_frames
```

**Engine entrypoint change** (`agent.py`, after `state_engine.set_persona_name(...)`):

```python
intro_text = _compose_intro_text(persona_name=state_engine.persona_name)
intro_audio = await synth_one(text=intro_text, tts=tts_plugin)
intro_variant = OpenerVariant(text=intro_text, audio_frames=intro_audio)
log.info(
    "engine.intro.built",
    persona_name=state_engine.persona_name,
    cache_hit=intro_audio is not None,
    text_len=len(intro_text),
)


def _compose_intro_text(*, persona_name: str) -> str:
    """The persona intro spoken before the FIRST question of every
    session. Kept short — the question is the substance, the intro
    just sets pacing.

    Lives at the agent.py module level so prompt-quality tests can
    assert its shape without importing engine internals.
    """
    return f"Hi, I'm {persona_name}. To start —"
```

**Orchestrator constructor change** (`orchestrator.py`):

```python
def __init__(
    self, *,
    ...
    opener_library: OpenerLibrary,
    intro_variant: OpenerVariant | None = None,
    ...
) -> None:
    ...
    self._intro_variant = intro_variant
```

`intro_variant` is optional with a `None` default to preserve the existing test surface (the 36 orchestrator tests + composition tests construct orchestrators without an intro). When `None`, the orchestrator falls back to today's behavior (Speaker LLM produces greeting + question as one).

**Pick-time routing in `_stream_speaker_and_say`:**

```python
if (
    speaker_input.instruction_kind == InstructionKind.deliver_first_question
    and self._intro_variant is not None
):
    # Per-session intro: synthesized at agent entrypoint, replaces the
    # opener library lookup for this kind only.
    opener = OpenerSelection(
        text=self._intro_variant.text,
        audio_iter=(
            (lambda: iter(self._intro_variant.audio_frames))
            if self._intro_variant.audio_frames is not None
            else None
        ),
    )
else:
    opener = self._opener_library.pick(
        kind=speaker_input.instruction_kind,
        sub_context=sub_ctx,
        recent_openers=self._recent_openers,
    )
```

Everything downstream (`speaker_task = create_task(...)`, `agent.session.say(text=opener.text, audio=...)`, `wait_for_playout`, `await speaker_task`) reuses the existing parallel-dispatch path verbatim. The Phase 9.8 invariant that the cache holds only Speaker LLM content is preserved by construction (the Speaker LLM never sees or emits the intro text).

**SpeakerInput propagation:** `pre_spoken_opener` already exists from Phase 9.8 (`models/speaker.py`). The intro flows through that field exactly like an opener — Speaker prompts already know how to handle non-null `pre_spoken_opener` (skip own opener, generate continuation).

**Multi-tenant correctness:** `state_engine.persona_name` is resolved per-session from `tenant_settings.engine_agent_name` falling back to `settings.engine_agent_name`. Different tenants get different intros. Per-session synth costs ~200–400ms at boot, paid once before the candidate hears the agent — net-zero against the 1.2s `deliver_first_question` TTFT we already saved with nano.

**Audit:** the existing `SPEAKER_OPENER_PLAYED` event fires for the intro turn too. Distinguishing from a regular opener: payload's `instruction_kind` is `"deliver_first_question"`, `sub_context` is `"default"`, and a new boolean field `is_session_intro: bool` is added to `SpeakerOpenerPlayedPayload`. Default `False` for backward compat.

### 4.4 Brevity prompt hardening (Phase 4)

`deliver_first_question.txt` rewrite:

- Cap drops from 30 words to **20 words total** (greeting + question).
- Examples shortened — current ones are 19–22 words; aim for 15–18.
- One ANTI-PATTERN example explicitly showing the wrong output style and labeling it as wrong.
- Instructions explicitly disallow listing two or more topical components from `bank_text` ("design OR refactor" counts as listing two verbs).
- The HOW-TO-COMPRESS recipe stays but adds: "If bank_text uses 'or' to list verbs (design or refactor), pick ONE. Same for objects."

Also tighten `_preamble.txt` ANTI-ENUMERATION bullet to mention conjunctions: "Do not preserve `X or Y` lists from bank_text — pick one."

Note: the prompt fix needs the per-session intro architecture (Phase 3) to be live, because once the intro is pre-spoken, the prompt's example outputs no longer include the greeting. The tightening rewrite is the natural place to also update the prompt to assume `pre_spoken_opener` carries the greeting (mirroring all the other Phase 9.8-aware prompts).

---

## 5. Data flow per turn (revised)

### 5.1 First-question turn (Phase 3 active)

```
1. on_enter fires.
2. Judge: synthetic NextAction.deliver_first_question.
3. _stream_speaker_and_say:
   a. sub_ctx = SubContext.DEFAULT
   b. Detected: kind == deliver_first_question AND self._intro_variant set.
      → opener = OpenerSelection(text="Hi, I'm Sam. To start —", audio_iter=...)
   c. speaker_input.pre_spoken_opener = "Hi, I'm Sam. To start —"
   d. speaker_task = create_task(self._speaker.stream(speaker_input))   # parallel
   e. agent.session.say(text="Hi, I'm Sam. To start —", audio=cached_frames)
   f. await wait_for_playout()  (~1.2s — intro length)
   g. SPEAKER_OPENER_PLAYED audit fires (is_session_intro=True)
   h. handle = await speaker_task
   i. agent.session.say(stream)  # the question content
   j. final_text = "Walk me through a JIRA workflow you configured." (15 words)
   k. register_agent_utterance(text=final_text, ...) → transcript only
   l. register_agent_question_for_repeat(text=final_text, ...) → cache: {turn_id: "Walk me through..."}
4. SPEAKER_CALL audit fires.
```

**Cache state after turn 1:** `{first_q_turn_id: "Walk me through a JIRA workflow you configured."}`

### 5.2 Repeat after first question

```
1. Candidate: "Can you repeat that?"
2. Judge: NextAction.repeat
3. _resolve_repeat → returns "Walk me through a JIRA workflow you configured."
4. orchestrator plays cached text → SPEAKER_CACHED audit
```

The intro is NOT replayed. Bug B fixed.

### 5.3 Repeat after interrupted push_back (Bug A fixed)

```
Turn N:   push_back/missing_specifics
   - Opener "Right —" plays.
   - Speaker LLM streams "Which validators..." — candidate interrupts mid-stream.
   - _handle_interrupted_speaker:
       * SPEAKER_INTERRUPTED audit fires.
       * register_agent_utterance(text="", instruction_kind=push_back)
         → transcript: empty agent entry recorded.
         → cache UNTOUCHED (Phase 1 contract — empty texts skip).
   - cache state still: {prior_question_turn_id: "Original question text"}

Turn N+1: candidate: "Can you repeat?"
   - Judge: NextAction.repeat
   - _resolve_repeat → returns "Original question text"  ← previous valid entry
   - SPEAKER_CACHED replays the actual prior question. NOT empty.
```

### 5.4 TTS cache build with transient DNS failure (Bug C fixed)

```
1. Engine boots, build_opener_cache fires 30 parallel _synthesize_variant calls.
2. Variant "Got it. And —" hits OSError (DNS).
3. Backoff 200ms, retry. DNS resolves. Audio frames returned.
4. BuildReport.success_count = 30, failed_variants = []
5. Cache fully populated. No degraded-mode runtime fallbacks.
```

If retries also fail (e.g., real network outage):

```
1. After 3 attempts (~600ms), variant returns OSError.
2. BuildReport.failed_variants += [("Got it. And —", "...")].
3. Engine logs WARNING with failed_count.
4. Runtime: opener.audio_iter is None for that variant → say(text=...) without audio kwarg.
   Falls back to live TTS for that one variant (~1.4s extra latency on those turns).
5. Sessions are functional, just slower for those specific openers.
```

---

## 6. Error handling

### 6.1 Cache integrity invariants

- `_question_utterances` only ever contains non-empty strings.
- A turn that interrupted before the LLM produced any text → no entry.
- A turn that hit a recovery exception path → no entry.
- A turn that produced an empty-output fallback → no entry (the fallback is restated bank_text, not THE agent's question text).
- `_resolve_repeat` no longer needs defensive walk-back logic.

### 6.2 Intro synthesis failures (Phase 3)

If `synth_one` for the intro returns `None` at agent entrypoint:

- Log WARNING with `persona_name`, the failure reason, and a `degraded_mode=text_only_intro` flag.
- `intro_variant.audio_frames` stays `None`.
- Orchestrator's `OpenerSelection` is built with `audio_iter=None` → `agent.session.say(text=intro_text)` runs live TTS for the intro on the first turn (~1.4s extra, one-time per session).
- Session is fully functional — degraded latency for the intro turn only.

If we want to be stricter (e.g., refuse to start sessions without an intro audio), that's a future enhancement. v1: graceful degradation matches the opener-cache contract.

### 6.3 Speaker exceeds prompt cap (Phase 4)

The prompt-only fix has no runtime guard. If the cap is exceeded, the audit envelope's `speaker.call.final_utterance` will show it; that's the empirical signal for whether we need the post-hoc validation fallback. No errors raised.

---

## 7. Observability

### 7.1 New audit field

`SpeakerOpenerPlayedPayload` gains `is_session_intro: bool = False`. When the orchestrator routes through the per-session intro path (Phase 3), the field is `True`; for all other openers (the 6 existing kinds), it stays `False`. Downstream forensic queries can filter on `is_session_intro=True` to find the specific turn the persona introduced themselves.

### 7.2 New log lines

- `engine.intro.built` — emitted per session at agent entrypoint after intro synth. Fields: `persona_name`, `cache_hit` (whether audio synth succeeded), `text_len`.
- `openers.cache.synth.retry` — emitted from `_synthesize_variant` when a retryable error fires. Fields: `variant_text` (truncated to 40 chars), `attempt` (1, 2, 3), `error_type`, `backoff_ms`.

### 7.3 No new audit kinds

Phase 1's contract change is invisible to existing audit kinds — the existing `SPEAKER_INTERRUPTED` and `SPEAKER_OUTPUT_EMPTY` events already capture the empty-text condition. The cache split just means those events no longer carry a side-effect on `_question_utterances`.

---

## 8. Testing strategy

### 8.1 Unit tests

**Phase 1 (cache integrity):**
- `test_register_agent_utterance_appends_to_transcript_for_empty_text` — empty text becomes a transcript entry, no cache write.
- `test_register_agent_question_for_repeat_skips_empty_text` — empty text is no-op, cache unchanged.
- `test_register_agent_question_for_repeat_skips_non_question_kinds` — redirect/repeat/etc. don't update cache.
- `test_register_agent_question_for_repeat_writes_for_question_kinds_with_non_empty_text` — happy path.

**Phase 2 (TTS retry):**
- `test_synthesize_variant_retries_on_oserror` — first attempt raises OSError, second succeeds → returns success.
- `test_synthesize_variant_retries_on_timeout` — same for asyncio.TimeoutError.
- `test_synthesize_variant_does_not_retry_on_4xx` — fake an `openai.BadRequestError` → no retry, returns immediately.
- `test_synthesize_variant_exhausts_retries` — all 3 attempts raise → returns last error.
- `test_synthesize_variant_backoff_timing` — measured backoff intervals match `0.2 * 2**attempt`.

**Phase 3 (intro variant):**
- `test_compose_intro_text_uses_persona_name` — `_compose_intro_text(persona_name="Sam")` returns `"Hi, I'm Sam. To start —"`.
- `test_synth_one_returns_audio_frames_on_success` — happy path with mock TTS.
- `test_synth_one_returns_none_on_failure` — TTS raises permanently → returns None.
- `test_orchestrator_constructor_accepts_intro_variant` — backward-compat: defaulting to None still works.
- `test_orchestrator_uses_intro_variant_for_deliver_first_question` — orchestrator routes through intro_variant when set.
- `test_orchestrator_falls_back_to_library_pick_for_other_kinds` — push_back etc. still go through `_opener_library.pick`.
- `test_speaker_opener_played_audit_is_session_intro_field` — payload's is_session_intro=True for intro turn.

**Phase 4 (prompt):**
- Existing prompt-loadable tests still pass (`test_speaker_prompt_loadable.py`).
- New: `test_deliver_first_question_prompt_documents_anti_pattern_example` — asserts the new anti-pattern example exists in the prompt.

### 8.2 Composition tests (in `tests/interview_engine/test_orchestrator_composition.py`)

- `test_repeat_after_interrupted_push_back_replays_prior_question_not_empty` — drives a 4-turn session: deliver_first_question (success) → push_back (interrupted) → repeat. Asserts `SPEAKER_CACHED.final_utterance` equals the original question text, NOT `""`.
- `test_repeat_after_empty_speaker_output_replays_prior_question_not_fallback` — same but with `_handle_empty_speaker_output` instead of interrupted. Asserts cache wasn't polluted by the fallback text.
- `test_first_question_repeat_replays_only_question_no_intro` — drives deliver_first_question then repeat. Asserts cache holds only the question (no "Hi, I'm Sam"); `SPEAKER_CACHED.final_utterance` is the question alone.

### 8.3 End-to-end manual session

After all four phases land:
1. Engine restart.
2. Inspect the boot logs for `engine.opener_cache.built` and confirm `failed_count=0` (or single-digit if DNS is flaky in the test environment).
3. Run a fresh interview session that exercises:
   - First question → repeat → expect ONLY the question, no "Hi, I'm Sam".
   - Interrupted push_back → repeat → expect the prior valid question text.
   - Expected first-question word count: ≤ 20.
4. Open the audit envelope and verify the new audit field `is_session_intro=True` on the first-question opener event.

---

## 9. Migration / rollout

The four phases ship as separate commits but in one PR (or main-branch sequence):

1. **Commit 1 (Phase 1):** Cache integrity contract — `register_agent_utterance` + `register_agent_question_for_repeat` split, all 4 callers updated, unit + composition tests. No user-visible behavior change in the happy path; the silent-agent disaster is now impossible. **Deployable independently.**

2. **Commit 2 (Phase 2):** `_synthesize_variant` retry-with-backoff. No API change. Unit tests. **Deployable independently.**

3. **Commit 3 (Phase 3a):** `synth_one` factored out + `_compose_intro_text` helper added at agent.py module level. `intro_variant` parameter added to InterviewOrchestrator constructor (default None). Unit + integration tests. **Behavior unchanged** when `intro_variant=None`.

4. **Commit 4 (Phase 3b):** Engine entrypoint synthesizes intro at session start and passes to orchestrator. `is_session_intro` audit field. Composition test for first-question-repeat-replays-only-question. **First user-visible behavior change** — first question repeats no longer include the intro.

5. **Commit 5 (Phase 4):** `deliver_first_question.txt` + `_preamble.txt` updates. Prompt-loadable test for the new anti-pattern example. **Prompt change only.**

Rollback: any commit can be reverted independently. Phase 3 has the largest blast radius (touches the orchestrator's hot path), so it ships AFTER Phase 1 (which already eliminates the worst symptom). If Phase 3 has issues, reverting it leaves Phase 1's correctness fix in place.

---

## 10. Open questions / risks

### Q1: Should `register_agent_utterance` skip the transcript append on empty text too?

**Decision:** No. The transcript is an audit log — recording "the agent emitted nothing on this turn" is information, not noise. Downstream replay tools and human reviewers benefit from seeing the empty entry next to the `SPEAKER_INTERRUPTED` event in the envelope.

### Q2: Is `_resolve_repeat` returning the LAST entry the right semantic, or should it be the LAST entry whose `instruction_kind` was `deliver_question` / `deliver_first_question` (the "primary" question, not push_back / clarify rephrasings)?

**Decision:** Out of scope for this spec. The current behavior (return the latest of any question kind) is intentional — if the agent just rephrased via clarify, the candidate's "repeat" should replay the rephrasing, not the original. Revisit only if we get user reports.

### Q3: What happens if `synth_one` for the intro fails on a first session, succeeds on the next session? Should we retry across sessions?

**Decision:** No global cache. The synth is per-session by design. A failed synth on session N is recovered next session N+1 by a fresh attempt (with retry-with-backoff). Acceptable given the alternative is global state with cache invalidation problems.

### Q4: Bug D residual — what if even tighter prompts don't kill rubric leakage?

**Decision:** Defer the post-hoc validation idea (re-prompt on long output) to a follow-up if data shows the prompt-only fix doesn't hold. Track via Bug D metric: `speaker.call.final_utterance` word count for `deliver_first_question`, sampled across sessions.

### Risk: Per-session intro synth latency adds to session startup time

~200–400ms one-time per session. Compared to the existing engine startup latency (LiveKit room join + JWT + STT/TTS provisioning), this is in the noise. The user already pays multi-second startup cost; adding sub-half-second is invisible.

### Risk: `intro_variant.audio_frames` reference held for entire session lifetime

Each set of audio frames is ~10–50KB. Held for the duration of the session (single-digit minutes). Negligible memory pressure; auto-collected on session close.

### Risk: Non-retryable HTTP errors masquerading as `OSError` in some edge case

The retry classification is: `OSError` + `asyncio.TimeoutError` retried; everything else not. If `openai.BadRequestError` (subclass of `Exception` but not `OSError`) ever gets re-raised through a chain that erases its type, retries could compound a non-recoverable error. Mitigation: the test `test_synthesize_variant_does_not_retry_on_4xx` pins this contract. In practice the OpenAI SDK preserves error types cleanly.

---

## 11. Self-review

**Spec coverage:** Every bug and goal in §1–§2 maps to a phase in §3. The four phases collectively cover all four bugs.

**Type consistency:** `OpenerVariant`, `OpenerSelection`, `OpenerLibrary`, `SubContext` references all match current code shapes (verified). `SpeakerInput.pre_spoken_opener` already exists. `register_agent_utterance` signature change is backward-incompatible by intent — every caller is enumerated in §4.1 table.

**Minimal change principle:** Phase 1 splits a function into two — the simplest possible API change. Phase 2 adds retry inside an existing function. Phase 3 adds one parameter to a constructor and one branch in `_stream_speaker_and_say`. Phase 4 is a prompt edit. No new modules, no new audit kinds, no schema changes.

**Test-first discipline:** Every behavior change has a failing-test → passing-test entry in §8. Composition tests prove the user-visible properties (silent-agent fixed, intro-repeat fixed).

**Open items resolved before implementation:** Q1–Q4 in §10 are decided, not deferred.
