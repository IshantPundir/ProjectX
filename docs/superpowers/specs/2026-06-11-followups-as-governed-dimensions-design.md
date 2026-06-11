# Follow-ups as Governed Dimensions — Deterministic Probing + Better Banks

**Date:** 2026-06-11
**Status:** Approved design — ready for implementation plan
**Scope:** One spec, phased: **Part 2 (engine runtime) implemented first**, then **Part 3 (bank generation)**.
**Branch:** `feat/followups-governed-dimensions`

---

## 1. Problem & Motivation

The Question Bank is a **standardized template**: 100s of candidates for one JD must be screened
against the *same* calibrated questions, the *same* rubric anchors, and the *same* probe dimensions —
so the `reporting` module can score them on one ruler. Comparable inputs → comparable reports.

A QA talk-test (session `RM_8oXPvEhZpvxo`, "Test - EMM Engineer", bank
`7b67e66a-ddb8-475a-ba88-2915a3db6f4e`) showed the engine **repeating questions** and **grinding the
same probe dimension** over and over. Forensic analysis (transcript + bank dump) found a **dual root
cause**:

### Finding A — the bank itself repeats one dimension across questions
"Safe / staged rollout" was authored as a follow-up on **4 of 8** questions:

| Q | Follow-up encoding "safe staged rollout" |
|---|---|
| Q3 (messy tenant) | `[1]` "validate impact before adjusting a policy" · `[2]` "test and **stage the fix safely**" |
| Q4 (iOS Wi-Fi) | `[2]` "**roll back or hotfix** without disrupting users" |
| Q5 (Android apps) | `[2]` "**deploy a safe fix** across fleets" |
| Q6 (Conditional Access) | `[1]` "**stage and monitor the rollout**" |

The generator writes each question independently with **no cross-question dedup**, so for an Intune ops
role it lands on "roll it out safely" four times.

### Finding B — the engine then *grinds* each dimension
Mapping the transcript to the bank, the agent re-fired the **same** follow-up dimension multiple times
in a single thread:

| Thread | Bank offers | What actually happened |
|---|---|---|
| Q3 | 3 distinct FUs | `[08/12]` validate-impact → `[14]`+`[16]` **re-composed "test safely"** → `[18]` **re-posed the whole main Q** |
| Q5 | 3 distinct FUs | `[52]` "safe pilot rollout" → `[55]` **"test with a small group first"** (same dim again) |
| Q6 | 3 distinct FUs | `[61]` "logs to tell rollout is safe" → `[63]`+`[67]`+`[69]` **three restatements** |

Q6 alone got the same "what logs prove it's safe to widen" probe **four times** — exactly when the
candidate said *"I think I answered this question."* The candidate (Punar) was actually strong (8–9 yrs,
real war stories); the **agent** degraded a good interview.

Quantified: 112 agent turns, **48 interrupted (42%)**, 180 EOU fires for 56 user turns. The
fragmentation/interruption layer ran with the now-fixed `unlikely_threshold=0.011` and is being handled
separately (turn detection / turn assembly). **This spec targets the durable LLM/determinism layer**,
which persists regardless of turn detection.

### Why the existing guard failed
The brain **composes** probe text (`composed_say`) but the anti-repeat guard (`coerce_probe_index`)
tracks follow-up **indices**. Because composed text is decoupled from the index, the brain re-phrased a
fired dimension as a "new" probe and nothing deterministic blocked it. The index ledger and the spoken
content drifted apart.

### Design convictions (from CLAUDE.md + project memory)
- **The LLM proposes, deterministic code disposes.** Probe-dimension tracking currently has no disposer.
- **This is a screen, not a panel.** One probe verifies a thin claim; a third grinds. Reads Indian-English
  under-selling correctly; never re-asks what it already heard.
- **Collector, not judge.** The engine collects signals; scoring/verdict is the report's job.
- **Teach principles + why, not replayed examples** (prompt discipline).
- **No regex for intent** — every "is the candidate X?" stays a semantic judgment; the *disposers* are
  deterministic structural code, not pattern-matching on meaning.

