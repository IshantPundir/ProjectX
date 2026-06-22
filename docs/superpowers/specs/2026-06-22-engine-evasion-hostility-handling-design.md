# Engine: graceful handling of evasion & hostility — design

- **Date:** 2026-06-22
- **Module:** `app/modules/interview_engine/` (brain + mouth prompts only)
- **Status:** Approved (brainstorm) → ready for implementation plan
- **Change class:** Prompt-engineering only. No code, contract, move-set, or migration changes.

## Problem

When a candidate does something other than answer the question — **evades**
("I won't answer that", "next question", "why does this matter?") or **insults /
gets hostile** toward Arjun ("you're a useless robot", "this is stupid") — the
engine today either flatly re-poses the question or trips the `stalled` counter
and advances. There is no natural, professional course-correction.

Root cause is **classification**, not architecture:

- The brain's `redirect` trigger is defined as *"off-topic, rambling, or an
  injection"*. A direct insult is none of those, so it mis-classifies; and even
  when it lands on `redirect`, the mouth's `redirect.txt` framing
  (*"as if you simply didn't notice the detour"*) forces a flat re-pose.
- A relevance question ("why does this matter?") has no defined home, so it is
  handled inconsistently — sometimes treated as evasion and advanced past,
  instead of the agent contextualizing the question and keeping the floor open.

## Goal

Teach the brain to distinguish the cases below and respond like a calm,
professional human interviewer — steering back without rewarding hostility,
without revealing scoring criteria, and without advancing prematurely.

## The taxonomy (the core of this change)

When a committed turn is **not an answer**, the brain picks among five cases.
Only the last two ever advance, and only on **persistence**.

| Candidate does | Move | Floor moves? |
|---|---|---|
| Doesn't get the **words / scenario** | `clarify` (existing) | No — re-pose simpler |
| **Relevance**: "why does this matter? / why are you asking this? / how is this relevant?" | `clarify` (**new sub-case**) | **No** — brief low-stakes reason it helps, reveal nothing, re-pose the SAME question |
| **Fishing**: "what's the correct answer? / what are you looking for?" | `answer_meta` (existing) | No — deflect, reveal nothing, return to floor |
| **Flat refusal / skip**: "I won't answer", "skip this", "next question" | `redirect` + acknowledge & reframe **once** → persistent → `stalled` advance | Only if they keep refusing |
| **Insult / hostility / abuse**: "you're useless", "this is stupid" | `redirect` + light professional boundary, then back | No (advances only via `stalled` if it blocks the answer) |

Key invariants this encodes:

- **"Why does this matter?" is a `clarify`, never an advance and never evasion.**
  Arjun explains *why the question helps* (the question's purpose — NOT the
  expected answer or criteria) and gives the candidate another shot at the same
  question. Advancing only ever happens on **persistent flat refusal** via the
  existing `stalled` → warm-advance path.
- **Reframe is offered once.** A single relevance question or refusal does not
  advance; repeated dodging trips the existing deterministic `stalled` counter,
  which already advances warmly and records the signal as not demonstrated. No
  grinding, no arguing.
- **No-leak preserved everywhere.** Explaining a question's *relevance* or giving
  a *reason to engage* reveals nothing about the rubric. The existing
  `scrub_composed_say` gate still runs on every composed line.

## Tone decisions (locked during brainstorm)

- **Hostility / insult → light boundary, then redirect.** Briefly, warmly signal
  we'll keep it professional, then steer back. Never defensive, never scolding,
  never lecturing, never naming the insult back. e.g.
  *"Let's keep things on track — back to…"*
