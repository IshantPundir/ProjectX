# Deepgram nova-3 + en-IN with LLM-extracted keyterm prompting

**Status:** Draft for user review · **Date:** 2026-05-19 · **Supersedes:** regex-extraction design (commits a927a69…9cd8fdd in the same file)

## Summary

The interview engine's STT is Sarvam `saaras:v3` (en-IN). Sarvam's TTS quality is acceptable but its
STT mistranscribes domain-specific technical vocabulary — brand names ("MuleSoft", "TIBCO", "Boomi"),
protocol/architecture terms ("API-led", "iPaaS", "ESB"), and candidate names — frequently enough to
degrade the downstream Judge + State Engine. Session `engine-events/a0388c8e-…json` shows
representative damage and a 5.3s p95 transcription delay — well above the 200–300ms STT budget.

Deepgram `nova-3` supports `en-IN` natively and exposes **Keyterm Prompting** — a per-request list
of 20–50 boostable terms that biases recognition toward role-specific vocabulary. This spec migrates
the default STT path to `deepgram/nova-3/en-IN` and adds an **LLM-extracted, per-bank-cached**
keyterm list passed into `deepgram.STT(keyterm=[…])` at every session start.

**Why LLM extraction, not regex/heuristic:** the diversity of roles (cloud infra `S3 EC2 Lambda`,
ML `LangGraph PyTorch HuggingFace`, fintech `ACH SWIFT ISO20022`, legacy enterprise `MuleSoft TIBCO
ServiceNow`, …) makes any fixed token regex brittle. An LLM call against the curated question bank +
company profile + role summary catches multi-word brands ("Dell Boomi"), digit-bearing identifiers
("S3"), domain abbreviations ("ESB"), and mixed-case terms ("iPaaS") uniformly without per-domain
tuning.

**Why at bank-generation time, not session start:** the question bank already runs an LLM per stage
(`question_bank/actors.py:generate_question_bank_stage`). Adding one final keyterm-extraction call
at the end of that actor, writing the result to a new `stage_question_banks.extracted_keyterms`
JSONB column, gives:

- **Zero session-start latency** — engine reads a cached field; no LLM call in the hot path
- **No new failure mode for live sessions** — STT plugin construction stays synchronous
- **Stale-tracking for free** — the bank's existing `is_stale` flag regenerates keyterms whenever the
  bank regenerates (pipeline edit, signal change, etc.)
- **Recruiter visibility (future)** — keyterms sit on the bank row, ready for a UI surface

Sarvam stays in the codebase as a switchable alternate (toggle via `INTERVIEW_STT_PROVIDER=sarvam`).
The Sarvam branch in `realtime.py` ignores the keyterm argument.

## Non-goals

- **Not removing Sarvam.** Sarvam-specific env knobs (`INTERVIEW_STT_MODE`) stay; the dispatch in
  `realtime.py:build_stt_plugin` stays a two-provider switch.
- **Not re-tuning endpointing/EOU.** Separate observation pass after we have real Deepgram logs.
- **Not adding mid-session keyterm updates.** Nova-3 takes keyterm at websocket open only.
- **Not changing TTS.** Sarvam `bulbul:v3` quality is acceptable; out of scope.
- **Not changing VAD, turn detector, noise cancellation, or adaptive interruption.**
- **Not touching the conversational-continuation watcher.** The pre-Speaker cancellation watcher in
  `orchestrator.py` (fields `continuation_enabled`, `continuation_min_word_count`,
  `continuation_consecutive_abort_cap`, state `_pending_continuation_text`, events
  `turn.aborted_for_continuation` + `turn.stitched_continuation`, spec
  `docs/superpowers/specs/2026-05-17-conversational-continuation-design.md`) is the load-bearing
  defense against EOU mis-firing on long candidate pauses. It is preserved verbatim. None of the
  files touched by this migration modify the orchestrator's stitching logic, its config defaults,
  or its event payload shapes.