---

## 2. Goals & Non-Goals

**Goals**
1. Treat each follow-up as a **governed sub-template** (a dimension with intent + listen-for), so the
   brain composes natural probes *within* a fixed intent instead of free-floating.
2. Make probing **deterministic**: each dimension fires at most once per thread; a hard cap force-advances;
   the thread closes crisply on `primary_signal`.
3. Eliminate **cross-question** dimension redundancy at generation time, with a runtime safety net.
4. Fix the floor/repeat/clarify re-ask bugs (E1–E3) that compounded the felt repetition.
5. Generate **higher-quality banks**: distinct follow-up dimensions, each with explicit intent + listen-for.

**Non-Goals**
- Turn detection / EOU / interruption tuning (handled separately).
- Report-module changes (it does not consume `follow_ups`).
- Frontend questions-page rendering of the richer shape (dependent change, scheduled separately).
- Generation-time dedup as a dedicated extra LLM pass (explicitly rejected — done inline; see §5.2).

---

## 3. Part 1 — The Shared Contract (load-bearing)

Promote `follow_ups: list[str]` to a list of **governed dimension objects**:

```python
class FollowUpDimension(BaseModel):
    dimension: str         # stable slug, e.g. "validate_impact" — the ledger key
    intent: str            # WHAT this probe verifies; the brain composes WITHIN this
    seed_probe: str        # pre-authored spoken seed (== today's follow-up string)
    listen_for: list[str]  # 2-4 observable specifics that SATISFY this dimension
```

One shape, two consumers:
- **Engine** uses `dimension` (ledger key) + `intent` (composition guardrail) + `listen_for`
  (the specifics the brain targets in a probe and judges satisfaction against).
- **Generator** produces it and dedups on `dimension`/`intent` across questions.

**Where it is defined (mirrors `QuestionRubric`).** `FollowUpDimension` is defined **independently in
both** `question_bank/schemas.py` (generation/persistence) and `interview_runtime/schemas.py` (the engine
wire contract) — exactly as `QuestionRubric` is today. These are two separate bounded contexts; the DB
`stage_questions.follow_ups` JSONB column is the contract between them. No cross-module import.

**One shape at runtime — a one-time data backfill, not a runtime shim.** Existing banks store
`follow_ups` as `list[str]`. A single **Alembic data migration** (no DDL — the column is already JSONB)
rewrites every row once: each string `s` becomes
`{dimension: <slug(s)>, intent: s, seed_probe: s, listen_for: []}`. This is a pure, lossless shape wrap
(recruiter-edited follow-ups are preserved verbatim as `seed_probe`); only `listen_for` is empty for
pre-existing banks, repopulated when a bank is regenerated. The migration ships a **rollback** that maps
the new shape back to `list[str]` (taking `seed_probe`). After the backfill, **all runtime and generator
code deals with exactly one shape** — no dual-shape branching anywhere, no compat path living forever.

---

## 4. Part 2 — Engine Runtime (implemented FIRST)

### 4.1 Probe bound to a declared dimension (contract change)
`contracts.py`:
- `BrainTurnOutput.probe_index: int | None` → **`probe_dimension: str | None`** — the dimension slug the
  probe serves. The brain composes `composed_say` *within that dimension's `intent`*, anchored to the
  candidate's actual words.
- `BankQuestionIndex.follow_ups: list[str]` → `list[FollowUpDimension]`.
- `ActiveQuestionRubric.follow_ups: list[str]` → `list[FollowUpDimension]` (carries `intent` + `listen_for`
  so the brain targets the probe at the right specifics and judges thread satisfaction — consistent with
  `positive_evidence`/`red_flags`/`evaluation_hint`, which the active-question rubric already gives the
  brain); rename `probes_used: list[int]` → **`fired_dimensions: list[str]`** (slugs already fired this thread).