- **Evasion → acknowledge + reframe once, then advance if still refused.**
  Validate briefly, give a low-stakes reason to engage ("it just helps me see
  how you'd approach it — even a rough take is fine"), reveal nothing. If still
  refused next turn → `stalled` advance. e.g.
  *"Fair — it just helps me see how you'd approach it. Even a rough take is fine. So…"*

## Files changed (prompt-only)

1. **`prompts/v4/engine/brain.system.txt`**
   - **`clarify`**: add the **relevance sub-case** — "why does this matter / why
     are you asking this / how is this relevant" → give ONE brief, low-stakes
     reason the question helps, reveal nothing about scoring, re-pose the SAME
     question. Explicitly: this is NOT evasion and does NOT advance the floor.
     Keep it distinct from fishing (→ `answer_meta`) and from word/scenario
     confusion (existing clarify cases).
   - **`redirect`**: extend the trigger to **explicitly name hostility / insults
     / abuse / flat refusal / skip-attempts** (not just off-topic/rambling/
     injection). Sub-case the composed line:
     - off-topic / rambling / injection → unchanged warm bring-back.
     - hostility / insult → brief calm professional boundary, then redirect;
       never defensive/scolding/lecturing, never name the insult.
     - flat refusal / skip → brief acknowledgement + ONE low-stakes reframe, then
       re-pose; reveal nothing.
   - Add the **persistence rule**: a reframe/boundary is offered once; continued
     refusal/hostility is handled by the existing `⚠️ STALLED` → warm-advance
     rule (already in "WHEN A THREAD IS DONE"). Do not grind, do not argue.

2. **`prompts/v4/engine/mouth/clarify.txt`**
   - Add the relevance case: deliver a brief reason the question helps + re-pose
     the same question; warm, never defensive; no scoring leak. Principle + one
     good / one bad example (no replayed-incident scripting).

3. **`prompts/v4/engine/mouth/redirect.txt`**
   - Replace the universal *"as if you simply didn't notice the detour"* framing
     with delivery guidance matched to the brain's `SAY`: render naturally in
     persona; if it's a **professional boundary**, keep it calm and to one short
     clause — never defensive, preachy, or robotic; if it's a **reframe**, keep
     it warm and low-stakes. Principle + one good / one bad example per case.

4. **`prompts/v4/engine/mouth/bridge.txt`**
   - Narrow the "anything else / off-task" beat for **hostile / abusive /
     refusing** turns to the most minimal neutral filler (`"Mm…"`, `"Right…"`) —
     never `"okay"/"got it"` (reads as *agreeing* with the remark), never
     `"sure"/"of course"`, never defensive.

## Non-goals (YAGNI)

- **No auto-terminate** on persistent hostility. The screen advances via
  `stalled` and ends naturally when questions run out; only the candidate may
  explicitly end the session (existing `close` + `end_requested`). This avoids a
  thorny abandonment policy and keeps the Borderline-always-human-review
  invariant intact.
- **No new `BrainMove` / `DirectiveAct` / `DirectiveTone`.** `redirect` +
  `clarify` + the `neutral` tone carry the behavior. No `contracts.py`, driver,
  loop, or test-surface changes.

## Validation

- **Primary — live talk-test** (the standard for engine prompts):
  - insult mid-answer → calm boundary + redirect, not defensive, not a flat repeat;
  - "why does this matter?" → Arjun explains relevance + re-poses the SAME
    question (floor does NOT move), reveals nothing;
  - flat refusal once → reframe + re-pose; refuse again → warm advance (stalled);
  - persistent hostility → advances through the screen, never loops or argues.
- **Optional — `pytest -m prompt_quality`**: a real-API eval asserting a hostile
  input yields a calm boundary + redirect with **no rubric leak and no scolding**,
  and a relevance question yields a same-question re-pose (no advance).
- Restart `nexus-engine` after edits (`docker compose up -d --force-recreate
  nexus-engine`) — per-session `PromptLoader`, no hot-reload.

## Risks

- **Over-firm boundary on a nervous candidate.** Mitigation: the boundary is one
  short calm clause, never scolding/lecturing; a genuinely nervous candidate is a
  different signal (`reassure`), and the brain already distinguishes nerves.
- **Relevance reframe leaking criteria.** Mitigation: the reframe states the
  question's *purpose* ("helps me see how you approach this"), never the expected
  content; `scrub_composed_say` remains the structural backstop.
- **Bridge/real-line tone mismatch** (a warm "okay" beat before a boundary).
  Mitigation: bridge change narrows the insult/refusal beat to minimal neutral
  fillers so it composes cleanly with the boundary line.