- **Not backfilling existing banks.** Per user confirmation (2026-05-19, dev mode, no real users):
  legacy banks with `extracted_keyterms IS NULL` are not migrated. The recruiter regenerates banks
  to populate keyterms. The engine ships an empty/minimal keyterm list (candidate first name only)
  for any bank whose keyterm extraction hasn't run yet — Deepgram still functions, just without the
  brand boost.

## What changes

### 1. Migration `0029_extracted_keyterms`

Add one JSONB column on `stage_question_banks`:

```sql
ALTER TABLE stage_question_banks
  ADD COLUMN extracted_keyterms JSONB NULL;
```

- **Nullable** — legacy banks won't be backfilled; null = "extraction hasn't run for this bank yet".
- **No new RLS policy needed** — the table is already tenant-scoped; the column lives inside the row.
- **Stale-tracking is free** — when a bank regenerates (`is_stale=true` flow), the actor overwrites
  this column as its final step.

Includes a rollback (`DROP COLUMN`). No data dependency.

### 2. AI schema: `KeytermExtractionOutput` in `app/ai/schemas.py`

```python
class KeytermExtractionOutput(BaseModel):
    """Output schema for the per-bank keyterm extraction LLM call.

    Used by question_bank/actors.py to populate stage_question_banks.extracted_keyterms.
    """
    keyterms: list[str] = Field(min_length=10, max_length=50)

    @model_validator(mode="after")
    def _validate_each_term(self) -> "KeytermExtractionOutput":
        for term in self.keyterms:
            if not term.strip():
                raise ValueError("keyterms must not contain empty strings")
            if len(term) > 80:
                raise ValueError(f"keyterm too long ({len(term)} chars): {term!r}")
        return self
```

Strict bounds (10–50 entries) ensure the LLM doesn't return a useless 2-term list or blow past
Deepgram's 50-term recommendation.

### 3. Prompt: `prompts/v1/question_bank_keyterms.txt`

Single versioned prompt file. System prompt instructs the model to extract 20–40 role-specific
keyterms from the bundle (job title + company profile + role summary + signal list + every question
in the bank), preserving capitalization for proper nouns, including multi-word brand names, and
excluding generic English filler. The user-message template is built in `actors.py` from the same
context the existing per-question refinement uses, plus the now-final question list.

### 4. Bank-generator actor extension: `app/modules/question_bank/actors.py`

`generate_question_bank_stage` is extended with one final step after all per-question refinement
completes successfully:

```python
# Final step: extract STT keyterms from the confirmed question bundle
keyterm_output = await _extract_bank_keyterms(
    job=job, company_profile=company_profile, role_summary=role_summary,
    signals=signals, questions=final_questions,
)
await db.execute(
    update(StageQuestionBankModel)
    .where(StageQuestionBankModel.id == bank.id)
    .values(extracted_keyterms=keyterm_output.keyterms),
)
```

`_extract_bank_keyterms` is a private helper in `app/modules/question_bank/refine.py` (sibling to
the existing per-question refine helpers), structured identically: `get_openai_client()` +
`prompt_loader.load_pair("question_bank_common", "question_bank_keyterms")` +
`set_llm_span_attributes(...)` + `client.chat.completions.create(response_model=KeytermExtractionOutput, ...)`.
The actor in `actors.py` calls it once after the per-question refinement loop completes.

**Idempotency:** if the bank regenerates, the keyterm call runs again and overwrites the column.

**Failure mode:** if the keyterm call fails, the actor logs structlog `question_bank.keyterm_extraction.failed`
with the bank id and continues — the bank itself is still useful without keyterms; the engine will
fall back to candidate-name-only. This matches the codebase's existing "AI augments, never blocks"
pattern.

### 5. `AIConfig` gains `question_bank_keyterm_model`

```python
question_bank_keyterm_model: str = "gpt-5.4-nano-2026-03-17"
```

Defaults to the same fast/cheap model used by Speaker. Env override:
`QUESTION_BANK_KEYTERM_MODEL`. Allows future re-tuning without code change.