- `BrainDecision.probe_index` → **`probe_dimension: str | None`** (carried for the driver's ledger).

The composed probe passes the existing no-leak scrub **plus** an in-scope check against the dimension
(structural substring/anchor check, not intent classification — consistent with "no regex for intent").

### 4.2 Per-thread dimension ledger (the deterministic disposer)
`driver.py` tracks `fired_dimensions: dict[str, set[str]]` (question_id → fired dimension slugs).
`brain/policy.py` gains **`coerce_probe_dimension`** (replaces `coerce_probe_index`). Pure code,
never raises:
- A dimension fires **at most once**. If the brain re-targets a fired dimension → coerce to the
  highest-value **unfired** dimension; if none remain → coerce the move to `ask` (advance).
- **Hard cap**: `probes_fired_this_thread >= CAP` (default **2**, env-driven
  `engine_probe_cap_per_thread`) → force `ask`, regardless of the brain's proposal.
- "Highest-value unfired" ordering: by the dimension's position in the bank list (authoring order =
  earliest/foundational first); the brain may instead advance when a dimension's signal already reads
  `sufficient` (§4.4).

This is the structural fix for Q6's four-times re-fire: the 2nd re-target of a fired dimension is
impossible.

### 4.3 Thread-closure on `primary_signal` (selective + early advance)
The brain picks the single highest-value **unfired** dimension and **advances early** (move=`ask`) the
moment `primary_signal` reads `sufficient` in the coverage projection. Result: ~1–2 probes/question
typical, never a march through all three. The deterministic advance triggers (any one):
- `primary_signal == sufficient` (brain may also choose `ask` semantically), OR
- `probes_fired >= CAP`, OR
- no unfired dimension remains.

Thread-closure inference (`driver._infer_closure`) continues to read the projection; unchanged in shape.

### 4.4 Cross-question runtime net (no new field)
The runtime cross-question net reuses what the brain **already** receives — it adds no fuzzy computed
field (no string-matching `listen_for` against the transcript, which would be both redundant and a soft
pattern-match on meaning). Three layers, in order of strength:

1. **Generation-dedup is the source fix** (§5.2) — distinct dimensions across questions mean the engine
   rarely faces a cross-question collision at all.
2. **The hard cap (§4.2) is the deterministic backstop** — total probing per thread is bounded regardless
   of any cross-question reasoning.
3. **The brain's existing per-signal coverage** (`evidence_so_far`, already in the suffix) is the semantic
   net: the prompt teaches that if the active question's follow-up dimension targets a signal already
   reading `sufficient`, the brain advances rather than re-probing it. This is the brain's judgment on data
   it already holds — bounded by the cap — not a new deterministic computation.

So the cross-question guarantee is *generation-side deterministic + a hard runtime cap*; the live "skip
the redundant probe" is semantic, on existing data. No new contract field.

### 4.5 Floor / repeat / clarify fixes (E1–E3)
- **E1 — clarify ≠ verbatim repeat.** A "simplify / I didn't understand" request must route to `clarify`
  (a genuinely simpler re-pose), never `repeat` (verbatim). Tighten the move boundary in
  `brain.system.txt` and the `mouth/clarify.txt` instruction (simpler words, same meaning, keep technical
  terms). `repeat` stays for explicit "say it again" with no confusion signal.
- **E2 — floor-pointer integrity.** `repeat`/`clarify`/`confirm` resolve only to the **current** floor
  line (`on_the_floor` / `_last_agent_line`). A drifted probe must never resolve back to a stale main
  question many turns later. Add a guard/assertion in `driver.py` floor tracking so the floor line is
  always the most recent question-bearing act (ask/probe/repeat), and document the invariant.
- **E3 — read-back ≠ re-ask.** A candidate reading the question back to confirm ("…correct?") is a
  confirmation, not a non-answer → acknowledge-and-wait (a brief affirming beat), do not re-pose. Taught
  in `brain.system.txt` (a confirmation read-back is not a `clarify`/`repeat` trigger).

### 4.6 Files touched (engine)
- `app/modules/interview_engine/contracts.py` — `FollowUpDimension`; `probe_dimension`;
  `fired_dimensions` (no new session-wide field — see §4.4).
