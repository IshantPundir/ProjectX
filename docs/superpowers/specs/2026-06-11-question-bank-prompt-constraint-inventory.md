# Question-Bank Prompt — Constraint Inventory (pre-rewrite safeguard)

**Date:** 2026-06-11
**Purpose:** Before rewriting `prompts/v2/question_bank_common.txt` + the unified `question_bank_ai_screening.txt` **from scratch**, capture every load-bearing rule currently encoded across the prompts so the clean rewrite keeps what matters and drops only the bloat. Each item is a MUST-KEEP unless explicitly marked.
**Source files audited:** `question_bank_common.txt`, `question_bank_ai_screening.txt` (technical phase), `question_bank_ai_screening_behavioral.txt` (behavioral phase — being retired), `question_bank_phone_screen.txt`, plus the schema (`schemas.py: GeneratedQuestion / FollowUpDimension / QuestionRubric`).

---

## A. Spoken-question constraints (the core job)

1. **Single-focus, ONE ask.** No "and… and…", no comma-spliced topic lists, no enumerated sub-parts inside `text`. The multi-part written-exam question is an automatic reject. Depth lives in `follow_ups`, asked one at a time.
2. **Length:** `text` ≤ 240 chars (schema-enforced; aim ~200 / one or two short sentences sayable in one breath).
3. **Numbers in words** ("five years", not "5 yrs") — TTS reads it aloud.
4. **Conversational register** — how a sharp human screener phrases it out loud, not a take-home prompt.
5. **NEW (raise the bar):** leads must be **self-contained / scenario-committed** — answerable on first hearing without the candidate asking "for what?". Ban bare comparative framings ("which would you pick / what's best?") that lack an inline scenario. (Principle + why, no replayed examples.)

## B. Signal binding

6. `primary_signal` is REQUIRED and MUST be one of the strings in this question's `signal_values` (schema + D5 invariant).
7. `signal_values` 1–3 items, each **copied VERBATIM** from the snapshot's signal `value` strings — no paraphrase/abbreviate/invent (validator rejects mismatches against the snapshot).
8. **One-of / "or" requirements** (e.g. "Java, Python, or Ruby"; "RDBMS or NoSQL"; "TIBCO, Dell Boomi, or MuleSoft"): the candidate satisfies with ANY ONE option. NEVER hard-code a single option in `text`, `follow_ups`, `rubric`, `positive_evidence`, or `red_flags`. Either ask option-agnostically ("whichever of X/Y/Z you're strongest in…") then probe within their choice, OR frame so it holds regardless. Rubric/evidence must credit ANY valid option.
   - Aside: a tool-SPECIFIC skill (e.g. Workato code step uses Ruby) binds to that tool's competency signal, NOT the generic one-of language knockout.

## C. question_kind taxonomy (Literal: experience_check | behavioral | technical_scenario | compliance_binary)