### 6. `interview_runtime/schemas.py` — new `SessionConfig.keyterms` field

```python
class SessionConfig(BaseModel):
    ...
    keyterms: list[str] = Field(
        default_factory=list,
        description=(
            "STT keyterm-prompting list, extracted at bank-generation time and "
            "cached on stage_question_banks.extracted_keyterms. Empty list when "
            "the bank hasn't had keyterm extraction run yet — the engine then "
            "falls back to candidate-name-only boosting."
        ),
    )
```

`build_session_config` in `interview_runtime/service.py` loads the value from the bank row and sets
the field. If `extracted_keyterms IS NULL`, the field stays `[]`.

### 7. Engine merger: `app/modules/interview_engine/keyterms.py`

Replaces the deleted regex extractor. A 20-line pure function that combines the bank-cached
keyterms with session-specific values (the candidate's first name) and returns a
`KeytermExtraction` dataclass:

```python
from dataclasses import dataclass
from app.modules.interview_runtime.schemas import SessionConfig

_KEYTERM_CAP = 50

@dataclass(frozen=True)
class KeytermExtraction:
    terms: list[str]
    sources: dict[str, int]

def assemble_keyterms(session_config: SessionConfig) -> KeytermExtraction:
    terms: list[str] = []
    sources: dict[str, int] = {}

    def _add(term: str, source: str) -> None:
        if not term or len(terms) >= _KEYTERM_CAP:
            return
        if any(t.lower() == term.lower() for t in terms):
            return
        terms.append(term)
        sources[source] = sources.get(source, 0) + 1

    # Candidate first name is the only session-specific term —
    # everything else is bank-cached
    if session_config.candidate.name.strip():
        _add(session_config.candidate.name.split()[0], "candidate_name")

    for term in session_config.keyterms:
        _add(term, "bank_cached")

    return KeytermExtraction(terms=terms, sources=sources)
```

No regex. Insertion order preserves candidate name as keyterm #1 (always survives the cap).

### 8. STT factory + `realtime.py` + `agent.py` wiring (unchanged from prior spec revision)

`stt_factory.py:build_stt_plugin_for_session(session_config) -> tuple[_BaseSTT, KeytermExtraction]`
calls `assemble_keyterms(session_config)`, forwards `extraction.terms` to
`build_stt_plugin(keyterms=…)`, and returns both. `realtime.py:build_stt_plugin` and
`_build_stt_deepgram` accept the optional `keyterms` kwarg and pass it as the `keyterm` REST API
parameter to `deepgram.STT(...)` when non-empty. `agent.py` unpacks the tuple, emits the
`audio.stt.keyterms_applied` audit event, and constructs `AgentSession(stt=stt_plugin, …)`.

### 9. Default-flip changes

`AIConfig` (via the underlying `Settings` defaults in `app/config.py`) field defaults change:

| Field | Old default | New default |
|---|---|---|
| `interview_stt_provider` | `"sarvam"` | `"deepgram"` |
| `interview_stt_model` | `"saaras:v3"` | `"nova-3"` |
| `interview_stt_language` | `"en-IN"` | `"en-IN"` (unchanged) |
| `interview_stt_mode` | `"transcribe"` | `"transcribe"` (unchanged; sarvam-only) |
| `question_bank_keyterm_model` | (new) | `"gpt-5.4-nano-2026-03-17"` |

`.env.example` mirrors. The comment block above the STT section flips: Deepgram is now the default,
Sarvam is the alternate.

### 10. New audit event: `audio.stt.keyterms_applied`

Registered in `event_kinds.py`; payload model `STTKeytermsAppliedPayload` in `audit_events.py`.
Emitted from `agent.py` once, after `build_stt_plugin_for_session` returns and before
`AgentSession(...)` construction.

```json
{
  "provider": "deepgram",
  "count": 31,
  "terms": ["Ishant", "Workato", "MuleSoft", "TIBCO", "Dell Boomi", "S3", "API-led", "..."],
  "sources": {
    "candidate_name": 1,
    "bank_cached": 30
  }
}
```

`redaction="full"`. No PII risk — these are role/company/candidate-name metadata.

When `provider="sarvam"` is toggled back: `count=0`, `terms=[]`, `sources={"candidate_name": 1}` —
the audit event still fires for parity (so a session with sarvam doesn't look like a missing event).

### 11. Tests

| Coverage area | File | Notes |
|---|---|---|
| Migration | `tests/db/test_migration_0029.py` (or piggyback on existing migration tests) | Upgrade adds the column NULL-able; downgrade drops it |
| Schema validation | `tests/ai/test_schemas.py` (add to existing) | Pydantic bounds: < 10 raises; > 50 raises; empty string in list raises; 80+ char term raises |
| Prompt-loadable | `tests/ai/test_prompt_loader.py` (add) | `prompt_loader.load_pair("question_bank_common", "question_bank_keyterms")` returns non-empty system + user templates |
| Bank actor — LLM call mocked | `tests/question_bank/test_actors_keyterm.py` (new) | With instructor mocked to return a known `KeytermExtractionOutput`, asserts the column is populated on the bank row after `generate_question_bank_stage` completes |
| Bank actor — LLM failure tolerated | same file | Mock raises; actor logs and continues; bank still marked confirmed; column stays NULL |
| Engine merger | `tests/interview_engine/test_keyterms.py` (new) | Pure unit tests against `assemble_keyterms`: candidate-name-only when `session_config.keyterms` is empty; merging preserves order with candidate first; case-insensitive dedupe; 50-cap with insertion-order guarantee |
| `build_session_config` | `tests/interview_runtime/test_session_config_keyterms.py` (new) | When `stage_question_banks.extracted_keyterms IS NOT NULL`, the field is propagated; when NULL, defaults to empty list |
| Audit-event payload | `tests/interview_engine/test_audit_events.py` (add) | `STTKeytermsAppliedPayload` accepts both provider values |

No integration test against the live Deepgram API — the manual smoke (one real interview) is the
end-to-end validation, per the project's documented preference (memory
`feedback_manual_agent_testing.md`).

## Risks & open questions

1. **Empirical: does `language=en-IN` + `keyterm` work together in nova-3?** Deepgram's blog says
   "Keyterm Prompting is available for both monolingual and multilingual transcription using the
   Nova-3 Models". LiveKit's "Supported configurations" doesn't explicitly enumerate `en-IN`
   keyterm pairing, but the plugin source doesn't gate by language. Empirically confirmed by the
   first real interview; rollback is one env var.
2. **Code-mix Hindi-English candidates.** Out of scope. Toggle back to Sarvam via env if needed.
3. **LLM call adds ~1s to bank generation.** Negligible compared to existing ~30s bank-generation
   wall time.
4. **Bank without keyterms before regeneration.** The engine falls back to candidate-name-only;
   Deepgram still functions, just without brand boost. User has confirmed (2026-05-19) this is
   acceptable in dev mode — no backfill needed.
5. **Prompt quality is now the dominant determinant of keyterm quality.** First-pass prompt will
   ship; iterate based on session-log inspection. Prompt versioning (v1/, v2/, …) gives a clean
   roll-back path.

## Out of scope (explicit YAGNI)

- Hybrid LLM + regex extraction.
- Caching at a level higher than the bank (e.g., per-job-posting). Per-bank is the natural cache
  granularity that matches existing stale-tracking.
- Mid-session keyterm updates (Flux-only, irrelevant to nova-3).
- Sarvam-branch removal.
- Re-tuning `engine_endpointing_max_delay` based on Deepgram's lower transcription latency.
- Adding `language=multi` support / billing toggle.
- Adding a recruiter-side keyterm override / edit UI.
- Backfilling `extracted_keyterms` for existing banks (dev-mode acceptable, per user 2026-05-19).