- `app/modules/interview_engine/brain/input_builder.py` — suffix: render `follow_ups` as dimension objects
  + `fired_dimensions`; the cross-question net is prompt-driven over the existing `evidence_so_far`.
- `app/modules/interview_engine/brain/policy.py` — `coerce_probe_dimension` (replaces
  `coerce_probe_index`); cap enforcement.
- `app/modules/interview_engine/brain/service.py` — `_resolve_probe` (dimension-based);
  `_derive_directive` wiring.
- `app/modules/interview_engine/driver.py` — `fired_dimensions` ledger; floor-pointer integrity (E2);
  read-back handling support.
- `app/modules/interview_runtime/schemas.py` — define `FollowUpDimension` (mirrors `QuestionRubric`);
  `QuestionConfig.follow_ups` carries it. No runtime shape-branching (the §3 backfill guarantees one shape).
- `migrations/versions/` — one Alembic data migration: backfill `follow_ups` `list[str]` → object shape
  (with rollback). Per §3.
- `prompts/v4/engine/brain.system.txt` — dimension-aware probing, advance discipline, E1/E3 boundaries.
- `prompts/v4/engine/mouth/{probe,clarify,repeat}.txt` — compose-within-intent; clarify simplifies;
  repeat is verbatim-only.

### 4.7 Config
- `engine_probe_cap_per_thread: int = 2` (new, `AIConfig` / settings — env-driven, never hardcoded).

---

## 5. Part 3 — Bank Generation Quality (implemented SECOND)

### 5.1 New follow-up object shape in generation
`question_bank/schemas.py`:
- `GeneratedQuestion.follow_ups: list[str]` → **`list[FollowUpDimension]`** (the Part 1 shape, with the
  same 0–3 length bound). The generator now emits, per probe: `dimension`, `intent`, `seed_probe`,
  `listen_for`.
- `CreateQuestionBody` / `UpdateQuestionBody` / `QuestionResponse` `follow_ups` → the new shape.
- `refine.py` `RefineResponse` / `DraftResponse` flows emit the new shape.

This makes the bank a true sub-template and gives recruiters a richer review surface (each probe shows
*what it checks* + *what a good answer names*).

### 5.2 Cross-question dedup — INLINE in the existing calls (no second pass)
Generation is **one streamed LLM call per phase** (`behavioral` = experience_check/behavioral/
compliance_binary; `technical` = technical_scenario), and the technical call already receives the
behavioral questions via `prior_phase_questions`. The EMM collision was **entirely within the single
technical call** — that call already had full visibility; it was never told to dedup.

