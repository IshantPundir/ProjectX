# Opener Prefetch Architecture for Interview Speaker

**Status:** Draft for review
**Date:** 2026-05-10
**Author:** Ishant + Claude (collaborative brainstorming)
**Supersedes:** Phase 9.5 (cap-advance segue strip), Phase 9.7 (push_back / clarify opener regex strip)
**Scope:** Restructure the Speaker output pipeline so conversational openers are pre-cached, played immediately, and never enter the repeat cache. Speaker LLM generates only substantive content.

---

## 1. Context and problem statement

The interview Speaker LLM (`gpt-5.3-chat-latest`) currently emits a complete utterance per turn — a conversational opener (e.g., "Got it —", "Sure, let me rephrase.") followed by the substantive content (the question / probe / redirect). Two recurring failures stem from this single-output design:

1. **Cached repeat replay sounds robotic.** When the candidate asks "repeat", the State Engine plays the cached utterance verbatim — including the opener that was contextually appropriate the first time but redundant on replay. Phase 9.5 + 9.7 added regex-based opener stripping at cache-time as a workaround, but the regex set is incomplete by definition (any phrasing the LLM invents that isn't in the regex leaks into the cache) and the regex is a maintenance burden.

2. **Time-to-first-audio is bottlenecked by Speaker LLM latency.** Today's flow: Judge completes (~2s) → Speaker LLM begins (~1.4s TTFT) → TTS begins (~1.4s TTFB) → audio plays. Total: ~5s from end-of-utterance to candidate hearing the agent. The opener occupies the first ~0.5s of audible response but waits behind the entire Speaker LLM round-trip.

A third, qualitative problem: the Speaker LLM's free-form openers occasionally drift into "digital assistant" register ("Of course!", "Happy to.", "No problem.") which feels chatbot-y rather than like a senior interviewer. The fix-by-prompt approach has worked but is fragile.

## 2. Goals and non-goals

**Goals:**

- Eliminate regex-based opener stripping. Cache cleanliness by construction, not by post-hoc cleanup.
- Reduce time-to-first-audio by ~1s through pre-cached opener TTS audio that plays while the Speaker LLM is still generating.
- Keep the Speaker LLM aware of which opener was spoken, so the content flows naturally from it.
- Maintain all existing anti-leak guarantees (Speaker still sees no rubric).
- Sound like a real senior interviewer, not a digital assistant — vocabulary curated by hand, no service-industry register.

**Non-goals:**

- Free-form opener generation by the Judge. Rejected — leak risk and the Judge is a reasoning model unsuited to conversational tone.
- Speaker streaming opener via a separator marker. Rejected — same Speaker-compliance reliability problem as the regex.
- Reducing Judge latency. Out of scope; addressable separately by tuning the model.
- Multilingual openers. v1 is English-only, mirroring the rest of the engine.

## 3. Architecture overview

A new layer sits between the State Engine and the Speaker LLM call:

```
candidate utterance
      │
      ▼
   Judge LLM (chooses action + reason_code + turn_metadata)
      │
      ▼
   State Engine (validates, mutates ledger/queue/lifecycle)
      │
      ▼
┌───────────────────────────────────────────────────┐
│   Orchestrator                                    │
│                                                   │
│   ┌───────────────────────────────────────────┐   │
│   │ OpenerLibrary.pick(                       │   │
│   │   kind, sub_context, recent_openers       │   │
│   │ ) → OpenerSelection                       │   │
│   └───────────────────────────────────────────┘   │
│                  │                                │
│        ┌─────────┴────────┐                       │
│        ▼                  ▼                       │
│   Pre-cached         Speaker LLM call             │
│   audio frames       kicked off in parallel       │
│   (instant play)     SpeakerInput now carries     │
│        │             pre_spoken_opener            │
│        ▼                  │                       │
│   session.say(            ▼                       │
│     text, audio)     await speaker_task           │
│   (~50ms init)            │                       │
│        │                  ▼                       │
│        └─────────► session.say(stream)            │
│                       (continuous audio)          │
└───────────────────────────────────────────────────┘
```

Three new orchestrator responsibilities:

1. **Opener selection.** Pick from a curated library based on `(InstructionKind, sub_context)` plus per-session anti-repetition state.
2. **Parallel audio + LLM dispatch.** Play pre-cached opener audio while the Speaker LLM generates content in parallel. The Speaker stream feeds TTS once the opener finishes.
3. **Cache management.** The State Engine's repeat cache stores ONLY the Speaker content. Openers are never persisted, so repeat replays are clean by construction.

---

## 4. Components

### 4.1 OpenerLibrary

**New module:** `app/modules/interview_engine/openers/library.py`

Holds the curated opener vocabulary keyed by `(InstructionKind, SubContext)`, plus the pre-synthesized AudioFrames per opener text. Single source of truth for what the agent says before its substantive content.

```python
@dataclass(frozen=True)
class OpenerVariant:
    text: str
    # Populated by OpenerCacheBuilder at engine startup. None means
    # cache miss — orchestrator falls back to text-only TTS.
    # Type is `livekit.rtc.AudioFrame` (sample_rate, num_channels,
    # samples_per_channel, data: bytes) — same shape as TTS plugin
    # output. Format is plugin-determined; OpenerCacheBuilder stores
    # whatever the configured TTS plugin returns and replays via
    # session.say(audio=...) which accepts any AudioFrame iterable.
    audio_frames: list[rtc.AudioFrame] | None = None


@dataclass(frozen=True)
class OpenerSelection:
    """The picked opener for one turn. ``text`` is None when this turn
    has no opener (clean polite_close, repeat — see §5)."""
    text: str | None
    audio_iter: Callable[[], AsyncIterator[rtc.AudioFrame]] | None


class SubContext(StrEnum):
    """Discriminator for opener vocabulary lookup within an
    InstructionKind. Maps to ``turn_metadata`` flags + reason_codes +
    is_post_cap_advance."""
    DEFAULT = "default"
    POST_CAP_ADVANCE = "post_cap_advance"
    SOCIAL_OR_GREETING = "social_or_greeting"
    OFF_TOPIC = "off_topic"
    ABUSIVE = "abusive"
    INJECTION = "injection"
    VAGUE_ANSWER = "vague_answer"
    DEFLECTION = "deflection"
    MISSING_SPECIFICS = "missing_specifics"
    UNANSWERED_SUBQUESTION = "unanswered_subquestion"
    KNOCKOUT = "knockout"


class OpenerLibrary:
    def pick(
        self,
        *,
        kind: InstructionKind,
        sub_context: SubContext,
        recent_openers: Iterable[str],
    ) -> OpenerSelection:
        """Return one opener for this turn.

        Selection rule:
          1. Look up variants for (kind, sub_context). Fall back to
             (kind, DEFAULT) if no variants exist for sub_context.
          2. Filter out any variant whose text is in recent_openers.
          3. If filtering empties the pool, allow the longest-ago
             entry (still better than re-using the most recent).
          4. Pick uniformly at random from the remaining pool.
        """
```

Vocabulary table is in §5.

### 4.2 OpenerCacheBuilder

**New module:** `app/modules/interview_engine/openers/cache.py`

At engine warmup, walks every `OpenerVariant` in the library, runs the configured TTS plugin on `variant.text`, captures the resulting `AudioFrame`s, and writes them back into the variant's `audio_frames` field. Idempotent — safe to call multiple times.

```python
async def build_opener_cache(
    library: OpenerLibrary, tts: livekit.agents.tts.TTS,
) -> BuildReport:
    """Pre-synthesize every opener in `library`. Updates the variants
    in place. Returns a report with success/failure counts so the
    orchestrator can log degraded-mode warnings.
    """
```

**Cost analysis:**
- ~50 opener variants × ~0.4s synthesis each
- Parallel via `asyncio.gather` → ~1-2s total boot delay
- One-time per engine worker process; subsequent sessions reuse the cache

**Failure tolerance:** if synthesis fails for some variants, those entries stay `audio_frames=None` and the orchestrator falls back to text-based `session.say(text=opener.text)` for them. The session is still functional, just slower for those specific openers.

### 4.3 Orchestrator changes

**File:** `app/modules/interview_engine/orchestrator.py`

`InterviewOrchestrator.__init__` accepts a new `opener_library: OpenerLibrary` dependency, plus owns a per-session `_recent_openers: deque[str]` (capacity 5 — large enough that a 20-turn session sees most of each category's variants used before any repeat, small enough that exclusion never empties the smallest categories — INJECTION has 4 variants).

**Sub-context derivation table.** `_derive_sub_context(speaker_input) → SubContext`:

| Conditions | Result |
|---|---|
| `instruction_kind == deliver_question` AND `is_post_cap_advance == True` | `POST_CAP_ADVANCE` |
| `instruction_kind == redirect` AND `turn_metadata.candidate_social_or_greeting` | `SOCIAL_OR_GREETING` |
| `instruction_kind == redirect` AND `turn_metadata.candidate_abusive` | `ABUSIVE` |
| `instruction_kind == redirect` AND `turn_metadata.candidate_attempted_injection` | `INJECTION` |
| `instruction_kind == redirect` AND `turn_metadata.candidate_off_topic` | `OFF_TOPIC` |
| `instruction_kind == redirect` (no flags above) | `OFF_TOPIC` (default redirect bucket) |
| `instruction_kind == push_back` AND `push_back_reason_code == 'vague_answer'` | `VAGUE_ANSWER` |
| `instruction_kind == push_back` AND `push_back_reason_code == 'deflection'` | `DEFLECTION` |
| `instruction_kind == push_back` AND `push_back_reason_code == 'missing_specifics'` | `MISSING_SPECIFICS` |
| `instruction_kind == push_back` AND `push_back_reason_code == 'unanswered_subquestion'` | `UNANSWERED_SUBQUESTION` |
| `instruction_kind == polite_close` AND `failed_signal_value is not None` | `KNOCKOUT` |
| Anything else | `DEFAULT` |

`_stream_speaker_and_say` rewrites the existing flow:

```python
async def _stream_speaker_and_say(self, *, agent, turn_id, speaker_input):
    # 1. Derive sub_context from speaker_input fields (instruction_kind,
    #    turn_metadata flags, push_back_reason_code, is_post_cap_advance,
    #    failed_signal_value).
    sub_ctx = _derive_sub_context(speaker_input)

    # 2. Pick opener (microseconds — pure Python).
    opener = self._opener_library.pick(
        kind=speaker_input.instruction_kind,
        sub_context=sub_ctx,
        recent_openers=self._recent_openers,
    )

    # 3. SpeakerInput.pre_spoken_opener tells Speaker which opener was
    #    spoken so it composes natural continuation content.
    speaker_input_with_opener = speaker_input.model_copy(
        update={"pre_spoken_opener": opener.text}
    )

    # 4. Kick off Speaker LLM call IN PARALLEL with opener playback.
    speaker_task = asyncio.create_task(
        self._speaker.stream(
            turn_id=turn_id,
            speaker_input=speaker_input_with_opener,
            correlation_id=self._correlation_id,
            tenant_id=self._tenant_id,
        )
    )

    # 5. Play opener audio (cache hit) or fall back to text TTS.
    if opener.text is not None:
        if opener.audio_iter is not None:
            opener_handle = await agent.session.say(
                text=opener.text,
                audio=opener.audio_iter(),
                allow_interruptions=True,
                add_to_chat_ctx=True,
            )
        else:
            # Cache miss / startup race — fall back to text TTS.
            opener_handle = await agent.session.say(
                text=opener.text,
                allow_interruptions=True,
                add_to_chat_ctx=True,
            )
        await opener_handle.wait_for_playout()
        # Track for anti-repetition.
        self._recent_openers.append(opener.text)
        # Audit.
        self._append(SPEAKER_OPENER_PLAYED, SpeakerOpenerPlayedPayload(
            turn_id=turn_id,
            instruction_kind=speaker_input.instruction_kind.value,
            sub_context=sub_ctx.value,
            opener_text=opener.text,
            cache_hit=opener.audio_iter is not None,
        ).model_dump())

    # 6. Get the Speaker handle (already running since step 4).
    handle = await speaker_task

    # 7. Continue with the existing flow (stream content, handle empty,
    #    handle interrupted, register_agent_utterance, etc.) — but
    #    register_agent_utterance no longer takes cache_text and stores
    #    text directly (cache cleanliness is by construction now).
    stream = handle.stream()
    speech_handle = await agent.session.say(
        stream, allow_interruptions=True, add_to_chat_ctx=True,
    )
    final_text = await handle.final_text()

    if not final_text.strip():
        if _was_interrupted(speech_handle):
            return await self._handle_interrupted_speaker(...)
        return await self._handle_empty_speaker_output(...)

    # ... existing SPEAKER_CALL / SPEAKER_OUTPUT events fire as today
    self._state.register_agent_utterance(
        turn_id=turn_id, text=final_text,
        instruction_kind=speaker_input.instruction_kind,
    )
    return final_text
```

The Speaker LLM call (step 4) starts before the opener audio finishes playing (step 5). By the time `await speaker_task` completes (step 6), the LLM is already generating; we then pipe its stream into the second `session.say`.

**Sequential `session.say()` calls** are used for v1. There is a small audible gap between opener end and content start (~150-300ms — TTS init for the second call). This is acceptable for v1; merging the two into a single AudioFrame iterator is a v2 optimization.

### 4.4 SpeakerInput schema change

**File:** `app/modules/interview_engine/models/speaker.py`

New field on `SpeakerInput`:

```python
pre_spoken_opener: str | None = Field(
    default=None,
    description=(
        "The conversational opener text (e.g., 'Got it.', 'Mhm.', "
        "'Let me put it differently.') that has ALREADY been spoken "
        "to the candidate as pre-cached audio BEFORE this Speaker "
        "call's content plays. The Speaker MUST compose its output "
        "as a natural continuation of the opener — do NOT include "
        "another opener, do NOT re-acknowledge with 'Got it' / 'Sure' "
        "/ etc. at the start. None means no opener was pre-played; "
        "the Speaker is free to start its content however reads "
        "naturally."
    ),
)
```

### 4.5 Speaker prompt overhaul

Every per-kind Speaker prompt under `prompts/v1/engine/speaker/` is updated:

**`_preamble.txt`** gets a new top-level instruction:

```
PRE-SPOKEN OPENER (load-bearing)
The input field `pre_spoken_opener` may contain a short opener phrase
that has ALREADY been spoken to the candidate as pre-cached audio
immediately before this LLM call. When `pre_spoken_opener` is set:
  - Compose your output to flow naturally from it.
  - Do NOT include any opener phrase ("Got it", "Sure", "OK", etc.)
    at the start of your output.
  - Pick up where the opener left off.

When `pre_spoken_opener` is null, no opener was pre-played; you may
start your content however reads naturally (but still avoid the chatbot
register listed under OUTPUT DISCIPLINE).
```

**Per-kind body prompts** (push_back.txt, clarify.txt, redirect.txt, etc.) are updated:
- New few-shot examples WITHOUT openers (showing pure content output)
- Existing "vary opener" / "anti-repetition opener" instructions removed (now orchestrator's responsibility)
- Examples re-anchored to show the content style only

Example for `push_back.txt` after update:

```
EXAMPLES (illustrative — compose your own; do not copy verbatim)

VAGUE_ANSWER (script)
  pre_spoken_opener: "Got it."
  last_candidate_utterance: "I would add, like, validation checks"
  push_back_reason_code: vague_answer
  → "Walk me through one validation check you'd actually write for
     this rule, even at a high level."

DEFLECTION (responsibility)
  pre_spoken_opener: "Fair."
  last_candidate_utterance: "I was particularly not responsible for
  the uptime SLAs. But, like, I did help implementing them."
  push_back_reason_code: deflection
  → "What was your specific contribution to those SLAs? Even one
     concrete thing you owned end to end."
```

**Files updated:**
- `_preamble.txt` (add pre_spoken_opener awareness)
- `deliver_first_question.txt` (special case — opener IS the persona intro "Hi, I'm Sam.")
- `deliver_question.txt`
- `deliver_probe.txt`
- `clarify.txt`
- `redirect.txt`
- `acknowledge_no_experience.txt`
- `polite_close.txt`
- `push_back.txt`

### 4.6 Audit events

**File:** `app/modules/interview_engine/event_kinds.py` + `audit_events.py`

New event kind: `speaker.opener.played`

```python
class SpeakerOpenerPlayedPayload(BaseModel):
    turn_id: str
    instruction_kind: str
    sub_context: str
    opener_text: str
    cache_hit: bool  # False if fell back to text-only TTS
```

The existing `SPEAKER_CALL`, `SPEAKER_OUTPUT`, `SPEAKER_INTERRUPTED`, `SPEAKER_OUTPUT_EMPTY`, and `SPEAKER_CACHED` events continue to fire for the content portion exactly as today.

### 4.7 Repeat behavior (cache cleanliness)

`StateEngine._resolve_repeat` is **unchanged**. It returns the most recent cached question text. Because the cache now stores ONLY the Speaker content (no opener prefix), replays are naturally clean:

> Candidate: "Can you repeat that question once again?"
> Agent: *plays repeat opener (cached audio: "OK.")* → *plays cached content: "Walk me through what those validation checks would actually do."*

Note: the orchestrator MAY also play a brief opener for the repeat itself (e.g., "OK." / "Sure.") — this is a per-`InstructionKind.repeat` decision in the OpenerLibrary. The cached content is replayed via the existing `SPEAKER_CACHED` path.

### 4.8 Anti-repetition state

Per-session deque tracking the last N opener texts used:

```python
self._recent_openers: collections.deque[str] = collections.deque(maxlen=5)
```

`OpenerLibrary.pick()` excludes deque-resident openers from the candidate pool. If exclusion empties the pool, fall back to allowing the longest-ago entry — still better than re-using the most recent.

The deque lives on the Orchestrator instance (one per session) so it resets per session and doesn't leak across candidates.

---

## 5. Opener vocabulary

The full curated library, organized by `(InstructionKind, SubContext)`.

**v1 scope:** vocabulary is static text only — no candidate-name interpolation. Every variant is a literal string that gets pre-synthesized once at engine startup and reused across all sessions.

**v2 enhancement (deferred):** name-interpolated variants (e.g., "Got it, Ishant.") would require per-session TTS synthesis at session start (~5-10 short calls). Out of scope for v1; mentioning here so we don't relitigate later.

### `deliver_first_question` — session opening

Special case: opener IS the persona intro. Speaker handles the entire utterance (no separate cache). Standard openers do not apply for turn 0.

### `deliver_question` — DEFAULT (clean advance after a substantive answer)

- "Got it."
- "Understood."
- "Right."
- "OK."
- "Mhm."
- "Thanks for walking me through that."
- "Thanks."

### `deliver_question` — POST_CAP_ADVANCE (cap-forced topic shift)

- "OK, let's switch gears."
- "Alright, moving on."
- "Let's try a different angle."
- "On a different note —"
- "Setting that aside for now —"

### `deliver_probe` — DEFAULT (drilling on same question)

- "Got it. And —"
- "Right. And —"
- "OK. And —"
- "Mhm. And —"
- "OK, on that —"
- "Building on that —"

### `push_back` — VAGUE_ANSWER

- "Got it."
- "OK."
- "Right —"
- "Mhm —"
- "Hmm —"
- "OK, let me press on that —"

### `push_back` — DEFLECTION

- "Fair."
- "Fair enough."
- "Understood."
- "Got it."
- "OK."

### `push_back` — MISSING_SPECIFICS

- "Right —"
- "OK —"
- "Got it —"
- "Mhm —"

### `push_back` — UNANSWERED_SUBQUESTION

- "OK on that —"
- "Got the first part —"
- "Right —"

### `clarify` — DEFAULT (candidate confused, rephrase needed)

- "OK, let me put it differently."
- "Let me reframe that."
- "Different way to ask that —"
- "Let me give you a more concrete example."
- "Hmm, OK — let me reword that."
- "Let me try a different angle."
- "Think of it this way —"

### `redirect` — SOCIAL_OR_GREETING

- "Hey there."
- "Hi there."
- "Hello."
- "Good to meet you."
- "Likewise."
- "Doing fine."

### `redirect` — OFF_TOPIC

- "Got it."
- "OK."
- "Right, but —"
- "Hmm —"
- "Noted."

### `redirect` — ABUSIVE

- "Alright."
- "OK."
- "Let's keep this professional —"
- "Hmm."

### `redirect` — INJECTION

- "OK."
- "Right —"
- "Let's stay focused —"
- "Back to the interview —"

### `acknowledge_no_experience` — DEFAULT

- "Got it."
- "Thanks for being upfront."
- "Appreciate the honesty."
- "Understood."
- "Fair enough."
- "OK, that's helpful to know."

### `polite_close` — DEFAULT (clean session completion)

No opener. The Speaker generates the entire close.

Optional warm sign-off:
- "Alright."
- "OK."

### `polite_close` — KNOCKOUT (after no-experience disclosure)

- "Thanks for being upfront."
- "Appreciate the honesty."
- "Got it."

### `repeat` — DEFAULT (cached delivery)

Brief opener acknowledging the request, then cached content plays:

- "OK."
- "Sure."
- "Right."

The orchestrator plays the opener via session.say(text+audio), then immediately plays the cached question content (already a deterministic replay path).

---

## 6. Data flow per turn

1. Candidate finishes speaking (end-of-utterance detected).
2. Orchestrator builds `JudgeInputPayload`, calls Judge LLM (~2s).
3. Judge returns `JudgeOutput` with action + payload + turn_metadata.
4. State Engine processes Judge output, returns `StateEngineDecision` with `SpeakerInput` (instruction_kind + flags + bank_text + recent_turns + ...).
5. Orchestrator `_stream_speaker_and_say`:
   - **(a)** Derives `sub_context` from SpeakerInput (instruction_kind + turn_metadata flags + reason_code + is_post_cap_advance + failed_signal_value).
   - **(b)** `OpenerLibrary.pick(kind, sub_context, recent_openers)` returns an `OpenerSelection`.
   - **(c)** Updates SpeakerInput with `pre_spoken_opener=opener.text`.
   - **(d)** Kicks off `speaker.stream(...)` as `asyncio.create_task` — runs in parallel with opener playback.
   - **(e)** Plays opener audio: `session.say(text=opener.text, audio=opener.audio_iter())`. Awaits playout (~500ms).
   - **(f)** Awaits the speaker_task → `handle`. By this point the Speaker LLM is already generating.
   - **(g)** Pipes Speaker stream to TTS via `session.say(stream)`.
   - **(h)** Caches `final_text` (content only) for repeat replay.
   - **(i)** Appends opener text to `_recent_openers` deque.
   - **(j)** Emits `speaker.opener.played` audit event.

Time-to-first-audio: opener TTS init + first frame transmission ≈ 100-150ms total. The Speaker LLM is generating in parallel, so by the time opener audio finishes (~500ms), the Speaker stream has begun producing.

---

## 7. Error handling and fallbacks

| Failure mode | Fallback |
|---|---|
| Opener cache empty / not pre-synthesized for this variant | Fall back to `session.say(text=opener.text)` — same outcome, slower (~1.4s TTFB). `cache_hit=False` on audit event. |
| Opener TTS playback fails mid-stream | Audio.pipeline.error logged. Continue to Speaker content. Candidate hears no opener; not catastrophic. |
| Opener selection finds no variants for a sub_context | OpenerLibrary falls back to `(kind, DEFAULT)` pool. If still empty, use `["OK.", "Got it.", "Mhm."]`. |
| Speaker LLM returns empty after opener already played | Existing empty-output fallback path (Phase 9.3) fires. The opener WAS heard by the candidate; the audit transcript reflects opener text only for that turn. |
| Speaker LLM call cancelled mid-stream (interruption) | Existing `speaker.interrupted` path (Phase 9.4). Opener was already heard. Transcript shows opener only. |
| Engine startup `build_opener_cache` fails entirely | Log error; engine continues. Every turn falls back to text-based TTS for openers (degraded but functional). Sessions work, latency win lost. |

---

## 8. Observability

### Per-turn (audit envelope)

- `speaker.opener.played` — new event per opener play. Includes `cache_hit`, `sub_context`, `opener_text`. Allows post-session analysis of opener distribution + cache effectiveness.
- `speaker.call` — unchanged, fires for content portion.
- `speaker.cached` — unchanged, fires for repeat replay.

### Per-session (audio_tuning_summary)

New aggregate fields:

```python
{
  "openers": {
    "total_played": int,
    "cache_hit_rate": float,         # 0.0-1.0
    "by_sub_context": dict[str, int],
    "fallback_to_text_count": int,
  }
}
```

### Per-engine boot

`engine.opener_cache.built` log event with `success_count`, `failed_variants`, `total_synthesis_time_ms`. Surfaces startup-time degraded-mode warnings.

---

## 9. Testing strategy

| Test surface | Coverage |
|---|---|
| OpenerLibrary unit | Vocabulary fully populated; `pick()` excludes recent_openers; falls back to DEFAULT when sub_context has no variants; falls back to safe pool when DEFAULT also empty; `_derive_sub_context` correctness across all `(kind, flags)` combinations |
| OpenerCacheBuilder unit | Cache populated for every variant; partial failure tolerated (some variants left `audio_frames=None`); BuildReport accurate |
| SpeakerInput model | `pre_spoken_opener` field present; defaults to None |
| Orchestrator integration | Opener and content play in sequence (verify two `session.say()` calls); cache stores content only; recent_openers updated per turn; SpeakerInput carries `pre_spoken_opener`; `speaker.opener.played` audit event emitted |
| State Engine | `register_agent_utterance` no longer takes `cache_text`; cache stores `text` directly (regression of the old kwarg-removal) |
| Composition test | Real StateEngine + mocked Judge/Speaker + mocked TTS — full turn flow with opener selection, parallel dispatch, and cache cleanliness |
| Speaker prompt loadable tests | `pre_spoken_opener` referenced in updated `_preamble.txt`; per-kind prompts contain new examples without openers; legacy "vary opener" guidance removed |
| Repeat replay test | Push_back → repeat replays Speaker content only (no opener leak); confirms regex strippers can be deleted |
| Regression tests for deleted code | `_strip_cap_advance_segue`, `_strip_push_back_opener`, `_strip_clarify_opener`, `_compute_cache_text` deleted; `cache_text` parameter removed from `register_agent_utterance` |
| Anti-repetition test | 10-turn run with same sub_context; verify all variants used before any repeats |

---

## 10. Migration

This is a breaking change to the Speaker prompt contract and the orchestrator dispatch flow. Cannot ship incrementally — Speaker prompts and orchestrator must change together.

**Single PR contains:**

1. New module `app/modules/interview_engine/openers/` (library.py + cache.py + tests)
2. New `SpeakerInput.pre_spoken_opener` field
3. Orchestrator dispatch rewrite
4. All Speaker prompt updates (`_preamble.txt` + 8 per-kind prompts)
5. New `speaker.opener.played` audit event + audit_envelope changes
6. Engine startup wiring (call `build_opener_cache` in `entrypoint`)
7. Deletion of regex strippers + `_compute_cache_text` + `cache_text` parameter (Phase 9.5 + 9.7 cleanup)
8. Test updates for all touched files + new tests per §9

After merge: every new session uses the new path. No data migration; no in-flight session compatibility concern (each session is independent and engine restart picks up new code).

---

## 11. Open questions

None blocking. All decisions are committed.

The two minor open items, deferred to v2 if needed:

- **Single merged AudioFrame stream** (vs sequential `session.say()` calls) to eliminate the ~150-300ms gap between opener and content. Current v1 accepts the gap as not user-visible at conversational pace.
- **Per-tenant opener vocabulary customization** (e.g., enterprise client wants their interviewer persona to use different openers). Out of scope; current library is global per engine.
