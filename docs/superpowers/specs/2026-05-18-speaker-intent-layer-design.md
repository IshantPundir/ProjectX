# Speaker intent layer — clarify_kind discriminator + deterministic opener pruning

**Status:** Draft for user review
**Date:** 2026-05-18
**Author:** Ishant Pundir (with Claude)
**Scope:** `backend/nexus/app/modules/interview_engine/models/judge.py`, `backend/nexus/app/modules/interview_engine/models/speaker.py`, `backend/nexus/app/modules/interview_engine/speaker/` (input_builder + naturalness + new openers helper), `backend/nexus/app/modules/interview_engine/state/` (push_back cap propagation), `backend/nexus/prompts/v2/engine/judge.system.txt`, `backend/nexus/prompts/v2/engine/speaker/clarify.txt`, `backend/nexus/prompts/v2/engine/speaker/_preamble.txt`.
**Predecessors:**
- `2026-05-17-speaker-realism-design.md` (Arjun persona + per-action prompts + naturalness flags)
- `2026-05-08-interview-engine-judge-speaker-redesign-design.md` (Judge↔Speaker boundary, anti-leak architecture)
- `2026-05-12-engine-simplification-design.md` (orchestrator simplification; State Engine inverse-quality-gate and validation paths)

---

## 1. Problem statement

Diagnostic of session `a13ec188-ebf4-4b5e-95fa-912555a556a7` (2026-05-18, 14 turns, ~10 min) surfaces five issues. Issues 1, 2, and 3 share a single architectural root cause; issues 4 and 5 are independent.

### Issue 1 — Judge misclassifies conceptual asks as evasion

**Turn 8** — candidate utterance (verbatim from `speaker.input.last_candidate_utterance`):
> "Why would there be duplicates for the same order ID? I mean, every order created in the Salesforce has their own unique order ID. Why would I mean, help me understand why would the same order ID be created twice?"

**Judge `reasoning` field (verbatim from `judge.call.output`):**
> "Candidate is pushing back on the duplicate-handling follow-up by asking why the same order ID would ever repeat, rather than giving a concrete design answer. The utterance stays on-topic and touches the reliability/idempotency concern, but it is **evasive and still lacks the requested specifics**."

**Judge `next_action`: `advance`** — but only because `push_back_count` had hit the cap. The Judge's first-choice classification was push_back. The cap-saturation escape masked the misclassification.

**Turn 12** — same shape, no cap save:
> "That's what I need to understand, how is it that ad impotency is a concern here because we are taking unique order IDs as a trigger for the recipes. So Uh, it's very hard for me to understand how a unique order ID being used as a trigger would lead to a situation where idempotency has to be enforced."

**Judge `reasoning`:**
> "Action: push back, because the answer is on-topic but **still missing specifics and is framed as a challenge to the premise rather than a methodical root-cause walk-through**."

Source: `prompts/v2/engine/judge.system.txt:36-42` defines CLARIFY as two sub-cases (specific term, generic confusion). No decision-tree branch exists for "candidate engaged with the topic but asks WHY a concept matters." Utterances of this shape fall through to PUSH_BACK §1.5 via "on-topic but THIN / EVASIVE / PARTIAL."

### Issue 2 — Clarify Speaker rephrases instead of bringing context

**Turn 2** — candidate: *"Okay I couldn't understand the question. Can you?"* (clarify fired correctly)
**Bank text:** *"Using your primary iPaaS (MuleSoft, TIBCO, or Boomi), how would you implement a near-real-time order sync from Salesforce to an on-prem E-R-P? Walk through the flow end-to-end…"*
**Speaker output:** *"Mm, OK — let's reframe it simply. Imagine an order is created in Salesforce, and you want it to appear in an on-prem E-R-P within minutes. Using your i-Paa-S, how would you design that end-to-end sync flow?"*

A tighter restatement of the same sentence. No domain, no volume, no contrast.

**Turn 5** — candidate explicitly asked for business context: *"What is the use case that Salesforce is being used in the Organization That helps me to set up the trigger…"*
**Speaker output:** *"Mm, OK — imagine Salesforce creates an order, and you need it to land in the on-prem E-R-P within minutes. What is your chosen Salesforce event, and how does your i-Paa-S subscribe to it?"*