9. **experience_check** — verify a factual background CLAIM (years, platforms/tools used in production, scope, team, employer). Reports facts; NOT a STAR narrative, NOT a design probe.
10. **behavioral** — a true STAR question about ONE specific past event (Situation/Task/Action/Result). Probe for "I" not "we".
11. **technical_scenario** — verbal design/depth/think-aloud reasoning. NO coding, no shared editor — spoken reasoning only.
12. **compliance_binary** — hard yes/no gate on a self-disclosed eligibility fact; "no" is a knockout; answerable in seconds; one requirement per question (don't bundle).
13. In the unified single-call ai_screening prompt, ALL FOUR kinds are allowed in one pass (the old behavioral/technical phase partition is gone). The single call must self-balance them (see §F).

## D. difficulty (Literal: easy | medium | hard, per question)

14. `difficulty` = how hard it is to give a STRONG answer (cognitive depth demanded), NOT the signal's importance/weight/knockout status. A factual claim-check is `easy` even if it's a critical knockout.
15. Rule of thumb: experience_check / compliance_binary → usually `easy`; behavioral STAR → `easy`/`medium`; technical_scenario → `medium`/`hard` by depth. Calibrate to stage difficulty as a baseline, then per-question by answer-difficulty. Always set it.

## E. follow_ups — governed FollowUpDimension objects (0–3, ordered earliest→deepest)

16. Each follow-up has all four fields: `dimension` (stable lower_snake_case slug — the ledger key the live engine dedups on), `intent` (WHAT it verifies + why, not what to ask), `seed_probe` (a single-ask spoken question, same spoken constraints as `text`, ≤240 chars), `listen_for` (1–4 concrete observable specifics a strong answer NAMES — real tool names/steps/tradeoffs/numbers, never vague "clear explanation").
17. **Generation guarantee:** every LLM-authored follow-up MUST have a NON-EMPTY `listen_for` (enforced by a `field_validator` on `GeneratedQuestion`; instructor retries on violation). (The shared/read shape is permissive for backfilled rows, but generation must populate it.)
18. **Within-bank dimension distinctness:** every `dimension` slug across ALL questions in the bank must be distinct. The live engine fires each dimension at most once per session; duplicates re-ask the candidate + break cross-candidate report comparability.
19. **NEW (raise the bar):** distinctness must be **semantic**, not just slug-deep — two follow-ups may not probe the same underlying thing even with different slugs (e.g. avoid observability/idempotency restated across questions).
20. Each follow-up explores a DIFFERENT dimension of its own lead — never a restatement of `text` or a sibling follow-up.
21. 0 follow-ups allowed only for a pure compliance gate; 1–3 otherwise.

## F. Coverage / completeness / budget

22. **Every KNOCKOUT signal in scope has ≥1 question** covering it — an uncovered knockout is a defect.
23. **`is_mandatory` = knockout-ONLY.** Set `true` only on a question verifying a knockout signal; EVERY other question (including required non-knockout) is `false`. (A deterministic code reconcile, `_apply_mandatory_correction_in_position_order`, enforces this as a backstop — but the prompt must author it correctly.)
24. **Required before preferred.** Spend budget on `priority=required` signals first; only probe `priority=preferred` once required are covered. Never cover a lower-weight/preferred signal while a required signal of equal-or-higher weight is unprobed. When budget can't fit every required signal, prefer higher-weight + knockout.
25. **NEW (raise the bar):** weight-aware — never spend budget on a w1 signal while a w3 competency is unprobed (sharpens §24 with the QA-analysis finding).
26. Budget is GUIDANCE, not a hard cap. Optimize for signal density; under-using is fine, padding with filler is rejected. A runaway is bounded by `ai_config.question_bank_max_questions` (code-side).
27. Do NOT repeat/reword a question already generated (prior stages' questions are in the user message — cover different angles).
28. **NEW (single-call balance — replaces the old phase partition):** in ONE pass, author, in order: (1) one mandatory knockout-verification per knockout signal; (2) a true STAR `behavioral` for each behavioral-type required signal (MUST NOT be crowded out by technical scenarios); (3) `technical_scenario` depth probes for the highest-weight competencies; stop when high-weight signals are covered. Verification-before-depth ordering.

## G. Evaluator-only fields (NEVER spoken to the candidate)

29. `rubric` / `positive_evidence` / `red_flags` / `evaluation_hint` exist only for the post-session scorer. Write them to describe what a good/acceptable/weak **SPOKEN** answer SOUNDS LIKE — not a written essay.
30. `positive_evidence` 3–5 items: concrete observables to LISTEN FOR (names a specific tool, states a real scope number, walks a hypothesis→verify loop, owns "I did X"). Never "answers well"/"communicates clearly".
31. `red_flags` 2–3 items: specific failure modes you'd HEAR ("says 'we' never 'I'", "names a tutorial as production experience", "buzzwords with no scope/numbers"). Not generic "vague".
32. `rubric` three bands, each ≥20 chars (schema): `excellent` (strong spoken answer), `meets_bar` (acceptable, clears the bar), `below_bar` (weak/evasive). Describe spoken content.
33. `evaluation_hint` (10–200 chars): one line on what the question really tests + how to read the answer.

## H. Output discipline

34. Return only the structured object the schema requires — no preamble, no commentary, no markdown fences.
35. The bank is purely declarative ("what we plan to test") — no runtime narrative / coverage notes / per-session summaries (those are the engine/report's job).

## I. DROP in the rewrite (bloat / obsolete — do NOT carry forward)

- The **per-question 7-point self-critique pass** (the `(a)…(g)` checklist) — replace with the §F single-call authoring recipe; it's a reasoning-token tax that a prescriptive recipe + the schema validators make unnecessary, especially at low effort.
- All **two-phase / behavioral-phase / technical-phase / "ALREADY-GENERATED BEHAVIORAL — DO NOT OVERLAP" / chaining** language — there is one call now.
- All **`engine-v2 M2` / `decision D2/D3/D6/D7`** historical markers.
- Replayed-conversation **examples** used to patch a single incident (keep the few-shot GOOD/BAD *shape* contrasts only if they teach a principle concisely; lean toward principles + why per [[feedback_prompt_principles_not_examples]]).

## J. Mechanical contract the rewrite must not break

- `question_bank_common.txt` + `question_bank_ai_screening.txt` are loaded via `load_pair(...)` and used **verbatim as the system prompt** (no `.replace()` placeholders) — do NOT introduce `{placeholder}` tokens in these two.
- `question_refine_single.txt` / `question_create_single.txt` ARE `.replace()`-templated — every existing `{placeholder}` token (`{signals_json}`, `{stage_name}`, `{stage_type}`, `{stage_difficulty}`, `{stage_duration_minutes}`, `{signal_filter_types}`, `{pass_criteria_json}`, `{existing_bank_json}`, `{prior_banks_json}`, `{question_text}`, `{question_signal_probed}`, `{question_mandatory}`, `{instruction}`) MUST be preserved exactly (cross-check against `refine.py`).
- `question_bank_regenerate_one.txt` is loaded via `load_pair("question_bank_common", "question_bank_regenerate_one")` — no placeholders; rich context is in the user message.
