# Engine prompts v2 — changelog

## 2026-05-18 — Intent layer (clarify_kind + opener pruning)

**Judge:**
- §1.3 CLARIFY sub-tree expanded from 2 sub-cases to 5
  (term_definition / concept_explanation / use_case_anchor /
  broad_rephrase / probe_context).
- Added disambiguation rules: concept_explanation vs push_back,
  vs meta_confession; "give me an example" trichotomy.
- §4 CLARIFY description updated.
- §8 worked examples G, H, I added (concept_explanation,
  use_case_anchor, concept_explanation chosen over push_back).

**Speaker:**
- `clarify.txt` rewrites the dispatch into 5 PATH sections keyed
  on the new `clarify_kind` field in SpeakerInput. PATH B
  rewritten to model domain+volume+contrast. PATH D and PATH E
  are new. PATH E carries a load-bearing anti-leak boundary
  with an explicit forbidden-verb list.
- `_preamble.txt` §OPENINGS gains a paragraph referencing
  `available_openers` — the per-turn pruned rotation supplied
  in the user message.

**Code (not prompt, but tied to this rev):**
- New helper `app/modules/interview_engine/speaker/openers.py`
  with `opener_slug` + `filter_available_openers`. Lifted out
  of `naturalness.py`.
- `SpeakerInput.clarify_kind` and `SpeakerInput.available_openers`
  added.
- `_SOFT_TARGETS` keyed by `(instruction_kind, clarify_kind|None)`
  with backward-compat fallback.
- `detect_solution_leak` informational flag for PATH E.
- State Engine fix: `is_post_cap_advance` now fires whenever
  `push_back_count >= 2` before an advance, regardless of
  whether the Judge or the SE chose advance.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md

## 2026-05-17 — v2 ships (interview-engine v2)

Spec: `docs/superpowers/specs/2026-05-17-interview-engine-v2-design.md`.

### Judge prompt (`judge.system.txt`)

Full rewrite, ~38% line cut, decision tree at top, single-canonical-statement
per invariant.

New sections:
- §1 DECISION TREE (moved to top, was §8 at line 555 in v1)
- §3 REASONING FIELD (new — defends against LMSF reasoning-quality drop)
- §6 CANDIDATE CLAIMS (new dedicated section; v1 had only one passing mention)
- §5 OBSERVATIONS FAN-OUT rule (new — one utterance often touches multiple signals)
- §4 META-CONFESSION rule (new — bluff-catch design intent encoded as a flag)

Removed:
- "session XYZ caused rule Y" notes moved to this CHANGELOG (~30-50 lines)
- 5× restatement of failure-obs rule collapsed to one canonical §5 statement
- 4× restatement of push_back collapsed to one canonical §4 entry
- 4× restatement of VERBATIM collapsed to one §7 anti-leak statement
- 5× restatement of acknowledge_no_experience collapsed to one §4 entry
- "Output ONE JSON object" reminder (the strict schema API enforces this)
- Misleading prompt-code drift: "the validator will reject" claim on
  push_back+concrete (validator was relaxed 2026-05-12; State Engine's
  inverse_quality_gate handles policy)

Rewritten:
- "I don't know" disambiguation now uses ActiveSignalMeta.type (added in v2
  schema cluster); v1 referenced a field that didn't exist
- `time_remaining_seconds` floor specified as 60 seconds (was unspecified)
- `evaluation_hint` field now used (§3 informs observation quality grading);
  v1 listed it as input but never told the Judge what to do with it

Absolutes audit: NEVER / MUST / DO NOT count reduced ~50%. Most "always"-class
rules downgraded to "prefer" / "by default" — saving reasoning model tokens
on reconciling absolutes.

Reference sessions for the rules:
- f665498d turn 2 — repeat misclassified as clarify (motivated the §1 DECISION
  TREE repeat entry + the orchestrator pre-filter)
- 1f02f55d turns 13-14 — no_experience flag set without acknowledge_no_experience
  action (motivated _check_no_experience_action_alignment validator, kept from v1)
- 70c126b4 turn 8 — false-knockout via meta-confession misread (motivated
  the meta_confession flag + State Engine promotion)
- 70c126b4 turn 2 — greeting with clarify action (motivated
  _check_greeting_action_alignment validator)
- 70c126b4 turn 7 — illegal sufficient→failed with anchor_id=0 (the existing
  failure-obs invariant; left as-is since the validator already catches it)

### Speaker prompts (`speaker/*.txt`)