The candidate explicitly asked for the use case; the Speaker ignored the ask and emitted another rephrase. Source: `prompts/v2/engine/speaker/clarify.txt:75-90` PATH B exemplars are themselves rephrases. The model is faithfully copying the demonstrated pattern.

### Issue 3 — No clarify path for concept-explanation

`clarify.txt:17-34` exhaustively defines three paths (term / generic / probe). None handle "engaged with the topic but asking for the failure mode of a concept." The anti-leak boundary for such a path is workable: explaining *why retries cause duplicates* is general engineering knowledge; naming the rubric anchor (*"idempotency keys/conditional upsert"* — anchor[3] from T12's `active_question_positive_evidence`) is a leak. The distinction is encodable.

### Issue 4 — Opener over-use, model ignores forceful prompt rule

Five `repeated_opener` flag triggers across 14 turns. Most damning: T2-T5 emitted "Mm, OK —" four turns in a row. T5's `recent_reply_starts` payload contained all three priors (`['Mm, OK — let's', 'Mm, OK — kindly', 'Mm, OK — that's']`); the gpt-5.4-nano model emitted "Mm, OK — imagine" anyway. `_preamble.txt:67-70` carries the rule with "NEVER" — model is not honoring it.

Detector logic in `naturalness.py:12-35` works correctly. `recent_reply_starts` propagation in `input_builder.py:197` is symmetric across all kinds. The signal is present; the model ignores it.

### Issue 5 — Sarvam STT mangles technical proper nouns (OUT OF SCOPE)

Workato → "avocado" / "Vokarto" / "Vokato"; idempotency → "ad impotency" (inconsistent within a single turn); callable → "collable"; RAG → "rack" after first correct mention. Out of scope for this rewrite; separate workstream.

### Two secondary observations

- **`is_post_cap_advance` doesn't fire on Judge-voluntary advances at cap.** Audit shows False on every turn including T8 (which was a post-cap advance from Q1 to Q2). The Judge picked advance directly because it knew the cap was hit; the State Engine never ran a downgrade, so the flag stayed False. The Speaker emitted "Thanks for that…" — model-discovered, not signal-driven.
- **Cache hit rate 1/14.** `prompt_cache_key="speaker:v2"` is shipped but mostly missing. Three hypotheses (preamble under 1024 tokens, per-kind tails concatenated into one system message breaking prefix stability, whitespace drift). Diagnostic — out of scope for this spec, filed as a follow-up.

---

## 2. Goals & non-goals

### Goals

1. **Intent in the data model.** Introduce `ClarifyKind` discriminator on the Judge→Speaker boundary. The Judge classifies; the State Engine routes; the Speaker dispatches. Intent becomes explicit data, not a Speaker heuristic.
2. **Five clarify kinds** covering the observed conversational space:
   - `term_definition` — candidate asked about a specific term.
   - `concept_explanation` — candidate engaged with topic, asks WHY a concept matters / for the failure mode.
   - `use_case_anchor` — candidate asks for business or operational setting (including bare "give me an example").
   - `broad_rephrase` — generic confusion with no specific ask; now context-bringing.
   - `probe_context` — confused after a probe.
3. **Speaker exemplars demonstrate context-bringing, not rephrasing.** PATH B (broad_rephrase) gains domain + volume + contrast in every exemplar. PATH D (use_case_anchor) gives a concrete business setting. PATH E (concept_explanation) describes ONE failure scenario and returns the question without proposing the fix.
4. **Anti-leak boundary load-bearing on PATH E.** "Expose the gap, never fill it." Explicit forbidden-verb list ("use", "implement", "add a", "store a", "key on", "track", "deduplicate by") as a programmatic post-check. WRONG-with-rationale exemplar in the prompt.
5. **Deterministic opener pruning.** `available_openers` (rotation minus recently-used) flows in the user-message payload alongside `recent_reply_starts`. System prompt cache prefix unchanged. Naturalness flag remains as audit signal.
6. **`is_post_cap_advance` propagates regardless of who chose advance.** State Engine sets the flag whenever `push_back_count == 2` before the transition.

### Non-goals

- **No model upgrade.** Speaker stays `gpt-5.4-nano` with `reasoning_effort="none"`. Memory-locked.
- **No bank text rewrites.** Bank stays in written-rubric form. Memory-locked.
- **No structural changes to the Judge↔Speaker boundary architecture.** The boundary stays; this spec extends what flows across it.
- **No new `InstructionKind`.** `clarify` remains a single kind on the Speaker side; the kind discriminator lives in `next_action_payload` and `SpeakerInput`.
- **No prompt version bump (v2 → v3).** Wire format extends backward-compatibly with a fallback (`clarify_kind=broad_rephrase` when absent). Engine prompt version stays `v2`.
- **No caching investigation in this spec.** Filed as separate diagnostic; tracked but out of scope.
- **No STT-side fixes.** Sarvam vocabulary hints / post-STT fuzzy correction is a separate workstream.
- **No persona changes.** Arjun stays; tenant override still scoped to `engine_agent_name`.
- **No CI scaffold.** Solo-dev; tests run via pytest manually plus one real-session mock.

---

## 3. Design overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  Judge LLM (gpt-5.4-mini, reasoning_effort=medium)                       │
│  reads:  candidate_utterance + active_question + signal state            │
│  emits:  { next_action: clarify,                                         │
│            next_action_payload: ClarifyPayload {                         │
│              kind: "clarify",                                            │
│              clarify_kind: term_definition                               │
│                          | concept_explanation                           │
│                          | use_case_anchor                               │
│                          | broad_rephrase                                │
│                          | probe_context }}                              │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  State Engine (deterministic)                                            │
│  - propagates clarify_kind to SpeakerInput unchanged                     │
│  - existing consecutive_dont_know_count >= 1 escalation still wins       │
│    (clarify → acknowledge_no_experience regardless of kind)              │
│  - sets is_post_cap_advance whenever push_back_count == 2 pre-transition │
│    (regardless of whether Judge or SE chose advance)                     │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  Speaker LLM (gpt-5.4-nano, reasoning_effort=none, temp=0.7)             │
│  system prompt:  _preamble.txt + clarify.txt (5 PATH dispatch sections)  │
│  user message :  SpeakerInput {                                          │
│    instruction_kind: "clarify",                                          │
│    clarify_kind: "concept_explanation",                                  │
│    bank_text: …,                                                         │
│    last_candidate_utterance: …,                                          │
│    recent_reply_starts: […],         # forbidden                         │
│    available_openers: […],           # filtered rotation, for this turn  │
│    … }                                                                   │
│                                                                          │
│  Dispatches on clarify_kind. PATH E enforces anti-leak boundary.         │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                          spoken utterance
                          + naturalness_flags (extended)
                                  │
                                  ▼
                              audit envelope
```

---

## 4. Schema changes

### 4.1 Judge output

`backend/nexus/app/modules/interview_engine/models/judge.py`:

```python
class ClarifyKind(str, Enum):
    term_definition     = "term_definition"
    concept_explanation = "concept_explanation"
    use_case_anchor     = "use_case_anchor"
    broad_rephrase      = "broad_rephrase"
    probe_context       = "probe_context"


class ClarifyPayload(BaseModel):
    kind: Literal["clarify"] = "clarify"
    clarify_kind: ClarifyKind   # NEW, required
```

`JudgeOutput.next_action_payload` continues to be a discriminated union; `ClarifyPayload` replaces the existing kind-only variant.

**Migration safety.** A legacy Judge output that emits `{"kind": "clarify"}` without `clarify_kind` produces a Pydantic `ValidationError`. The existing judge-fallback path (currently emits `judge.fallback` audit events from the orchestrator) handles this: when a validation error fires on a `clarify`-shaped output, the fallback synthesizes `clarify_kind=broad_rephrase` (the safest default — broad_rephrase produces a context-bringing scenario that works for nearly any candidate utterance). No traffic-compatibility shim; single deploy.

### 4.2 Speaker input

`backend/nexus/app/modules/interview_engine/models/speaker.py`:

```python
class SpeakerInput(BaseModel):
    # ... existing fields ...
    clarify_kind: ClarifyKind | None = None     # NEW
    available_openers: list[str] = []           # NEW
```

`clarify_kind` is populated when `instruction_kind=clarify`; None otherwise. `available_openers` is populated unconditionally for all kinds (the filter applies to every Speaker call, since opener rotation is a persona-wide concern, not a clarify-specific one).

---

## 5. Judge prompt changes

`backend/nexus/prompts/v2/engine/judge.system.txt`:

### 5.1 §1 decision tree — expanded CLARIFY sub-tree

Replace the current §1.3 (2-sub-case definition) with the 5-sub-case definition:

```
3. CLARIFY — candidate doesn't understand or needs context to engage.
   Pick ONE clarify_kind:

   a. term_definition — candidate asked about a SPECIFIC TERM in the
      question.
      Trigger: "What is X?", "What do you mean by Y?", "What's an
      ERP?"

   b. concept_explanation — candidate is ENGAGED with the topic but
      asks WHY a concept matters / asks for the FAILURE MODE of a
      premise they have stated.
      Trigger: "Why would there be duplicates if IDs are unique?",
      "Help me understand why X is a concern here", "How is it that
      X is a concern given Y?"
      Disambiguation vs push_back: the candidate is not refusing or
      evading — they are framing the conceptual gap that's blocking
      their answer. If push_back_count > 0 on this question and the
      candidate's confusion is conceptual (asks WHY / HOW) rather
      than evasive (deflects, restates without specifics), prefer
      concept_explanation over a further push_back.
      Disambiguation vs meta_confession: meta_confession says "I
      cannot answer this" (gives up); concept_explanation says
      "help me understand WHY" (asks for grounding to answer).

   c. use_case_anchor — candidate asks for the BUSINESS or OPERATIONAL
      setting.
      Trigger: "What is the use case for X in this org?", "What kind
      of company is this?", "What does the team do?", "Can you give
      me an example?", "Can you give me a specific case?" (bare ask,
      no qualifier).

   d. broad_rephrase — generic confusion with no specific ask.
      Trigger: "I don't understand", "Can you rephrase?", "Can you
      say it differently?", "I'm not following."

   e. probe_context — candidate is confused after the agent just
      delivered a PROBE (bank_text is short, follow-up shape — the
      input_builder passes the probe text as bank_text in this case).

   "Give me an example" disambiguation rule:
     - Bare ("Can you give me an example?") → use_case_anchor.
     - Qualified with "of why X" / "of how X breaks" / "of a case
       where X" → concept_explanation.
     - Qualified with "of what you're looking for" / "of an answer"
       / "of what's right" → redirect with candidate_off_topic=true
       (rubric fishing — covered by §1.1).

   Never use clarify for greetings (redirect) or for hint-fishing
   (redirect). The consecutive_dont_know_count >= 1 escalation still
   applies regardless of clarify_kind — at that count, emit
   acknowledge_no_experience.
