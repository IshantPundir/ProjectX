# Deepgram nova-3 + en-IN keyterm migration

**Status:** Draft for user review · **Date:** 2026-05-19

## Summary

The interview engine's STT is Sarvam `saaras:v3` (en-IN). Sarvam's TTS quality is acceptable, but the
STT mistranscribes domain-specific technical vocabulary — brand names ("MuleSoft", "TIBCO", "Boomi",
"Salesforce"), protocol/architecture terms ("API-led", "iPaaS", "ESB", "JSON Schema"), and candidate
names — frequently enough to degrade the downstream Judge + State Engine, which see corrupted text
and either over-clarify, fall back to `clarify`, or fire a validation error. Session
`engine-events/a0388c8e-…json` shows representative damage: a "Hello" fragment stitched onto a fresh
utterance triggered a Judge cross-field validation error (`candidate_social_or_greeting=true` +
`next_action=acknowledge_no_experience`), forcing a fallback `clarify`. The session-level
`audio.tuning_summary` reports STT transcription delay p95 = 5304ms, end-of-utterance delay p95 =
5487ms — well above the 200–300ms STT budget documented in `backend/nexus/CLAUDE.md`.

Deepgram `nova-3` supports `en-IN` natively and exposes **Keyterm Prompting** — a per-request list of
20–50 boostable terms that biases recognition toward role-specific vocabulary. This spec migrates the
default STT path to `deepgram/nova-3/en-IN` and wires a deterministic per-session keyterm extractor
that reads from the already-curated `SessionConfig` (`signal_snapshot`, `company_profile`,
`candidate`, `tenant`, `job`) and passes the resulting list into `deepgram.STT(keyterm=[…])`.

**Why deterministic, not LLM-extracted:** the `signal_snapshot` is already a recruiter-confirmed,
AI-validated list of canonical signal phrases. ~90% of high-leverage tech vocabulary for a given
role lives in those phrases. Extracting deterministically (regex + tokenization) costs zero latency
at session start, is unit-testable, and produces no new LLM failure mode. If empirical results show
gaps (specific role types where keyterms underperform), the upgrade path is a one-time cached LLM
augmentation per `(stage_question_bank, pipeline_version)` — not a per-session call.

**Sarvam stays in the codebase** as a switchable alternate (toggled via `INTERVIEW_STT_PROVIDER=sarvam`)
so that a Deepgram regression has an in-tree fallback. The Sarvam branch ignores the keyterm
argument.

## Non-goals

- **Not removing Sarvam.** Sarvam-specific env knobs (`INTERVIEW_STT_MODE`) stay; the dispatch in
  `realtime.py:build_stt_plugin` stays a two-provider switch.
- **Not re-tuning endpointing/EOU.** Deepgram's lower transcription latency *should* let
  `engine_endpointing_max_delay` shrink back from 4.5s toward 3.0s, but that's a separate
  observation pass after we have real Deepgram session logs.