Substantively edited:
- `deliver_question.txt` — added ANTI-PATTERN section for 3-verb conjunctions
  (from session 70c126b4 turn 4: "assess, stabilize, and standardize" preserved
  all three from bank_text)
- `clarify.txt` — same ANTI-PATTERN for 3-criterion enumeration; new
  PROBE-CONTEXT branch (the Speaker input_builder fix in cluster 5 now passes
  probe text on clarify-after-probe; this prompt branch handles it)
- `push_back.txt` — added ANTI-PATTERN for stacked asks (from session 70c126b4
  turn 7: "walk through one integration ... including specific checks and
  rollback steps" stacks 3 asks); strengthened single-ask rule

Mechanically copied unchanged: `_preamble.txt`, `deliver_first_question.txt`,
`deliver_probe.txt`, `redirect.txt`, `polite_close.txt`,
`acknowledge_no_experience.txt`, `repeat.txt`.

---

## 2026-05-17 — v2 Speaker realism rewrite

Spec: `docs/superpowers/specs/2026-05-17-speaker-realism-design.md`.
Plan: `docs/superpowers/plans/2026-05-17-speaker-realism.md`.

Full rewrite of the v2 Speaker prompt set + persona module to make the
candidate hear **Arjun, Senior Engineering Manager (pronounced Indian
English)** consistently across all 9 InstructionKinds and all 3 canned
fallback paths. Diagnostic: session `d82d7407-5d7b-4004-af35-cc1ab50768c8`
(7/8 contextual turns echoed bank vocabulary, 2 redirect turns leaked
rubric content, 0 opener variation across 16 turns).

### `_preamble.txt` — full rewrite

Now a PersonaSpec template (~1200 tokens after render). Eight labeled
sections per OpenAI Realtime prompting guide structure:

1. WHO YOU ARE — Arjun's identity (`{name}`, `{archetype}`)
2. VOICE — `{register}`, opener rotation, vocab cues, disfluency density,
   name-usage policy
3. OUTPUT RULES — spoken-output framing, em-dash pause encoding (no SSML
   — Sarvam bulbul:v3 doesn't parse it), acronym spell-out rule
4. BANNED PHRASES — `{vocab_banned_bulleted}` + principle
5. ANTI-LEAK (load-bearing) — 4 numbered rules including
   ANTI-ENUMERATION
6. OPENINGS & ANTI-REPETITION — `recent_reply_starts` contract
7. CONVERSATIONAL CONTEXT — `recent_turns` + `claims_pool_snapshot` use
8. WHAT FOLLOWS — pointer to per-action body + soft-cap framing

Rendered from `PersonaSpec` at `SpeakerService.__init__` time via
`render_preamble()`. Result is deterministic + module-level → byte-identical
across calls of the same kind and across sessions in the same deployment.
Triggers OpenAI Responses-API auto-caching of the `instructions` field.

### `PersonaSpec` dataclass — new

Replaces the previous `DEFAULT_PERSONA` dict in `speaker/persona.py`.
Frozen dataclass; single source of truth for prompt content, canned
fallback strings, and observability flag detection (read by
`naturalness.py`). Tenant override remains scoped to `name` (via
`engine_agent_name`); other fields are locked in code.

Key fields:
- `name = "Arjun"`, `archetype = "Senior Engineering Manager at the hiring company"`
- `register` describes pronounced Indian English
- `opener_rotation` — 9 discourse markers (`See —`, `Right, so —`,
  `Mm, OK —`, `Let me put it this way —`, …)
- `vocab_banned` — short curated list (delve, leverage, streamline,
  robust, Great question, Certainly, Absolutely) + principle
- `fallback_recovery`, `fallback_empty_output{_no_bank}`,
  `fallback_session_ended` — Arjun-voiced strings
- `tts_voice_recommended = "shubh"` (bulbul:v3 male; locked P4.3)
- `tts_pace_recommended = 0.95`, `tts_temperature_recommended = 0.6`
- `speaker_llm_temperature = 0.7` (was implicit 1.0)

### Per-action bodies (9 files) — all rewritten in Arjun's voice

Each follows: TASK / ARJUN'S SHAPE / EXAMPLES / REMINDER structure
with heavy few-shot exemplars (3-6 per file). Pronounced Indian English
register throughout — "See —", "Kindly walk me through", "Let us stay
with", "What is the first thing".

- `deliver_first_question.txt` — 3 exemplars (procedural/scenario/open-ended)
- `deliver_question.txt` — 4 exemplars incl. `is_post_cap_advance` segue
- `deliver_probe.txt` — 3 exemplars; default no recap
- `clarify.txt` — 4 exemplars; three explicit paths (A: specific term,
  B: generic confusion, C: probe-context); ANTI-PATTERN block
- `push_back.txt` — 4 exemplars, one per `push_back_reason_code`;
  ANTI-PATTERN for stacked asks
- `redirect.txt` — 6 scenario exemplars (salary, hint-fishing, logistics,
  social, injection, abusive) + generalization principle for novel
  off-topic patterns
- `acknowledge_no_experience.txt` — 2 exemplars (more-mandatory / last)
- `polite_close.txt` — 4 exemplars; two branches (clean / knockout);
  ANTI-PATTERN forbidding duplication of prior-turn acknowledgment
- `repeat.txt` — empty-bank fallback line in Arjun voice

### Code paths

- `speaker/input_builder.py` — `recent_reply_starts` now symmetric across
  all kinds (was non-contextual-only). Contextual kinds fire most often
  in a session; phrase variation is the highest-frequency 'sounds AI'
  tell per LiveKit/Vapi/OpenAI research.
- `speaker/service.py` — preamble rendered once at `__init__`; explicit
  `temperature=DEFAULT_PERSONA.speaker_llm_temperature` (0.7) on the
  Responses-API call.
- `orchestrator.py` — three canned fallback paths
  (`_RECOVERY_TEXT`, `_compose_empty_output_fallback`,
  `_format_session_ended_message`) now read PersonaSpec strings. No
  failure path bypasses the persona.
- `app/config.py` — `engine_session_ended_message` default → `None`
  (orchestrator falls back to PersonaSpec); `interview_tts_pace`
  default → `0.95`.

### Observability — new `speaker/naturalness.py`

Four pure-function flag detectors that run after every successful
Speaker turn. Results attached to existing `SPEAKER_OUTPUT` audit event
as `naturalness_flags` (optional Pydantic model, backward-compatible).

- `detect_repeated_opener(output, recent_reply_starts) -> bool`
- `detect_banned_phrases(output) -> list[str]` (reads `PersonaSpec.vocab_banned`)
- `detect_name_overuse(output, candidate_name, prior_output) -> bool`
  (orchestrator tracks `_prior_speaker_output` instance state)
- `detect_exceeded_soft_target(output, instruction_kind) -> bool`
  (50%-over-target threshold; per-kind soft targets)

17 unit tests in `tests/interview_engine/speaker/test_naturalness.py`.
Operator query: `scripts/grep_naturalness.sh <session_uuid>` surfaces
flagged turns from any session envelope.

### TTS — Sarvam bulbul:v3 alignment (P4.3)

- Voice: `shubh` (existing default; locked as Arjun's voice). The v2
  voices (manoj/arvind/abhilash) referenced in the spec do NOT exist
  in v3 — discovered during the P4.1 plugin investigation. P4.1
  `scripts/speak_one_off.py` exposes the actual v3 voice list
  (`shubh`, `rahul`, `amit`, `aditya`) for future A/B if voice
  re-selection becomes useful.
- Pace: `0.95` (slightly slower than 1.0 default — matches Arjun's
  measured cadence).
- Temperature: `0.6` (unchanged).
- No SSML support in Sarvam — pause encoding is punctuation-only
  (em-dash `—`, comma, ellipsis `…`).

### Verifications still pending (user-driven)

- **P1.5 / P2.10 / P3.6** — listen to a real candidate-mock session;
  verify Arjun's voice across all turn kinds.
- **P5.1** — verify `cached_tokens > 0` shows up in audit envelope on
  the second same-kind call.
- **P5.3** — verify Sarvam pronounces iPaaS / ERP / API / SQL /
  JSON / CDN correctly; if not, add deterministic preprocessor
  (P5.4 conditional).

### Tooling

- `scripts/speak_one_off.py` — standalone Speaker + Sarvam TTS A/B
  bench. Accepts `--utterance` (literal) or `--speaker-input <json>`,
  iterates over `--voices`, plays via `aplay`/`afplay`.
- `scripts/grep_naturalness.sh` — `jq` query wrapping the audit
  envelope for flagged-turn surfacing.

### Files touched

13 modified + 4 new (~2400 net insertions per `git diff --stat
worktree-speaker-realism..origin/main`). All test suites green except
the 3 pre-existing replay tests that require a local envelope file
not present in fresh checkouts.