```

### 5.2 §4 CLARIFY section — extended description

Update the existing §4 CLARIFY paragraph to reflect the 5 sub-cases. The hard rule about `consecutive_dont_know_count` stays.

### 5.3 §8 worked examples — one new example per new kind

Add three new worked examples to §8 (the existing 6 stay):

- **EXAMPLE G** — `concept_explanation` on a follow-up (T12 shape).
  Active question: Q3 (nightly upsert duplicates triage). `push_back_count=0`. Candidate: *"That's what I need to understand, how is it that idempotency is a concern here because we are taking unique order IDs as a trigger…"*
  Output: `next_action=clarify`, `next_action_payload={kind:"clarify", clarify_kind:"concept_explanation"}`. `reasoning` explicitly cites the "asks WHY" framing + topic engagement disambiguation rule.

- **EXAMPLE H** — `use_case_anchor` from a bare example ask.
  Candidate: *"Can you give me an example?"* (no qualifier). Output: `clarify_kind=use_case_anchor`. `reasoning` cites the bare-ask disambiguation rule.

- **EXAMPLE I** — `concept_explanation` chosen over push_back at `push_back_count=1`.
  Same shape as G but with `push_back_count=1` to demonstrate the "prefer concept_explanation over further push_back when conceptual" rule.

---

## 6. Speaker prompt changes — rewritten `clarify.txt`

`backend/nexus/prompts/v2/engine/speaker/clarify.txt`:

### 6.1 Structure

```
# TASK
The candidate needs help. The Judge has classified WHAT KIND of help
via clarify_kind. Dispatch on clarify_kind. ONE PATH only.

