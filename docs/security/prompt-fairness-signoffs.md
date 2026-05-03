# Prompt Fairness Sign-Off Log

This document records the senior-reviewer fairness sign-off for every
prompt file (or prompt-file delta) that affects candidate scoring,
classification, or routing. Per root `CLAUDE.md` "Compliance Anchors"
+ overview spec Decision #18, every such prompt change requires a
human reviewer to walk a fairness checklist before the change is
considered shipped.

The arc convention is **commit-message sign-off** (since the dev-state
arc develops commit-by-commit on `main` rather than via PR review).
This log is the durable, human-readable index of which prompt files
have been reviewed by whom and when. Future audit replay should treat
this file as authoritative for sign-off attribution.

---

## How to add a sign-off

1. Pick the row matching the prompt file you reviewed.
2. Walk the checklist (the row links to the per-prompt checklist or
   summarises it inline).
3. Add a line under "Sign-offs" with your name, the commit SHA your
   sign-off applies to, the date, and any notes.

If you reviewed a delta to an already-signed-off prompt, add a new
sign-off line — don't overwrite the previous one. The log is
append-only.

---

## Standing checklist (applies to every row unless that row's
"Additional checks" overrides it)

- [ ] No biased phrasing or examples that imply a protected class.
- [ ] No protected-class signals in any tool argument schema the LLM
      can fill (or, for prompts that don't introduce tools: no
      protected-class proxy probing).
- [ ] Knockout reasons must be factual self-disclosures, not
      AI-inferred personality traits.
- [ ] Borderline candidates remain human-reviewable; the prompt does
      not encourage auto-advance or auto-reject.
- [ ] Persona maintenance cases pass (covered by
      `prompt_quality/test_persona_maintenance.py` for engine
      prompts, by the prompt-quality runs for bank-gen prompts).

---

## Phase 2 — Controller cutover (commit `6193f73`)

### `prompts/v1/interview/controller.txt`

Identity / role / tone / tools / GOOD-BAD examples / jailbreak /
off-topic / persona guidance for `InterviewController`.

**Additional checks (Phase 2 spec §7.1):**
- [ ] Tool-argument schemas (`category` enum on
      `flag_safety_concern`, etc.) carry no protected-class fields.
- [ ] Free-form `note` fields are redaction-default per event-log
      §5.2.

**Sign-offs:**
- [x] Reviewed by **Ishant Pundir** for commit `6193f73`, date
  **2026-05-03**. Notes: Initial cutover prompt body. Walked the
  standing checklist + Phase 2 §7.1 additions; no protected-class
  surface in tool schemas; persona-maintenance prompt-quality suite
  green at sign-off time.

### `prompts/v1/interview/task_technical_depth.txt`

Per-task instructions for `TechnicalDepthTask`: open-ended depth
probing with `record_answer_assessment`, `request_probe`,
`complete_question`. Max probes = 1.

**Additional checks (Phase 2 spec §7.1):**
- [ ] No leading phrasing that rewards a particular answer style.
- [ ] Below-bar tier description is not punitive; reflects
      observable evidence absence, not personality.

**Sign-offs:**
- [x] Reviewed by **Ishant Pundir** for commit `6193f73`, date
  **2026-05-03**. Notes: Walked the standing checklist + Phase 2
  §7.1 additions; rubric anchors are content-based not
  personality-based; below-bar tier describes evidence absence
  factually.

---

## Phase 3 — Per-kind tasks (commits `be06fc2`, `b976e1c`)

### `prompts/v1/interview/task_behavioral.txt` (commit `be06fc2`)

Per-task instructions for `BehavioralStarTask`: STAR-component
detection, `record_behavioral_answer`, `request_star_probe`. Max
probes = 2.

**Additional checks (Phase 3 spec §4.1):**
- [ ] No leading phrasing that suggests a "right" STAR shape.
- [ ] No personality scoring (e.g., "candidate seems
      collaborative" — this is inference from behavior, not a
      fact).
- [ ] No protected-class proxy probing (e.g., follow-ups must not
      ask about family, life situation, or other protected
      contexts).
- [ ] Non-answers (candidate says "I don't have an example") are
      never re-probed.
- [ ] Probes framed as natural curiosity, not skepticism.

