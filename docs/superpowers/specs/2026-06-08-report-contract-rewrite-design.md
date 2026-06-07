# Report Contract Rewrite — Design (provenance-aware, bank-anchored scoring)

> **Status:** design approved (brainstorm), ready for an implementation plan.
> **Date:** 2026-06-08
> **Module:** `app/modules/reporting/` (rewrite) + one wiring change in `app/modules/interview_runtime/`.
> **Depends on:** the gen-3 interview engine (merged to local `main`, commit `1cb09673`) and its
> append-only evidence contract `app/modules/interview_runtime/evidence.py` (`SessionEvidence`,
> migration `0054_session_evidence`).

---

## 1. Why

Two problems, one rewrite:

1. **The pipeline is disconnected.** The gen-3 engine's `finalize` calls
   `record_session_evidence()`, which writes `sessions.session_evidence_json` **only** and
   **does not enqueue report scoring** (see `interview_runtime/service.py:525`). The report
   module still reads the gen-2 `coverage_summary` from `raw_result_json`. So a gen-3 interview
   today produces rich evidence that **nothing scores**. The report must be rewritten to consume
   `SessionEvidence`, and the scoring enqueue must be re-connected.

2. **The original unfairness.** The gen-2 scorer computes `coverage = assessed_weight /
   total_weight` over the **role's full signal set** (`scoring/aggregate.py`). A role with 24
   signals whose bank only covers 8 → coverage ≈ 0.33 → trips `BORDERLINE_CEILING`, capping
   *even excellent candidates* at borderline. The product principle: **the question bank is the
   standardization template; a candidate should be graded against what the bank actually assesses,
   not against role signals no question ever covered.**

The fix: make the **bank's primary signals the scoring denominator**, consume the engine's
append-only evidence, and use `provenance` to score fairly and defensibly.

---

## 2. Goals & non-goals

**Goals**
- Re-connect the post-session pipeline for gen-3 sessions (evidence → enqueue → report).
- Rewrite the report's scoring core around `SessionEvidence` (notes + provenance + closure).
- Make scoring **fair** (bank-anchored denominator) and **standardized** (session-invariant
  denominator, ungameable) and **defensible** (provenance-labelled, audit-reproducible core).
- Keep the recently-built verdict-driven structure (four dimensions → tier → verdict, fit
  ceilings, ±5 holistic, communication, narrative) and the layered-AI architecture — re-pointed
  at the new evidence.

**Non-goals (explicitly deferred)**
- The two-tier (core/coverage) **question-bank refactor** — deferred. The bank stays as-is; the
  report adapts. (The `QuestionTier` field already exists in the contract; the engine emits
  `core` for the flat bank today.)
- **Timing-based integrity** (flatline / think-time / possible external-assist from
  `pre_turn_gap_ms` + word timing) — carried in the contract, **unused** by this rewrite. It
  overlaps the proctoring/vision plane and is its own feature.
- No backward compatibility with the gen-2 result contract (gen-3 is the only engine).

---

## 3. Decisions (the load-bearing calls from the brainstorm)

1. **Deeper rewrite** of the scoring core — built around the append-only notes, not an adapter
   shim over the old `coverage_summary`.