# SHAPE BY PATH (soft targets — flagged at 1.5×, not enforced)
  term_definition       ≈ 25 words   one clause + re-ask
  concept_explanation   ≈ 50 words   one failure scenario + hand-back ask
  use_case_anchor       ≈ 40 words   business setting + re-ask
  broad_rephrase        ≈ 35 words   domain + volume + contrast + re-ask
  probe_context         ≈ 25 words   short definition + probe re-ask

# OPENERS
Pick from `available_openers` in your input — the rotation already
pruned of openers you used recently. NEVER use anything in
`recent_reply_starts`. The system rotation is the master persona;
`available_openers` is the per-turn allowed subset.

# PATH DISPATCH
[5 PATH sections — see 6.2 below]

# ANTI-ENUMERATION (load-bearing — unchanged from v1)
[existing rule preserved]

# EXEMPLARS
[2 per PATH, plus 1 WRONG-with-rationale on PATH E]

# REMINDER
Expose the gap, never fill it. Per-PATH soft targets. NO rubric leak.
```

### 6.2 Per-PATH design

**PATH A — `clarify_kind: term_definition`** (mostly unchanged from current production).
Define the term plainly in one short clause; re-ask in the broadest form. One new exemplar added for breadth.

**PATH B — `clarify_kind: broad_rephrase`** (REWRITTEN — Issue 2 fix).

Rephrase by anchoring to a concrete business setting. Bring three moves explicitly:
1. **Domain** — e-commerce / fintech / healthcare / SaaS / logistics.
2. **Volume** — a concrete number ("about 10k orders a day").
3. **Contrast** — what the answer is NOT ("minutes, not a nightly batch").

Then re-ask in the broadest single form.

*Exemplar (NEW pattern):*
> *"Mm — picture a digital-native retailer doing about 10k orders a day, all flowing from Salesforce into their on-prem E-R-P. Fulfillment needs them visible in minutes, not in a nightly batch. How would you design that integration end-to-end?"*

**PATH C — `clarify_kind: probe_context`** (mostly unchanged from current production).
By-X-I-mean clarification on the probe + re-ask of the probe.

**PATH D — `clarify_kind: use_case_anchor`** (NEW).

Provide the business or operational setting the candidate is asking about, so they can ground their answer. Setting is allowed; design criteria are not.

*Exemplar:*
> *"See — say you're working with a digital-native retailer. Salesforce is their customer-facing order desk; the E-R-P is the back-office that fulfillment, finance, and inventory all live in. Without the sync, fulfillment doesn't see new orders. With that setting in mind, how would you design the flow?"*

**PATH E — `clarify_kind: concept_explanation`** (NEW — load-bearing anti-leak).

Principle: **expose the gap, never fill it.** Describe ONE concrete failure scenario where the candidate's premise breaks. After describing the failure, hand the question back open-ended.

Shape:
1. Acknowledge the premise the candidate stated.
2. Describe ONE concrete failure scenario where that premise breaks. Generic infra reality is fair game (retries, network partitions, partial writes, at-least-once delivery, race conditions, ordering). Pick ONE scenario, not a menu.
3. Hand the question back — open-ended, no nudge toward the fix.

Anti-leak boundary specific to PATH E:
```
ALLOWED:   "Retries can re-fire after a partial success."
ALLOWED:   "At-least-once delivery means the same event can land twice."
FORBIDDEN: "...so you'd want an idempotency key on the order ID."
FORBIDDEN: "...the fix is a conditional upsert."
FORBIDDEN: enumerating multiple solutions ("retries OR dedup OR ...").

