# Engine prompts v2 — changelog

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