2. **Denominator = primary signals, hybrid model.**
   - **Primary signals** (any signal that is the `primary_signal` of ≥1 bank question) form the
     fixed, session-invariant **graded denominator** — they can help *or* hurt.
   - **Secondary-only signals** (in some question's `signal_values` but never a `primary_signal`)
     are **upside-only**: cross-credited when demonstrated (lift the picture), silently excluded
     when absent (never a penalty). Rationale: a secondary-only signal can *never* be
     `probed_absent` (it has no own question), so counting its absence would re-introduce the
     original "graded on what we never asked" unfairness.
3. **"Not demonstrated" = uniform low band.** `probed_absent` (asked & failed) and `not_reached`
   (never asked / time-truncated) score at the **same floor** — closes the stall-to-dodge gaming
   vector and maximizes standardization. The report still **labels** which it was (honesty/audit).
4. **Must-have (knockout) verdict gate** (provenance-aware):
   - `knockout_close` **or** `probed_absent` must-have → **reject**.
   - `not_reached` / `thin` must-have → **Borderline (human-held)** — never auto-reject (EEOC),
     never auto-advance (unverified must-have).
   - `solid` / `strong` must-have → passes the gate.
5. **Keep the layered AI architecture**: deterministic core + AI re-check (can re-grade evidenced
   signals) + ±5 holistic + communication judge + narrative — all re-pointed at the evidence.

---

## 3.1 Cross-check against the engine (verified 2026-06-08)

Read against the real producer code (`interview_engine/driver.py::finalize`,
`interview_engine/notes.py::compute_provenance`, `brain/resolver.py::build_question_records`):

**Confirmed — the spec's assumptions hold:**
- Provenance is computed exactly per §7.1 (`notes.py::compute_provenance`):
  `asked_directly` / `cross_credited` / `probed_absent` / `not_reached`. `own_questions(S)` =
  questions where `primary_signal == S`; `asked_fairly` excludes `closure == truncated`.
- `EvidenceNote` carries `stance`, `texture`, `quote` (full utterance), `span`, `from_question_id`,
  `via_probe`, `retracts_seq`. A retraction links the prior same-signal note; both kept.
- `QuestionRecord` carries `primary_signal`, `tier`, `outcome`, `closure`, `probes_used`,
  `probes_available`. `closure` is best-effort (`satisfied`/`absent`/`tapped_out`; `truncated` for
  the question still on the floor at finalize). `tier` is hardcoded `"core"` today (flat bank).
- `KnockoutOutcome` is emitted only when the brain verified a must-have absent; when that ends the
  screen, `meta.completion` flips to `knockout_close`.
- `record_session_evidence` does **not** enqueue scoring (confirmed). The actor already loads the
  bank `StageQuestion` rows (rubric + `signal_values`) and `signal_metadata` — both still needed.

**Correction this forces into the design (important — guards the core bug):**
- **`SessionEvidence.signals[]` is the FULL role signal set**, not the bank-covered set. `finalize`
  builds one `SignalEvidence` per `config.signal_metadata` entry (uncovered role signals get
  `provenance = not_reached`). So the **graded primary-signal denominator MUST be derived by
  filtering to `{ qr.primary_signal for qr in evidence.questions }`** — *not* by scoring all of
  `signals[]`. Scoring `signals[]` wholesale would re-introduce the exact "graded against the
  role's full set" bug we're fixing. `primary_signal` is always populated (the engine falls back to
  `signal_values[0]`), so the set is reliable.
- **Demonstrated secondary signals** (the upside-only path) are then exactly:
  `signal ∉ primary_set AND provenance == cross_credited` in `signals[]` (a secondary-only signal
  has no own question, so it can only ever be `cross_credited` or `not_reached`).
- **Knockout** is read from `evidence.meta.completion == knockout_close` + `evidence.knockout`
  (a `KnockoutOutcome`), not an audit-envelope event — `detect_knockout_close` is gone.
- **Transcript** is `list[TranscriptTurn]` with a `speaker` enum (`agent`/`candidate`), not dicts
  keyed `"role"`. The communication / holistic / narrative inputs adapt to it
  (`candidate_text = "\n".join(t.text for t in transcript if t.speaker == candidate)`).

---

## 4. Architecture — data flow & module layout

**Trigger (re-connect the pipeline).** At the end of `record_session_evidence`, after it durably
commits the evidence, enqueue `score_session_report.send(session_id, tenant_id, correlation_id)` —
gated by the existing `AUTO_SCORE_SESSION_REPORTS` dev flag, and only on a **fresh** write (never
on the idempotent no-op return). This is the only change outside `reporting/`.

**Actor** (`reporting/actors.py::score_session_report`) — bypass-RLS, tenant-filtered:
1. load the session row, parse `session_evidence_json` → a `SessionEvidence` object;
2. load the **bank questions** for the stage (needs each question's `rubric` + `signal_values` —
   those live in the bank, not the evidence) and the role's signal metadata;
3. call `build_report(...)` → `persist_report(...)`.

**Module map:**

| File | Role after rewrite |
|---|---|
| `scoring/evidence_adapter.py` **(new — replaces `engine_signals.py`)** | Parse `SessionEvidence`; expose `notes_by_signal`, `provenance_by_signal`, `question_records`, `knockout` (from `meta.completion` + `evidence.knockout`), `transcript` (speaker-enum). Derive the **primary-signal set** = `{qr.primary_signal for qr in questions}` and filter the full-role `signals[]` to it (see §3.1 — guards the core bug); expose demonstrated secondary signals (`∉ primary_set ∧ cross_credited`). |
| `scoring/aggregate.py` **(rewritten)** | Deterministic core: per-signal rollup → dimensions → coverage/confidence → ceilings → verdict (§5–§6). |
| `recheck.py` / `holistic.py` / `judge.py` / `narrative.py` **(kept, re-pointed)** | The four AI layers, fed by notes + quotes + provenance (§7). |
| `service.py::build_report` **(new signature)** | `SessionEvidence` + bank questions + signal metadata + correlation_id (no `coverage_summary`/`envelope`). |
| `service.py::persist_report` | Unchanged except `engine_version` `"v2"` → `"v3"`. |
| **Deleted** | `engine_signals.py`; `service.py::_triage_kind_by_question`; all `raw_result_json` / `coverage_summary` reads. |

**Pipeline order (unchanged):** deterministic core → re-check (may adjust evidenced grades) →
re-aggregate dimensions/overall → ceiling → holistic → communication → verdict → narrative →
persist.

---

## 5. Per-signal scoring (the heart)

For each **primary signal**, roll its notes into one *demonstration level*, then to a score.
Provenance + closure disambiguate the no-support cases.

**Step 1 — notes → demonstration level**

| Level | Derivation (from notes + provenance/closure) |
|---|---|
| `strong` | supporting notes present; best `texture == strong` |
| `solid` | supporting notes present; best `texture == concrete` |
| `thin` | supporting notes present; only `texture == thin` (generic / hypothetical, no real *how*) |
| `absent` | `provenance == probed_absent`, **or** an un-retracted `contradicts` note (disclaim / "never used X") |
| `not_reached` | `provenance == not_reached`, **or** asked with `closure == truncated` (cut by the clock — no fair data) |

**Step 2 — level → score (0–100)** (illustrative; tunable by feel — the *ordering* is load-bearing):

| Level | Score |
|---|---|
| `strong` | 100 |
| `solid` | 80 |
| `thin` | 40 |
| `absent` | 10 |
| `not_reached` | 10 — **same floor as `absent`** (uniform low band; still labelled distinctly) |

The `thin` rung carries **bluff-awareness**: a confident-but-generic answer earns partial credit,
not full. The AI re-check (§7) may then move an *evidenced* signal up/down, rubric-bounded.

**Step 3 — the hybrid denominator**
- **Primary signals** → all enter the weighted mean (by JD weight), including floored
  `absent`/`not_reached`. Fixed, session-invariant. Can help or hurt.
- **Secondary-only signals** → kept *out* of the core mean. A demonstrated one (cross-credited at
  `solid`+) feeds two **upside-only** places: the recruiter-facing **coverage/breadth view** and
  the **holistic ±5 nudge**. Pure upside, never a penalty.
  *(Alternative considered: fold demonstrated secondaries into the mean at reduced weight.
  Rejected to keep the denominator clean and provably upside-only.)*

**Disclaims & retractions:** an un-retracted `contradicts` note makes the signal read `absent`
(the candidate said no, or corrected an earlier yes). Both notes are kept for the narrative; the
deterministic read follows the final stance.

---

## 6. Dimensions, coverage/confidence, ceilings & verdict

**Four dimensions** (unchanged surface): **Technical** (`competency`/`experience`/`credential`
primaries), **Behavioral** (`behavioral` primaries), **Communication** (separate, §7), **Overall**
(weighted mean of all primaries; communication excluded).

**Split "the number" from "how sure we are":**
- **Score (0–100)** uses the **bank primary signals** as the denominator (including floors). This
  kills the original bug — no division by the role's full signal set.
- **Confidence/coverage** is a *separate* measure = (weight of primaries with **real data**) ÷
  (total primary weight), where real data = demonstrated **or** `probed_absent`. `not_reached` /
  `truncated` score at the floor but **do not count as covered** → a sparse screen reads as **low
  confidence** even though every primary got a number. This is what lets "uniform low band"
  coexist with honesty.

**Fit ceilings (the must-have gate as caps):**

| Must-have situation | Cap |
|---|---|
| `knockout_close`, **or** any must-have `probed_absent` | **Reject ceiling** |
| Any must-have `not_reached` / `thin`, **or** overall confidence below the minimum | **Borderline ceiling** |
| All must-haves `solid` / `strong` | No cap |

**Edge case — a secondary-only must-have.** If a signal is flagged `knockout=True` but is *never*
any bank question's `primary_signal` (it appears only in `signal_values`), it has no dedicated
question to verify it, so the engine can never `probe_absent` or `knockout_close` it. The must-have
gate therefore applies only to must-haves **in the primary set**; a secondary-only must-have is a
**bank-design gap** — surfaced to the recruiter as an unassessed must-have (a coverage note), never
silently failed and never auto-rejected.

**Verdict** (score already ceiling-capped; categorical rules are backstops):
- `knockout_close` or a `probed_absent` must-have → **reject**;
- else score ≥ advance-threshold → **advance**; below reject-threshold → **reject**; otherwise (or
  capped at the borderline ceiling) → **borderline**;
- **Borderline is always human-held** (product invariant).

**Holistic ±5** stays: applied *after* the ceiling and re-capped so it can never break a
categorical guarantee. Demonstrated secondary-signal breadth feeds it (the upside path).

---

## 7. The four AI layers (kept, re-pointed)

| Layer | Change for gen-3 |
|---|---|
| **Signal re-check** (`recheck.py`) | Reads the engine's **notes directly** (verbatim quotes + texture + stance) vs the question rubric — "lighter re-check" (verify, don't re-derive). May refine an evidenced signal's level (e.g. `solid`→`strong`, or confirm a `thin` answer is a genuine **bluff**). **Scope:** signals with evidence (`asked_directly`/`cross_credited`) + `probed_absent` (confirm the negative). **Skips** `not_reached`. **Trusts the engine** on factual/binary gates (`compliance_binary`, etc.) — already judged live. |
| **Holistic ±5** (`holistic.py`) | Also fed demonstrated **secondary-signal breadth** (upside-only). Stays bounded ±5, re-capped. |
| **Communication** (`judge.py`) | Stays an LLM transcript judge → `weak`/`adequate`/`strong`. Timing cues deferred (§2). |
| **Narrative** (`narrative.py`) | Ground-truth JSON now carries **provenance per signal** → prose can honestly distinguish *"not assessed — ran out of time"* from *"candidate lacked X."* Sees final numbers; cannot change them. |