RULE: After describing the failure, the candidate must still have work
to do. If your last sentence proposes a fix or names a design pattern,
you've leaked. Trim and re-emit.

WORDS that almost always indicate leak: "use", "implement", "add a",
"store a", "key on", "track", "deduplicate by". If your output
contains these in the last sentence, you've crossed the boundary.
```

*Exemplar — CORRECT (T12 scenario):*
> Candidate: *"how is it that idempotency is a concern here because we are taking unique order IDs as a trigger"*
> Speaker: *"See — your trigger fires once per new order ID, but the i-Paa-S workflow itself can retry after a network blip. Imagine the first attempt completed the E-R-P write just before the timeout. The retry now fires the same order ID into the E-R-P again. So — given that, what would you put in your design to handle it?"*

*Exemplar — WRONG (anti-pattern, with rationale):*
> *"See — retries can re-fire after a partial success, so you'd want an idempotency key on the order ID to prevent duplicate writes."*
> Why wrong: the last clause proposes the solution. Candidate has nothing to think about. Trim everything after "partial success" and hand the question back.

### 6.3 `_preamble.txt` change

Append to the existing §OPENINGS & ANTI-REPETITION section:

```
For THIS turn, your input includes `available_openers` — the rotation
already pruned of openers you've used recently. Pick one from there.
The system rotation above is the master persona; available_openers is
the per-turn allowed subset. recent_reply_starts is the forbidden list.
```

No other preamble changes.

---

## 7. Deterministic opener pruning

### 7.1 New helper module

`backend/nexus/app/modules/interview_engine/speaker/openers.py`:

```python
"""Opener slug computation + per-turn filtering.

Single source of truth for the 3-word slug used by both:
- naturalness.detect_repeated_opener (post-hoc flagging)
- input_builder (pre-prompt filtering)

Lifting this here removes the duplication that previously had
naturalness.py owning the slug definition while input_builder needed
its own copy.
"""
from __future__ import annotations


