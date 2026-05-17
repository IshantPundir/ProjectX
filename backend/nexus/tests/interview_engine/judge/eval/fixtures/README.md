# Judge eval fixture corpus

This directory holds the hand-curated set of Judge eval fixtures. Each file is
one labeled scenario: a `JudgeInputPayload` + the expected output shape.

## Adding a fixture

When a Judge decision in a real session surprises you:

1. Open the session's audit envelope at `backend/nexus/engine-events/<session_id>.json`.
2. Locate the `judge.call` event for the surprising turn.
3. Copy the `judge.call.input_summary` content into a new fixture JSON
   (next free 3-digit ID, descriptive slug).
4. Label the **expected** output explicitly:
   - `next_action`: what should the Judge have emitted?
   - `turn_metadata`: which flags should be set?
   - `observations_*`: shape constraints (min/max count, expected signals).
   - `forbidden_failure_observations: true` when the case should NEVER produce a
     `->failed` observation.
   - `expected_reasoning_substrings`: soft check (warns, doesn't fail).
5. Commit. The corpus grows from your own real testing.

Target: ~50 fixtures by end of first month of v2 in production.

## Fixture file shape

See `005_probe_failure_mandatory_meta_confession.json` for the canonical example.

Required fields:
- `id` — unique slug matching the filename (without `.json`)
- `description` — one-sentence description of the scenario
- `tags` — list of tags (e.g. `["bluff_catch", "real_session"]`)
- `judge_input` — full `JudgeInputPayload` shape; all fields from the audit envelope
- `expected` — assertions to run against the Judge output
- `source` — `session_<id>_turn<N>` for real sessions, `synthesized` for hand-crafted
- `labeled_by` — who labeled this fixture
- `labeled_at` — ISO date of labeling

### `active_question_signal_metadata` notes

Real session audit envelopes captured before v2 do not include the `type` field
on signal metadata entries. When adapting old envelopes, default `type` to
`"competency"` unless the signal clearly describes years/duration/employer
(use `"experience"`) or a certification (use `"credential"`).

## Running the eval

```bash
# Default version (v2):
docker compose -f backend/nexus/docker-compose.yml run --rm nexus \
  pytest -m prompt_quality

# Compare against v1:
JUDGE_PROMPT_VERSION=v1 docker compose -f backend/nexus/docker-compose.yml run --rm nexus \
  pytest -m prompt_quality

# Single fixture by ID prefix:
docker compose -f backend/nexus/docker-compose.yml run --rm nexus \
  pytest -m prompt_quality \
  tests/interview_engine/judge/eval/test_judge_eval.py -k 005 -v
```

Cost: ~$0.65 per full run on ~50 fixtures. A/B (v1 + v2) ~$1.30.

## A/B mode

Run the same corpus against v1 and v2 in sequence and compare pass/fail counts:

```bash
JUDGE_PROMPT_VERSION=v1 pytest -m prompt_quality 2>&1 | tee /tmp/v1_eval.txt
JUDGE_PROMPT_VERSION=v2 pytest -m prompt_quality 2>&1 | tee /tmp/v2_eval.txt
diff /tmp/v1_eval.txt /tmp/v2_eval.txt
```

A v2 prompt edit must not introduce regressions on any fixture that v1 passes.

## Note on v1 compatibility

v1 prompt does not emit the required `JudgeOutput.reasoning` field (added in
Cluster 1). Running the eval with `JUDGE_PROMPT_VERSION=v1` will cause the
JudgeService to fall back on validation_error and every fixture will report
"fallback path fired: validation_error". This is expected — v1 is incompatible
with the v2 schema. v1 is rollback-only after the v2 cutover.