- **Within-phase:** strengthen the generation prompt with a hard rule — *"Every follow-up `dimension`
  across all questions in this bank must be distinct. Never author the same probe intent (e.g. 'roll out
  safely') on two questions; if a scenario shares an angle, deepen it differently per question."* The
  streamed call attends to its earlier list items autoregressively, so this is enforceable in one call.
- **Cross-phase:** the technical prompt already gets `prior_phase_questions`; extend it to surface their
  dimensions as *"already covered — do not repeat."*
- **Safety net:** the runtime net (§4.4 — hard cap + the brain advancing over an already-`sufficient`
  signal) still catches anything that slips through live.

**Tradeoff (accepted):** one call doing "generate great questions" *and* "globally dedup" is marginally
less bulletproof than a dedicated pass — but full within-phase visibility + threaded cross-phase context
+ the runtime net make it sound for zero extra latency/cost and a simpler flow. If post-implementation
talk-tests still show collisions, promoting it to a focused pass is a clean follow-up.

### 5.3 Prompt updates (`prompts/v2/`)
- `question_bank_common.txt` — teach the `FollowUpDimension` shape + the principle: *each follow-up is a
  distinct dimension with its own intent and listen-for; never restate the lead or another follow-up;
  every dimension across the bank is distinct.* (Principle + why, not a replayed example.)
- `question_bank_ai_screening.txt` / `question_bank_ai_screening_behavioral.txt` /
  `question_bank_phone_screen.txt` — emit the new shape; cross-phase "already covered" context.
- `question_refine_single.txt` / `question_create_single.txt` — emit the new shape for recruiter
  refine/draft.

### 5.4 Migration / regeneration
- **No DDL** (column is JSONB). The §3 **Alembic data backfill** rewrites every existing `follow_ups`
  row to the object shape once (with a rollback). After it runs, every bank — legacy or new — is the
  single shape; there is no compat branch.
- Pre-existing banks carry `listen_for: []` until regenerated; regeneration (or a recruiter refine/draft)
  repopulates it. Optionally regenerate the test EMM/Workato banks to exercise the full new structure.

### 5.5 Files touched (generator)
- `app/modules/question_bank/schemas.py` — `FollowUpDimension`; `GeneratedQuestion.follow_ups`;
  request/response shapes.
- `app/modules/question_bank/actors.py` — persistence of the new JSON shape; dedup wiring through
  `_build_user_message` / `prior_phase_questions`.
- `app/modules/question_bank/refine.py` — refine/draft emit the new shape.
- `app/modules/question_bank/models.py` — no column change; document the JSON shape in a docstring.
- `prompts/v2/*` — per §5.3.

---

## 6. Testing

**Engine (unit, livekit-free):**
- Dimension ledger: a dimension fires at most once; re-target → coerce to unfired/advance.
- Hard cap: `probes_fired >= 2` force-advances even when the brain says probe.
- Early advance: `primary_signal == sufficient` → `ask` before the cap.
- Cross-question hint: a session-wide-satisfied dimension is skipped → advance.
- E1: simplify request → `clarify` (genuinely simpler), never verbatim repeat.
- E2: floor pointer never resolves to a stale main question.
- E3: read-back/confirmation ≠ re-ask.
- Data backfill migration: `list[str]` → object shape (slug/intent/seed_probe/`listen_for=[]`) and its
  rollback (object → `list[str]` via `seed_probe`) round-trip on a fixture bank.

**Prompt-quality (opt-in `pytest -m prompt_quality`, real API):**
- Generated bank has **zero** cross-question dimension collisions.
- Follow-ups are distinct dimensions, each with non-empty `listen_for`.

**Generator (unit):**
- `FollowUpDimension` schema validation; new-shape JSON round-trips through persistence.
- Refine/draft emit the new shape.

**Manual talk-test (user loop):**
- Re-run an EMM-style screen; confirm no repeated/near-identical probes and willing early-advance on
  thin answers.

---

## 7. Out of Scope / Risks

- **Frontend** (`frontend/app` questions page) must render the richer follow-up objects — dependent
  change, scheduled separately; the API returns the new shape regardless.
- **Report module** unaffected (does not consume `follow_ups`).
- **Risk:** pre-existing banks carry `listen_for: []` until regenerated — accepted; the engine only needs
  `dimension`/`intent`/`seed_probe` to run, and regeneration repopulates `listen_for`.
- **Risk:** inline generation dedup is slightly less bulletproof than a dedicated pass — mitigated by the
  runtime cap + the brain's coverage awareness; promotable to a focused pass if talk-tests show residual
  collisions.
- **No DDL** — one Alembic data-migration (JSONB shape rewrite) with a rollback; no column change.

---

## 8. Rollout Order

1. **Part 2 (engine) + the §3 data backfill** — define `FollowUpDimension` in both modules, ship the
   Alembic backfill (every bank → object shape, single shape at runtime), then the ledger + cap +
   dimension binding + early-advance + E1–E3. Verified by unit tests + a manual talk-test.
2. **Part 3 (generator)** — emit the new shape natively + inline cross-question dedup + prompts. Verified
   by prompt-quality + a regen of the test banks + a manual talk-test.

The data backfill lands in Part 1, so both parts operate on a single shape — no dual-shape branching, and
Part 3 can land independently after Part 2.