def opener_slug(text: str) -> str:
    """Lowercase first-3-word slug. Same definition as the detector."""
    return " ".join(text.strip().split()[:3]).lower()


def filter_available_openers(
    rotation: tuple[str, ...],
    recent_reply_starts: list[str],
) -> list[str]:
    """Return the rotation pruned of openers whose 3-word slug matches
    any recent_reply_start.

    Safety: at current dimensions (9 openers in PersonaSpec.opener_rotation,
    3-4 entries in recent_reply_starts), the filter never empties the
    rotation. If a future change inverts this — e.g., rotation shrinks
    or recent_reply_starts grows — the safety branch returns the full
    rotation rather than emitting an empty bullet list to the Speaker.
    """
    used = {opener_slug(s) for s in recent_reply_starts if s.strip()}
    fresh = [op for op in rotation if opener_slug(op) not in used]
    return fresh or list(rotation)
```

### 7.2 `input_builder.py` integration

Replace the existing local `_slug` definition (if any duplication exists) with an import from `openers`. Add `available_openers` computation:

```python
from app.modules.interview_engine.speaker.openers import (
    filter_available_openers,
)
from app.modules.interview_engine.speaker.persona import DEFAULT_PERSONA

# Inside build_speaker_input(...), after reply_starts_payload is computed:
available_openers_payload = filter_available_openers(
    DEFAULT_PERSONA.opener_rotation,
    reply_starts_payload,
)
```

`SpeakerInput.available_openers` populated for every kind.

### 7.3 `naturalness.py` integration

Replace the local `_slug` in `detect_repeated_opener` with `from .openers import opener_slug`. No behavior change; deduplication only.

---

## 8. Naturalness extensions

### 8.1 Per-clarify-kind soft targets

`_SOFT_TARGETS` becomes keyed by `(instruction_kind, clarify_kind | None)` with fallback semantics:

```python
_SOFT_TARGETS: dict[tuple[str, str | None], int] = {
    ("clarify", "term_definition"):     25,
    ("clarify", "concept_explanation"): 50,
    ("clarify", "use_case_anchor"):     40,
    ("clarify", "broad_rephrase"):      35,
    ("clarify", "probe_context"):       25,
    ("clarify", None):                  35,  # legacy fallback
    ("deliver_first_question", None):   22,
    ("deliver_question", None):         25,
    ("deliver_probe", None):            18,
    ("push_back", None):                18,
    ("redirect", None):                 18,
    ("acknowledge_no_experience", None): 12,
    ("polite_close", None):             20,
    ("repeat", None):                   99999,
}
```

`detect_exceeded_soft_target` updated to accept an optional `clarify_kind` parameter:

```python
def detect_exceeded_soft_target(
    output: str,
    instruction_kind: str,
    clarify_kind: str | None = None,
) -> bool:
    target = (
        _SOFT_TARGETS.get((instruction_kind, clarify_kind))
        or _SOFT_TARGETS.get((instruction_kind, None))
    )
    if not target or not output:
        return False
    return len(output.split()) > int(target * 1.5)
```

### 8.2 New `solution_leak_suspected` flag

```python
_LEAK_INDICATOR_VERBS: tuple[str, ...] = (
    "use ", "implement ", "add a ", "store a ", "key on ",
    "track ", "deduplicate by ",
)


