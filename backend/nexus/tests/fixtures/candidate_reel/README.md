# Candidate Reel — test fixtures

## `session_5e004a4d_transcript.json`

A real `sessions.transcript` column captured from a full live interview
(session `5e004a4d-e1a5-4165-9fd8-1e2e6a6438f6`, 2026-06-02) — the session that
verified the Phase 1 word-timing capture end-to-end. It exists so Phase 2 (the
reel director) can be built and tested against realistic word-timed data
**without running a new live session**.

Shape = the raw `sessions.transcript` JSON array (drop-in for `json.load`):

- 62 entries: 34 candidate turns, 28 agent turns.
- **Candidate** turns carry `start_ms`, `end_ms`, and a `words[]` list of
  `{text, start_ms, end_ms, confidence}` (session-clock ms).
- **Agent** turns carry `words: null` (TTS, no STT word timing) — by design.
- `confidence` is always `1.0`: the LiveKit Deepgram plugin doesn't forward
  per-word confidence, so the engine defaults it. Treat it as non-informative.

### Known caveats (read before building against this)

1. **`words` can be a superset of `text`.** A turn's `words[]` may include a
   leading acknowledgment (e.g. "sure") that the committed `text` omits. Treat
   the **word timeline as the source of truth** for clip in/out + captions; do
   not assume an exact `text` ↔ `words` correspondence.
2. **Turn 1 predates the floor-gate fix.** This capture was taken just before
   the word-buffer floor gate landed (words spoken while the agent holds the
   floor are no longer buffered). Turn 1 (`candidate`, "I would say… four to
   five years…") therefore has a stray leading `"sure"` word — a backchannel
   spoken over the agent's opener — which skews that turn's `start_ms` ~4.8s
   early. New sessions no longer exhibit this; it's preserved here as a
   realistic worst-case input for Phase 2 robustness.