**Guardrails preserved:** re-check only moves evidenced signals and stays rubric-bounded; holistic
is bounded ±5 + re-capped; narrative cannot alter numbers. All adjustments logged in
`scoring_manifest` (overrides, deltas, ceilings) → same evidence ⇒ same number.

Prompts get a version bump (e.g. `prompts/v4/report_scorer/`): re-check reads notes; narrative
gets provenance.

---

## 8. Deletions, testing, follow-ups

**Deleted (no backward-compat):** `scoring/engine_signals.py` (coverage_summary projection,
envelope `detect_knockout_close`, `collect_signal_evidence`); `service.py::_triage_kind_by_question`;
all `raw_result_json` / `coverage_summary` reads.

**Re-wired:** report-scoring enqueue moves `record_session_result` → `record_session_evidence`.

**Verify-then-remove follow-up (out of this module):** with the enqueue moved,
`record_session_result` + its `raw_result_json` write are orphaned for gen-3 — flag for removal
after confirming no other callers (it lives in `interview_runtime`, so not deleted blind here).

**Testing (TDD; "human-review-before-merge" area per CLAUDE.md):**
- **Deterministic core** — exhaustive table-driven unit tests: every provenance × closure ×
  texture → level → score; hybrid denominator (primary counts; secondary upside-only never
  penalizes); must-have gate cells; `absent == not_reached` floor equivalence; "borderline always
  human-held."
- **AI layers** — mock the LLM at the existing seam: re-check overrides *only* evidenced signals
  and stays rubric-bounded; holistic within ±5 + re-cap; narrative can't change numbers.
- **Golden fixture** — a representative `SessionEvidence` JSON → expected `ReportRead` snapshot
  (end-to-end regression guard).
- High branch coverage on `aggregate.py` + `evidence_adapter.py`.

---

## 9. Source artifacts / references

- Engine→report contract: `app/modules/interview_runtime/evidence.py` (`SessionEvidence` and parts).
- Engine evidence persistence: `interview_runtime/service.py::record_session_evidence` (migration
  `0054_session_evidence`).
- Current (gen-2) scorer being rewritten: `app/modules/reporting/` (`service.py`,
  `scoring/aggregate.py`, `scoring/engine_signals.py`, `scoring/types.py`, …).
- Engine design it consumes: `docs/superpowers/specs/2026-06-05-interview-engine-gen3-design.md`
  (§7 evidence contract, §7.1 provenance computation, §7.2 report-side changes `[OPEN]` — this
  spec resolves that `[OPEN]`).
- Prior verdict-driven scorer design (structure preserved):
  `docs/superpowers/specs/2026-05-28-verdict-driven-fit-score-design.md`.