- **Not adding mid-session keyterm updates.** Nova-3 takes keyterm at websocket open and does not
  support runtime reconfiguration (that's a Flux-only feature). One-shot extraction at session
  start is the only path.
- **Not adding a hybrid (LLM-augmented) extraction strategy.** Deterministic only for v1.
- **Not changing TTS.** Sarvam `bulbul:v3` TTS quality is acceptable; out of scope.
- **Not changing VAD, turn detector, noise cancellation, or adaptive interruption.** Those layers
  are orthogonal to STT.

## What changes

### 1. New file: `app/modules/interview_engine/keyterms.py`

A single pure function `extract_keyterms(session_config: SessionConfig) -> list[str]`. No I/O, no
LiveKit deps, no asyncpg.

**Field rules:**

| Source | Rule |
|---|---|
| `candidate.name` | First whitespace-split token only |
| `tenant.name` | As-is |
| `company_profile.org_unit_name` | As-is (when present) |
| `org_unit_ancestry[].name` | All entries, deduped against the above |
| `job.title` | As-is |
| `signal_snapshot.signals[].value` (each phrase) | Two-pass: (a) emit the full phrase as one keyterm, (b) extract proper-noun-looking tokens (CapitalizedWords, ALL_CAPS, hyphenated like "API-led") and emit each individually |
| `signal_snapshot.role_summary` | Extract proper-noun tokens only (prose is too noisy as a phrase) |

**Filtering / normalization (applied at the end):**

- Drop generic English filler against a small stopword list: `the, and, or, with, for, from, into,
  via, of, on, in, at, to, a, an, is, are, was, were`.
- Drop single-letter tokens and pure-digit tokens.
- Strip leading/trailing punctuation (commas, parens, periods) from individual tokens; keep
  internal punctuation that matters ("API-led", "Sr.").
- Dedupe **case-insensitively**, keeping the first-seen casing. Order is insertion order.
- Cap final list at **50** entries (Deepgram recommends 20–50; their hard cap is 500 tokens).

**Proper-noun token extraction regex (precise):**

A token qualifies if it matches at least one of:

- `^[A-Z][a-zA-Z]+(?:-[A-Z][a-zA-Z]+)*$` — CapitalizedWord, optionally Hyphenated-CamelCase
- `^[A-Z]{2,}$` — ALL_CAPS acronym (≥2 chars)
- `^[A-Z][a-zA-Z]*-[a-z][a-zA-Z]*$` — Mixed like "API-led"
- `^[A-Z][a-zA-Z0-9]*\d[a-zA-Z0-9]*$` — Brand-with-digit like "S3", "OAuth2"

Each candidate token is also filtered against the stopword list (so common words that happen to
start a sentence don't sneak in) and against a small denylist of generic capitalized words that
appear at sentence starts: `The, This, That, We, You, It, In, On, At, For, As`.

### 2. Wiring: `stt_factory.py`, `realtime.py`, and `agent.py`

`stt_factory.py:build_stt_plugin_for_session` replaces today's pass-through and returns a tuple
so the caller can audit-log the keyterms without re-computing them:

```python
from app.ai.realtime import build_stt_plugin
from app.modules.interview_engine.keyterms import extract_keyterms
from app.modules.interview_runtime.schemas import SessionConfig

def build_stt_plugin_for_session(
    *, session_config: SessionConfig,
) -> tuple["_BaseSTT", list[str]]:
    keyterms = extract_keyterms(session_config)
    return build_stt_plugin(keyterms=keyterms), keyterms
```

`agent.py` unpacks the tuple, emits the audit event, then passes the STT to `AgentSession`:

```python
stt_plugin, keyterms = build_stt_plugin_for_session(session_config=session_config)
emit_audit_event(
    "audio.stt.keyterms_applied",
    payload={"provider": ai_config.interview_stt_provider, "count": len(keyterms), "terms": keyterms, "sources": {...}},
)
session = AgentSession(stt=stt_plugin, llm=..., tts=..., ...)
```

`realtime.py:build_stt_plugin` accepts an optional `keyterms: list[str] | None = None`. The
sarvam branch ignores it (no-op). The deepgram branch passes it as the `keyterm` kwarg (singular —
matches Deepgram REST API naming) when non-empty.

```python
def _build_stt_deepgram(keyterms: list[str] | None = None) -> "_BaseSTT":
    from livekit.plugins import deepgram

    kwargs: dict[str, object] = {
        "model": ai_config.interview_stt_model,
        "language": ai_config.interview_stt_language,
    }
    if keyterms:
        kwargs["keyterm"] = keyterms

    logger.info(
        "ai.realtime.stt.built",
        provider="deepgram",
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
        keyterm_count=len(keyterms) if keyterms else 0,
    )
    return deepgram.STT(**kwargs)
```

### 3. Default flip: `AIConfig` and `.env.example`

`app/ai/config.py` field defaults change:

| Field | Old default | New default |
|---|---|---|
| `interview_stt_provider` | `"sarvam"` | `"deepgram"` |
| `interview_stt_model` | `"saaras:v3"` | `"nova-3"` |
| `interview_stt_language` | `"en-IN"` | `"en-IN"` (unchanged) |
| `interview_stt_mode` | `"transcribe"` | `"transcribe"` (unchanged; sarvam-only, unused with deepgram) |

`.env.example` mirrors. The comment block above the STT section is updated to note that Sarvam is now
the alternate/rollback path.

### 4. New audit event: `audio.stt.keyterms_applied`

Registered in `app/modules/interview_engine/event_kinds.py`. Emitted from `agent.py` once, after
`build_stt_plugin_for_session` returns its `(stt, keyterms)` tuple and *before* the `AgentSession(...)`
constructor call. The provider name comes from `ai_config.interview_stt_provider` so the event is
truthful when sarvam is toggled back (provider="sarvam", count=0, terms=[]).

**Payload shape:**

```json
{
  "provider": "deepgram",
  "count": 32,
  "terms": ["Ishant", "BinQle", "Workato", "MuleSoft", "TIBCO", "Dell Boomi", "Salesforce", "API-led", "..."],
  "sources": {
    "candidate_name": 1,
    "tenant": 1,
    "org_units": 2,
    "job_title": 1,
    "signal_phrases": 7,
    "proper_nouns": 20
  }
}
```

`redaction="full"`. Keyterms are role/company/candidate-name metadata, no resume content, no
transcripts — same risk class as `model_versions` already emitted.

**Why this event is load-bearing:** when STT quality regresses on a specific role, the only way to
debug is to know *what context the STT saw*. The existing `model_versions.stt` envelope tells you
which model was used; this event tells you which terms were boosted. Without it, regression
investigations are blind.

### 5. Tests

New file `backend/nexus/tests/interview_engine/test_keyterms.py`. Pure unit tests against
hand-built `SessionConfig` fixtures (no DB, no LiveKit, no network). Cases:

1. **Minimum input** — empty `signal_snapshot.signals`, no company profile → returns at minimum
   `[candidate_first_name, tenant_name, job_title]`.
2. **List-style signal expansion** — signal value "MuleSoft, TIBCO, or Dell Boomi" produces both
   the full phrase and each brand name individually.
3. **Proper-noun extraction from role_summary** — role_summary containing "ESB/iPaaS (MuleSoft/TIBCO/Dell Boomi)"
   yields "ESB", "iPaaS", and the brand names.
4. **Case-insensitive dedupe** — input that contains "MuleSoft" and "mulesoft" emits only the
   first-seen casing.
5. **Stopword filtering** — single-letter, pure-digit, generic stopword tokens are dropped.
6. **Candidate name normalization** — `"Ishant Pundir"` → `"Ishant"` only.
7. **Cap enforcement** — synthetic 200-signal input emits exactly 50 terms; the 50 selected are
   the first-seen (insertion order preserved).
8. **Snapshot test** — feeds a committed JSON fixture
   (`backend/nexus/tests/interview_engine/fixtures/session_config_mulesoft_sample.json`, derived
   from the canonical `build_session_config` output for the MuleSoft Integration Engineer sample)
   through and asserts the frozen expected keyterm list. Acts as the canonical fixture-driven
   regression guard. The transient `tmp/interview_context.json` is NOT used as a test fixture —
   the test fixture is a committed copy.

No integration / docker-compose test is included in this PR. The manual smoke (real Deepgram call
against a real session) is intentionally outside the test gate — the user runs one real interview
and inspects transcripts. This matches the project's documented preference for "manual testing for
AI agents" (memory: `feedback_manual_agent_testing.md`).

## Risks & open questions

1. **Empirical: does `language=en-IN` + `keyterm` work together in nova-3?** Deepgram's blog quote
   confirms "Keyterm Prompting is available for both monolingual and multilingual transcription
   using the Nova-3 Models". The LiveKit "Supported configurations" table doesn't explicitly
   enumerate `en-IN` keyterm pairing, but the plugin source doesn't gate by language. The first
   real interview after merge is the empirical confirmation. Rollback is one env var.
2. **Code-mix Hindi-English candidates.** Sarvam's `mode="codemix"` is purpose-built for this; Nova-3
   monolingual `en-IN` won't handle Hindi insertions cleanly. If a candidate population emerges that
   needs this, options are (a) switch language to `multi` (paid at multilingual rate; covers Hindi +
   English) or (b) toggle back to Sarvam for those tenants. Out of scope for this PR.
3. **Keyterm cap interaction with very long signal lists.** Some roles may produce >50 proper-noun
   candidates. The first-50 cap is order-stable (insertion order), so the most important fields
   (candidate name, tenant, company, job title) always make the cut — they're emitted first.
4. **Audit log size.** The new event adds ~1KB per session to `engine-events/*.json`. Negligible.

## Out of scope (explicit YAGNI)

- LLM-extracted or hybrid keyterm strategy.
- Caching keyterms in `stage_question_banks` (extraction is microseconds — caching adds a migration
  for no real-world payoff).
- Mid-session keyterm updates (Flux-only, irrelevant to nova-3).
- Sarvam-branch removal.
- Re-tuning `engine_endpointing_max_delay` based on Deepgram's lower transcription latency.
- Adding `language=multi` support / billing toggle.
- Adding a recruiter-side keyterm override UI.