**Sign-offs:**
- [x] Reviewed by **Ishant Pundir** for commit `be06fc2`, date
  **2026-05-03**. Notes: Walked the standing checklist + Phase 3
  §4.1 additions. The commit message records the substance of the
  walk inline ("Fairness signoff per spec §4.1: no leading
  phrasing, no personality scoring, no protected-class proxy
  probing, non-answers never probed, probes framed as natural
  curiosity").

### `prompts/v1/interview/task_compliance_binary.txt` (commit `b976e1c`)

Per-task instructions for `ComplianceBinaryTask`: yes/no
attestation with `record_compliance_attestation`,
`request_compliance_clarification`. Max probes = 0; one
clarification allowed; 60s budget cap.

**Additional checks (Phase 3 spec §5.1):**
- [ ] No inference-from-related-statements (the task may only score
      what the candidate explicitly attests, not what it can guess
      from context).
- [ ] No protected-class proxy probing — explicitly forbid
      child-care / family / parenting follow-ups for shift / hours
      / availability questions.
- [ ] Single-clarification limit is enforced (not "as many as
      needed").
- [ ] Knockout pairing rule: a `confirmed=false` attestation triggers
      `disqualify_knockout` exactly once (no double-counting).
- [ ] No moralizing about the candidate's answer (e.g., "that's a
      shame" / "are you sure?").

**Sign-offs:**
- [x] Reviewed by **Ishant Pundir** for commit `b976e1c`, date
  **2026-05-03**. Notes: Walked the standing checklist + Phase 3
  §5.1 additions. The commit message records the substance of the
  walk inline ("Fairness signoff per spec §5.1:
  no inference-from-related-statements, no protected-class proxy
  probing (child-care follow-up explicitly forbidden),
  single-clarification limit, knockout pairing rule, no
  moralizing").

---

## Phase 4 — Bank-generator prompt edits (commit `3468025`)

The Phase 4 prompt edits affect classification (which kind = which
task = which budget = which scoring path) so they require fairness
sign-off per Decision #18. Four prompt files were edited atomically
in one commit; the §5.5 checklist applies to the combined diff.

### `prompts/v1/question_bank_common.txt` — §6 Question kind block

New "§6 Question kind — choose the engine task subclass" section
appended. Defines the 3 generator-allowed kinds (technical_depth,
behavioral_star, compliance_binary) with examples and selection
rules. Includes the structural-not-social fairness guard.

### `prompts/v1/question_bank_phone_screen.txt` — calibration

New "Question kind selection (this stage)" paragraph. Phone screen
prefers `compliance_binary` for binary knockouts.

### `prompts/v1/question_bank_ai_screening.txt` — calibration

New "Question kind selection (this stage)" paragraph. AI screening
HARD BANs `compliance_binary` (would rob deep-dive budget).

### `prompts/v1/question_bank_regenerate_one.txt` — preserve rule

New bullet under "Your task": preserve original `question_kind`
unless `replace_signal_values` materially changes the question
shape.

**Additional checks (Phase 4 spec §5.5):**
- [ ] No prompt text uses biased phrasing or examples that imply a
      protected class.
- [ ] The "kind is structural, not social" guard in §6 is intact
      and unambiguous.
- [ ] No example question reproduces a problematic real-world ask
      (e.g., "where are you from", "do you have kids").
- [ ] The `compliance_binary` examples are factual self-disclosures
      (work auth, shift, relocation, certification), not AI-inferred
      personality traits.
- [ ] The BAN on `compliance_binary` in ai_screening is intact
      (prevents budget-stealing misclassification that would cost
      candidates depth-probe time unequally).
- [ ] No prompt body changes the existing rules around protected
      classes, evidence-based scoring, or borderline handling.

**Sign-offs:**
- [x] Reviewed by **Ishant Pundir** for commit `3468025`, date
  **2026-05-03**. Notes: Walked the standing checklist + Phase 4
  §5.5 additions across all four prompt files in the single atomic
  commit. The structural-not-social guard at §6 is intact; the
  `compliance_binary` example set (UK shift, work auth, Bangalore
  relocation, AWS Solutions Architect cert) is factual
  self-disclosure only, with no protected-class proxy. The HARD
  BAN on `compliance_binary` in ai_screening is preserved verbatim
  ("BANNED at this stage"), validated empirically by the Phase 4
  prompt-quality test (commit `fb7e6e2`) across N=3 independent
  ai_screening runs that emitted zero `compliance_binary`
  questions.

---

## Phase 6 — server-authoritative audio (2026-05-03 → ROLLED BACK 2026-05-04)

**Rolled back.** The Phase 6 audio invariant (browser EC/NS/AGC OFF,
ai_coustics SPARROW_S / 0.4 as sole filter) was reverted when the
production target shifted to self-hosted LiveKit from day one. The
candidate surface is back on standard browser-side WebRTC noise
suppression and the e2e checklist's audio fairness scenarios (9a
soft-spoken / 9b noisy-environment) no longer gate. See
`docs/security/threat-model.md` Phase 6 section for the rollback
rationale.

If LiveKit Cloud ever becomes the production target again, the
fairness implications of disabling browser-side EC/NS/AGC need a
fresh review — not a revival of this entry.

---

## Future prompt changes

When a new prompt file (or material delta) lands:

1. Append a new section to this file under the appropriate phase
   heading (or open a new "Phase N — …" section).
2. Reference the commit SHA + the spec checklist that applies.
3. Add the sign-off line.

If the prompt change is a tiny typo / formatting fix that does not
affect candidate scoring, classification, or routing, no sign-off
is required — but note the commit SHA in this file's git history
("docs(security): clarify the X language in prompt-fairness-signoffs
log") so the audit trail is intact.