def detect_solution_leak(
    output: str,
    clarify_kind: str | None,
) -> bool:
    """True iff PATH E (concept_explanation) output's last sentence
    contains a leak-indicator verb.

    Anti-leak signal specific to concept_explanation. Cheap regex
    post-check. Informational — does not block emission, just surfaces
    in the audit envelope so we can grep across sessions.
    """
    if clarify_kind != "concept_explanation" or not output:
        return False
    last = output.rstrip(".!?").rsplit(".", 1)[-1].lower()
    return any(verb in last for verb in _LEAK_INDICATOR_VERBS)
```

Both flags appended to `SpeakerOutputPayload.naturalness_flags`. `solution_leak_suspected` is informational — does not regenerate, does not retry. Surfaces in the audit envelope for grep-based diagnostic across sessions.

---

## 9. `is_post_cap_advance` propagation

State Engine change (in `state/` — exact module name verified at implementation time): wherever the transition handler currently sets `is_post_cap_advance` only on SE-forced downgrades, replace with an unconditional check:

```python
# Before applying the advance transition, regardless of whether Judge
# or SE chose advance:
is_post_cap_advance = (
    next_action == "advance"
    and prior_push_back_count >= PUSH_BACK_CAP  # PUSH_BACK_CAP == 2
)
```

`prior_push_back_count` is the active question's `push_back_count` *before* this turn's transition is applied. The Judge-voluntary case (T8 in the diagnostic session: Judge picked `advance` while cap was already reached) now correctly raises the signal.

Plumbed into `build_speaker_input` as today (already a parameter; the only change is upstream — the State Engine sets it more eagerly).

---

## 10. Testing strategy

### 10.1 Unit tests (cheap, run via pytest)

- **Judge schema validation.** `ClarifyPayload` requires `clarify_kind`. Legacy `{kind: "clarify"}` without `clarify_kind` raises `ValidationError`. Fallback path synthesizes `clarify_kind=broad_rephrase`.
- **`filter_available_openers`.** Exhaustive cases: empty `recent_reply_starts` (returns full rotation); all 9 openers banned (returns full rotation via safety fallback); partial overlap (returns expected subset); case-insensitivity (`"mm, ok —"` vs `"Mm, OK —"` match).
- **`opener_slug`.** Truncation to 3 words; whitespace handling; empty/whitespace-only input.
- **`_SOFT_TARGETS` lookup.** `(clarify, concept_explanation)` returns 50; `(clarify, None)` returns 35 (legacy); unknown clarify_kind tuple falls back to `(clarify, None)`.
- **`detect_solution_leak`.** Fires on the WRONG exemplar last sentence; doesn't fire on the CORRECT exemplar; doesn't fire when `clarify_kind != "concept_explanation"`.
- **`is_post_cap_advance` propagation.** Three cases: Judge picks push_back at cap → SE downgrades → flag True; Judge picks advance at cap voluntarily → flag True (this is the new case); Judge picks advance below cap → flag False.

### 10.2 Replay tests (run once during development; not CI)

Fixtures: `engine-events/a13ec188-…json`, `engine-events/bf34128f-…json`, `engine-events/d82d7407-…json`.

For each fixture, replay each turn's `JudgeInputPayload` through the new Judge prompt; assert specific routing:

- a13ec188 T8 (*"Why would there be duplicates for the same order ID…"*) → `clarify_kind=concept_explanation`.
- a13ec188 T12 (*"how is it that idempotency is a concern here…"*) → `clarify_kind=concept_explanation`.
- a13ec188 T5 (*"What is the use case that Salesforce is being used in…"*) → `clarify_kind=use_case_anchor`.
- a13ec188 T2 (*"I couldn't understand the question"*) → `clarify_kind=broad_rephrase`.

For Speaker output: replay through the rewritten `clarify.txt` with synthetic `clarify_kind` inputs. Don't assert exact strings (LLM nondeterminism). Instead:

- PATH B output contains at least one volume-shaped token. Regex: `\b\d+\s*(k|K|orders|requests|users|TPS|RPS)\b` matches at least once.
- PATH D output contains one of `{"company", "team", "department", "fulfillment", "back-office", "retailer", "operator"}`.
- PATH E output's last sentence does NOT contain any leak-indicator verb.
- Across the 14-turn replay with opener filtering applied: `repeated_opener` flag count drops from 5 (current baseline) to ≤1.

### 10.3 Real session test

One candidate-mock session run by user. Grep audit envelope:
- `clarify_kind` values present and varied.
- `repeated_opener` flag count ≤ 1.
- `solution_leak_suspected` flag count == 0.
- T8/T12-shape questions (if reproduced) route to `concept_explanation` and Speaker output describes a failure scenario without leak.

### 10.4 Prompt-quality (existing pytest-marker)

Extends `pytest -m prompt_quality` (gated off default suite). Add cases for PATH E anti-leak: 8/10 sampled outputs across temperatures 0.5 / 0.7 / 0.9 must have no leak-indicator verbs in the last sentence. Same fixture set as 10.2.

---

## 11. Rollout sequence

Solo-dev path, single branch, commits sequenced for bisectability:

1. **Schema + filter helper.** `ClarifyKind` enum, `ClarifyPayload.clarify_kind`, `SpeakerInput.clarify_kind` + `available_openers`, `speaker/openers.py` helper, `naturalness.py` refactor to import from openers. No behavior change yet — fields wired but not consumed.
2. **Judge prompt.** `judge.system.txt` §1.3 expanded sub-tree + disambiguation rules + §8 worked examples (G, H, I). Judge starts emitting `clarify_kind`. Speaker still ignores it (uses old dispatch).
3. **Speaker prompt + dispatcher.** Rewritten `clarify.txt` with 5 PATH sections. `input_builder.py` populates `clarify_kind` and `available_openers`. Speaker now dispatches on `clarify_kind`. `_preamble.txt` gains the `available_openers` paragraph. **This is the visible behavior change.**
4. **Naturalness extensions.** `_SOFT_TARGETS` tuple keys, `detect_solution_leak`, both wired into `SpeakerOutputPayload.naturalness_flags` computation in the orchestrator.
5. **`is_post_cap_advance` propagation.** State Engine change. Five-line diff.
6. **Test pass.** Unit tests + replay tests + one real-session mock. If real session surfaces a regression, `git bisect` against the 5 commits.

Each commit standalone-mergeable.

---

## 12. Out of scope (explicit)

- **Issue 5 (Sarvam STT mishearings).** Separate workstream — Sarvam vocabulary hints or post-STT fuzzy correction layer.
- **Cache investigation.** Three hypotheses noted in §1; deferred to a separate diagnostic task. Worth flagging because 600ms of latency per Speaker call is masked by the current 1/14 cache hit rate.
- **Bank text rewrites.** Memory-locked.
- **Speaker model upgrade.** Memory-locked.
- **Persona changes.** Arjun locked in code; tenant override scoped to `engine_agent_name`.
- **New `InstructionKind` values.** Intent layer lives inside the existing `clarify` kind, not as a sibling action.
- **Multi-call Speaker / post-validator LLM.** Single Speaker call per turn; informational flags only.
- **`recent_turns` window changes.** Stays at the existing window size.
- **CI scaffold or eval harness.** Solo-dev; pytest manual + real session mock.

---

## 13. References

- Diagnostic session: `backend/nexus/engine-events/a13ec188-ebf4-4b5e-95fa-912555a556a7.json` (14 turns, 2026-05-18).
- Predecessor sessions for replay fixtures: `bf34128f-764f-4731-a19e-66fa17cb2289.json`, `d82d7407-5d7b-4004-af35-cc1ab50768c8.json`.
- Predecessor spec: `docs/superpowers/specs/2026-05-17-speaker-realism-design.md`.
- Judge↔Speaker boundary spec: `docs/superpowers/specs/2026-05-08-interview-engine-judge-speaker-redesign-design.md`.
- State Engine inverse-quality-gate + validation: `docs/superpowers/specs/2026-05-12-engine-simplification-design.md`.
- Naturalness flag detectors: `backend/nexus/app/modules/interview_engine/speaker/naturalness.py`.
- Acronym preprocessor (passed-through context for the spec): `backend/nexus/app/modules/interview_engine/speaker/input_builder.py:31-62`.
- PersonaSpec source of truth: `backend/nexus/app/modules/interview_engine/speaker/persona.py`.
