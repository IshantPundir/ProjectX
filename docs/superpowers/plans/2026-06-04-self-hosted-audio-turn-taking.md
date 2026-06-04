# Self-Hosted Audio & Turn-Taking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the interview engine portable off LiveKit-Cloud-only features (adaptive interruption + ai-coustics) and stop over-suppressing candidate audio, while adding two default-on dev toggles so tuning runs don't burn tokens/compute.

**Architecture:** Drop adaptive interruption (→ VAD-mode barge-in), delete server-side ai-coustics NC, swap the VAD to Silero (open-source, prewarm-loaded), flip browser `noiseSuppression` back on. Two independent `bool` settings gate the report LLM scorer and the vision gaze analysis at their single enqueue sites. The `MultilingualModel` turn detector, STT, and TTS are untouched.

**Tech Stack:** Python 3.13, FastAPI, LiveKit Agents 1.5.x (`livekit-plugins-silero`), Dramatiq, pytest (Docker), pydantic-settings; frontend Next.js/vitest (no change).

**Spec:** `docs/superpowers/specs/2026-06-04-self-hosted-audio-turn-taking-design.md`

**Test command (all tasks):** Backend runs in Docker.
`docker compose run --rm nexus pytest <path> -v`
(Rebuild the image — `docker compose build nexus` — only after Task 3 changes dependencies.)

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `app/config.py` | `Settings` — add 2 toggles; remove NC config | 1, 2, 3 |
| `app/ai/config.py` | `AIConfig` wrapper — remove NC properties | 3 |
| `app/ai/realtime.py` | plugin factories — interruption `mode=vad`, Silero VAD, delete NC | 3 |
| `app/modules/interview_engine/agent.py` | Silero registration import + prewarm-load + run() wiring; drop NC | 3 |
| `app/modules/interview_runtime/service.py` | gate report enqueue | 1 |
| `app/modules/session/recording.py` | gate vision enqueue | 2 |
| `app/modules/session/service.py` + `schemas.py` | flip `noise_suppression` hint + docstrings | 4 |
| `backend/nexus/pyproject.toml` + `uv.lock` | −ai-coustics, +silero | 3 |
| `backend/nexus/.env.example` | remove NC var; add 2 toggles | 1, 2, 3 |
| docs + stale comments | CLAUDE.md ×2, threat-model, etc. | 5 |

Tasks 1, 2, 4 are independent and low-risk (ship first — they unblock token-free testing). Task 3 is the atomic audio core (deps + code must land together to stay importable). Task 5 is docs. Task 6 is operator validation.

---

## Task 1: `AUTO_SCORE_SESSION_REPORTS` toggle

**Files:**
- Modify: `app/config.py` (add setting)
- Modify: `app/modules/interview_runtime/service.py` (import `settings`; gate the enqueue at lines 455–484)
- Modify: `backend/nexus/.env.example`
- Test: `tests/interview_runtime/integration/test_record_session_result_enqueue_isolation.py` (add one test, reusing existing helpers)

- [ ] **Step 1: Write the failing test** — append to the existing file (it already has `_seed_active_session`, `_result`, and imports `reporting`, `record_session_result`, `SessionRow`, `select`):

```python
@pytest.mark.asyncio
async def test_no_enqueue_when_auto_score_disabled(db, monkeypatch) -> None:
    """With AUTO_SCORE_SESSION_REPORTS off, the session still completes durably
    but report scoring is NOT enqueued (token-saving dev/test toggle)."""
    from app.config import settings

    session_id, tenant_id = await _seed_active_session(db)
    calls: list[tuple] = []
    monkeypatch.setattr(
        reporting.score_session_report, "send",
        lambda *a, **k: calls.append((a, k)),
    )
    monkeypatch.setattr(settings, "auto_score_session_reports", False)

    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=_result(session_id),
        correlation_id="corr-disabled",
    )

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.state == "completed"   # completion is durable regardless
    assert calls == []                # but nothing was enqueued
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/integration/test_record_session_result_enqueue_isolation.py::test_no_enqueue_when_auto_score_disabled -v`
Expected: FAIL — either `AttributeError: ... 'auto_score_session_reports'` (monkeypatch on a missing attr) or the assertion `calls == []` fails because the enqueue still fires.

- [ ] **Step 3: Add the setting** in `app/config.py` (near the other engine/interview settings, e.g. just after the report/engine block). Insert:

```python
    # Dev/test ergonomics — leave True in every real environment. Set False
    # locally to skip the post-session report LLM scorer during agent tuning
    # runs (saves tokens). Non-destructive: the session still completes and
    # persists coverage_summary, so it stays re-scorable via the manual endpoint.
    auto_score_session_reports: bool = True
```

- [ ] **Step 4: Add the gate** in `app/modules/interview_runtime/service.py`. First add the import near the top (with the other `from app.*` imports):

```python
from app.config import settings
```

Then replace the existing enqueue block (currently `if result.coverage_summary is not None:` at ~line 462) with:

```python
    if not settings.auto_score_session_reports:
        logger.info(
            "interview_runtime.record_session_result.report_scoring_disabled",
            session_id=str(session_id),
            tenant_id=str(tenant_id),
            correlation_id=correlation_id,
            reason="auto_score_session_reports=false",
        )
    elif result.coverage_summary is not None:
        try:
            from app.modules.reporting import score_session_report  # noqa: PLC0415

            score_session_report.send(
                str(session_id),
                str(tenant_id),
                correlation_id,
            )
            logger.info(
                "interview_runtime.record_session_result.report_enqueued",
                session_id=str(session_id),
                tenant_id=str(tenant_id),
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001 — enqueue is best-effort; completion is durable
            logger.warning(
                "interview_runtime.record_session_result.report_enqueue_failed",
                session_id=str(session_id),
                tenant_id=str(tenant_id),
                correlation_id=correlation_id,
                exc_info=True,
            )
```

(Only the outer `if not ... :` branch and the `elif` are new; the body of the `elif` is the unchanged original block.)

- [ ] **Step 5: Run the test + the existing two to verify all pass**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/integration/test_record_session_result_enqueue_isolation.py -v`
Expected: PASS (3 tests — the new one plus the 2 original isolation tests still green).

- [ ] **Step 6: Document the flag** in `backend/nexus/.env.example` (add near the engine config):

```
# Dev/test ergonomics — leave TRUE in every real environment.
# Set FALSE locally to skip post-session report scoring during agent tuning (saves tokens).
AUTO_SCORE_SESSION_REPORTS=true
```

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/modules/interview_runtime/service.py backend/nexus/.env.example \
        tests/interview_runtime/integration/test_record_session_result_enqueue_isolation.py
git commit -m "feat(engine): AUTO_SCORE_SESSION_REPORTS toggle to skip report scoring in test runs"
```

---

## Task 2: `AUTO_ANALYZE_PROCTORING` toggle

**Files:**
- Modify: `app/config.py` (add setting)
- Modify: `app/modules/session/recording.py` (gate `_enqueue_vision_analysis`, ~line 125 — `settings` and `log` are already imported)
- Modify: `backend/nexus/.env.example`
- Test: `tests/vision/test_recording_enqueue.py` (add two tests)

- [ ] **Step 1: Write the failing tests** — append to the existing file (it already imports `pytest`, `MagicMock`, and `from app.modules.session import recording as rec`):

```python
def test_no_send_when_proctoring_disabled(monkeypatch):
    """AUTO_ANALYZE_PROCTORING off => the vision actor is never enqueued."""
    import app.modules.vision as vision
    from app.config import settings

    monkeypatch.setattr(settings, "auto_analyze_proctoring", False)
    send = MagicMock()
    monkeypatch.setattr(vision.analyze_session_proctoring, "send", send)

    rec._enqueue_vision_analysis("sid-1", "tid-1")
    send.assert_not_called()


def test_send_when_proctoring_enabled(monkeypatch):
    """AUTO_ANALYZE_PROCTORING on (default) => the vision actor is enqueued."""
    import app.modules.vision as vision
    from app.config import settings

    monkeypatch.setattr(settings, "auto_analyze_proctoring", True)
    send = MagicMock()
    monkeypatch.setattr(vision.analyze_session_proctoring, "send", send)

    rec._enqueue_vision_analysis("sid-1", "tid-1")
    send.assert_called_once_with("sid-1", "tid-1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/vision/test_recording_enqueue.py::test_no_send_when_proctoring_disabled tests/vision/test_recording_enqueue.py::test_send_when_proctoring_enabled -v`
Expected: FAIL — `test_no_send_when_proctoring_disabled` fails because the actor is still sent (gate doesn't exist yet); the `settings.auto_analyze_proctoring` monkeypatch raises `AttributeError`.

- [ ] **Step 3: Add the setting** in `app/config.py` (right after `auto_score_session_reports`):

```python
    # Dev/test ergonomics — leave True in every real environment. Set False
    # locally to skip the post-session vision gaze analysis (heavy CPU/GPU) during
    # agent tuning runs. Non-destructive: the recording is still produced, so the
    # analysis can be re-run later from the report page.
    auto_analyze_proctoring: bool = True
```

- [ ] **Step 4: Add the gate** in `app/modules/session/recording.py` — `_enqueue_vision_analysis` (~line 125). `settings` (line 26) and `log` (line 32) are already imported:

```python
def _enqueue_vision_analysis(session_id: str, tenant_id: str) -> None:
    if not settings.auto_analyze_proctoring:
        log.info(
            "session.recording.vision_analysis_disabled",
            session_id=session_id,
            reason="auto_analyze_proctoring=false",
        )
        return
    # Imported here (not module top) to keep the import graph obviously light
    # and to make monkeypatching in tests trivial.
    from app.modules.vision import analyze_session_proctoring

    analyze_session_proctoring.send(session_id, tenant_id)
```

- [ ] **Step 5: Run the full file to verify all pass**

Run: `docker compose run --rm nexus pytest tests/vision/test_recording_enqueue.py -v`
Expected: PASS (the original 5 enqueue tests + the 2 new ones).

- [ ] **Step 6: Document the flag** in `backend/nexus/.env.example`:

```
# Set FALSE locally to skip the post-session vision gaze analysis (saves CPU/GPU).
AUTO_ANALYZE_PROCTORING=true
```

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/modules/session/recording.py backend/nexus/.env.example \
        tests/vision/test_recording_enqueue.py
git commit -m "feat(engine): AUTO_ANALYZE_PROCTORING toggle to skip vision analysis in test runs"
```

---

## Task 3: Audio core — Silero VAD + VAD-mode interruption + remove server NC

This is **one atomic task**: the dependency swap and the code swap must land in the same commit (deleting `build_noise_cancellation` while `agent.py`/`ai/config.py`/`config.py` still reference NC would leave an unimportable tree).

**Files:**
- Modify: `backend/nexus/pyproject.toml` (line 81) + regenerate `uv.lock`
- Modify: `app/ai/realtime.py` (interruption `mode`; delete `build_noise_cancellation`; `build_vad`→Silero; fix stale comment line 73)
- Modify: `app/ai/config.py` (drop `NoiseCancellationMode` import + 2 properties)
- Modify: `app/config.py` (drop `NoiseCancellationMode` Literal + 2 fields + "locked to LK Cloud" comment)
- Modify: `app/modules/interview_engine/agent.py` (Silero registration import; prewarm-load; run() wiring; drop NC)
- Modify: `backend/nexus/.env.example` (remove `INTERVIEW_NOISE_CANCELLATION` block, ~lines 226–230)
- Test: `tests/ai/test_realtime_factories.py`

- [ ] **Step 1: Swap the dependency** in `backend/nexus/pyproject.toml`. Remove the line:

```
    "livekit-plugins-ai-coustics>=0.2,<1",
```

and add (next to the other first-party plugins):

```
    "livekit-plugins-silero>=1.5.4,<2",
```

- [ ] **Step 2: Regenerate the lockfile and rebuild the image**

Run:
```bash
docker compose run --rm nexus uv lock
docker compose build nexus
```
Expected: `uv.lock` updates (ai-coustics removed, silero added); image builds. The Dockerfile's `download-files` step (line 52) will bake the Silero ONNX weights because Step 5 adds the registration import.

- [ ] **Step 3: Update the factory tests (TDD red)** in `tests/ai/test_realtime_factories.py`:

  (a) Change the import line to drop `build_noise_cancellation`:
```python
from app.ai.realtime import (
    build_interruption_options,
    build_stt_plugin,
    build_tts_plugin,
    build_vad,
)
```
  (b) Replace `TestBuildInterruptionOptions` with:
```python
class TestBuildInterruptionOptions:
    def test_returns_vad_mode_with_gates(self) -> None:
        opts = build_interruption_options()
        assert opts == {
            "mode": "vad",
            "min_duration": 1.0,
            "min_words": 2,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        }
```
  (c) **Delete** the entire `class TestBuildNoiseCancellation` (3 methods).
  (d) Replace `class TestBuildVad` with a Silero assertion that mocks the heavy load:
```python
class TestBuildVad:
    def test_returns_silero_vad(self) -> None:
        from unittest.mock import MagicMock, patch

        with patch("livekit.plugins.silero.VAD.load", return_value=MagicMock()) as load:
            result = build_vad()
        assert result is not None
        load.assert_called_once()
        assert "livekit.plugins.silero" in sys.modules
```

- [ ] **Step 4: Run the factory tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/ai/test_realtime_factories.py -v`
Expected: FAIL — `ImportError` is gone after the import edit, but `test_returns_vad_mode_with_gates` fails (`mode` is still `"adaptive"`) and `test_returns_silero_vad` fails (`build_vad` still returns ai-coustics VAD).

- [ ] **Step 5: Edit `app/ai/realtime.py`**:

  (a) `build_interruption_options()` — flip the mode + docstring:
```python
def build_interruption_options() -> dict[str, object]:
    """Construct the `interruption=` block for TurnHandlingOptions.

    VAD-based barge-in — self-hostable, no LiveKit-Cloud dependency. The word-count
    and duration gates filter backchannel/noise; false-interruption recovery resumes
    the agent's line if no transcript follows.
    """
    logger.info("ai.realtime.interruption.built", mode="vad")
    return {
        "mode": "vad",
        "min_duration": 1.0,
        "min_words": 2,
        "false_interruption_timeout": 2.0,
        "resume_false_interruption": True,
    }
```
  (b) **Delete** the entire `build_noise_cancellation()` function.
  (c) Replace `build_vad()` with Silero (also fixes the stale ai-coustics comment at line 73 if it sits in this function — otherwise update that comment to reference Silero):
```python
def build_vad() -> object:
    """Construct the Silero VAD. Blocking ONNX model load — call from prewarm()."""
    from livekit.plugins import silero

    logger.info("ai.realtime.vad.built", provider="silero")
    return silero.VAD.load()
```
  (d) If the comment at `realtime.py:73` reads "...does not race with our ai-coustics VAD", update it to "...does not race with our Silero VAD".

- [ ] **Step 6: Remove the NC config — `app/ai/config.py`**:
  - Remove `NoiseCancellationMode` from the `from app.config import (...)` line (line 40).
  - Delete the `interview_noise_cancellation` property (lines 135–136).
  - Delete the `interview_nc_enhancement_level` property (lines 139–140).

- [ ] **Step 7: Remove the NC config — `app/config.py`**:
  - Delete the `NoiseCancellationMode = Literal[...]` block (lines 6–9).
  - Delete the comment block "Architecture is locked to LK Cloud + ai-coustics exclusively..." (lines ~409–414) and the two fields `interview_noise_cancellation` and `interview_nc_enhancement_level`.

- [ ] **Step 8: Wire Silero into `app/modules/interview_engine/agent.py`**:
  (a) Add the registration import at module top, immediately after the existing turn-detector import (line 50):
```python
from livekit.plugins import silero as _silero_vad  # noqa: F401  — register for download-files
```
  (b) In the import block from `app.ai.realtime` (lines 63–71), remove `build_noise_cancellation`.
  (c) In `prewarm(proc)` (after the OTel block), add:
```python
    proc.userdata["vad"] = build_vad()
    log.info("engine.vad.prewarmed", provider="silero")
```
  (d) In `run()`, change `vad=build_vad()` (line 690) to:
```python
        vad=ctx.proc.userdata["vad"],
```
  (e) In `run()` `session.start(...)` (lines 1029–1036), delete `nc_filter = build_noise_cancellation()` and remove the `audio_input=room_io.AudioInputOptions(noise_cancellation=nc_filter)` argument, leaving:
```python
    await session.start(
        agent=agent, room=ctx.room,
        room_options=room_io.RoomOptions(
            delete_room_on_close=True,
        ),
    )
```

- [ ] **Step 9: Remove the NC env var** from `backend/nexus/.env.example` — delete the block (~lines 226–230) containing the "adaptive interruption + ai-coustics" comment and `INTERVIEW_NOISE_CANCELLATION=ai_coustics_quail`.

- [ ] **Step 10: Run the factory tests + an import smoke check**

Run:
```bash
docker compose run --rm nexus pytest tests/ai/test_realtime_factories.py -v
docker compose run --rm nexus python -c "import app.modules.interview_engine.agent; import app.ai.config; import app.config; print('import OK')"
```
Expected: factory tests PASS; the import smoke prints `import OK` (confirms no dangling `NoiseCancellationMode` / `build_noise_cancellation` / ai-coustics references).

- [ ] **Step 11: Grep for any leftover ai-coustics code references**

Run: `docker compose run --rm nexus bash -lc "grep -rn 'ai_coustics\|build_noise_cancellation\|NoiseCancellationMode\|interview_noise_cancellation\|interview_nc_enhancement_level' app/ tests/ || echo CLEAN"`
Expected: `CLEAN` (no matches in `app/` or `tests/`). (Comments in `reel/timing.py` and docs are handled in Task 5; the migration `0028` docstring is intentionally left.)

- [ ] **Step 12: Commit**

```bash
git add backend/nexus/pyproject.toml uv.lock app/ai/realtime.py app/ai/config.py app/config.py \
        app/modules/interview_engine/agent.py backend/nexus/.env.example \
        tests/ai/test_realtime_factories.py
git commit -m "feat(engine): drop adaptive interruption + ai-coustics, adopt Silero VAD

interruption mode=vad; Silero VAD prewarm-loaded + registered for
download-files; server-side NC removed across both config layers."
```

---

## Task 4: Flip browser `noiseSuppression` to true

**Files:**
- Modify: `app/modules/session/service.py` (`_compute_audio_processing_hints`, line 80 + docstring)
- Modify: `app/modules/session/schemas.py` (`AudioProcessingHints` docstring, lines 69–87)
- Test: `tests/test_audio_hints.py`

- [ ] **Step 1: Update the test (TDD red)** — replace the test in `tests/test_audio_hints.py`:

```python
def test_audio_hints_enable_browser_noise_suppression() -> None:
    """No server-side NC: the browser handles light noise suppression locally.
    EC stays on (full-duplex barge-in); AGC stabilizes input level."""
    hints = _compute_audio_processing_hints()
    assert hints == AudioProcessingHints(
        noise_suppression=True,
        echo_cancellation=True,
        auto_gain_control=True,
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_audio_hints.py -v`
Expected: FAIL — current value is `noise_suppression=False`.

- [ ] **Step 3: Flip the hint + docstring** in `app/modules/session/service.py`:

```python
def _compute_audio_processing_hints() -> AudioProcessingHints:
    """Browser-side audio constraints for the candidate session.

    No server-side noise cancellation: the browser's built-in noise suppression
    handles the (mandated-quiet) ambient case. Echo cancellation is load-bearing
    for full-duplex barge-in; AGC stabilizes the input dynamic range.
    """
    return AudioProcessingHints(
        noise_suppression=True,
        echo_cancellation=True,
        auto_gain_control=True,
    )
```

- [ ] **Step 4: Update the schema docstring** in `app/modules/session/schemas.py` (`AudioProcessingHints`, lines 77–79) to:

```python
      noise_suppression=True   (browser handles light NS; no server-side NC)
      echo_cancellation=True   (load-bearing for full-duplex barge-in)
      auto_gain_control=True   (stabilizes input dynamic range)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_audio_hints.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules/session/service.py app/modules/session/schemas.py tests/test_audio_hints.py
git commit -m "feat(session): enable browser noiseSuppression (no server-side NC)"
```

---

## Task 5: Docs, stale comments & compliance

No tests; verification is a clean grep + a human read. Each edit below is mechanical.

- [ ] **Step 1: Fix the stale comment in `app/modules/reel/timing.py:10`** — change "ai-coustics NC + VAD" to "Silero VAD (no server-side NC)". (The timing logic is unaffected — this is a docstring describing pipeline lag.)

- [ ] **Step 2: Root `CLAUDE.md` → "Audio Path" section** — in the constraints table, change `noiseSuppression` from **false** to **true** and update its rationale to "browser handles light NS; no server-side NC". Remove the "Architecture is locked to LK Cloud (no self-hosted fallback)" paragraph's claim about adaptive interruption + ai-coustics; note barge-in is now VAD-mode and the VAD is Silero.

- [ ] **Step 3: `backend/nexus/CLAUDE.md`** — update the Phase 3D.audio-pipeline bullet and the `app/ai/realtime.py` description: remove ai-coustics (NC + VAD) and adaptive interruption; state interruption is `mode="vad"`, VAD is Silero (prewarm-loaded + registered for download-files), `build_noise_cancellation` is gone. Add a one-line note documenting the two new dev toggles (`AUTO_SCORE_SESSION_REPORTS`, `AUTO_ANALYZE_PROCTORING`).

- [ ] **Step 4: `docs/security/threat-model.md`** — remove **ai-coustics** as an audio-path sub-processor (candidate audio no longer transits an external NC processor — a net reduction in data-path surface). Specifically update: the ai-coustics sub-processor entry (lines ~67–68); the data-flow line "`noiseSuppression` OFF … → ai-coustics QUAIL_L" (line ~74); the "`noiseSuppression` is **false** … avoids double-denoising" rationale (lines ~78–79) → now `noiseSuppression` is **true** (browser-side light NS; no server NC, so no double-denoising concern); the engine-installs-`livekit-plugins-ai-coustics`/`build_noise_cancellation` paragraph (lines ~84–90) → Silero VAD, no NC plugin; and the bystander-speech-disclosure row crediting "QUAIL_L provides server-side NS before STT" (line ~100) → there is no server-side NS now; bystander mitigation rests on the quiet-environment mandate + consent + event-log redaction.

- [ ] **Step 4b: `frontend/session/AGENTS.md`** (review-surfaced — authoritative agent instructions, MUST fix or a future agent re-breaks it). Lines ~15/19/21: the rule still mandates `noise_suppression` is "always `false`" and a `{ noiseSuppression: false, ... }` fallback (which now contradicts BOTH the new backend default and the actual `app.tsx` fallback). Update the "Why" to: no server-side NC; the server sets `noise_suppression: true` (browser does light NS); EC/AGC stay true. Update the missing-hints fallback to `{ noiseSuppression: true, echoCancellation: true, autoGainControl: true }`. Keep the core rule intact ("the server decides — read `audio_processing_hints`; do not hard-code").

- [ ] **Step 4c: `frontend/session/components/interview/app/app.tsx`** (comment-only — the runtime fallback at lines ~91–95 is ALREADY `noise_suppression: true`, no code change). Fix the stale comment at lines ~80–82 ("Cloud mode sets noise_suppression=false so the ML model sees raw audio") → server sets `noise_suppression: true`; browser does light NS; EC/AGC stay on.

- [ ] **Step 5: `docs/superpowers/specs/2026-05-06-audio-pipeline-design.md`** — add a header note at the top: `> Superseded (audio path) by docs/superpowers/specs/2026-06-04-self-hosted-audio-turn-taking-design.md (2026-06-04).` Do not rewrite the body (history).

- [ ] **Step 6: `docs/deployment/2026-06-03-deployment-architecture-research.md`** — in the staged-decision table / near-term actions, mark "Step 1 — decouple Cloud *features*" as **done** (adaptive interruption removed → VAD; ai-coustics removed entirely, so the "own key" sub-item is moot).

- [ ] **Step 7: Frontend comment/test tidy** (review-surfaced; do it — these are now contradictory, not merely cosmetic). In `frontend/session/lib/api/audio-hints.ts` update the docstring referencing "Cloud mode … ai-coustics is not an EC" to describe the self-hosted reality (server sets NS true; the mapper is a pure passthrough). In `frontend/session/tests/lib/api/audio-hints.test.ts` rename the misleading label "cloud mode (server NC on) sets browser noiseSuppression to false" (e.g. to "maps server-provided hints (NS off legacy input)") — keep BOTH mapping test cases (they exercise the pure mapper for both values; don't delete coverage). Run `cd frontend/session && npm run test -- audio-hints` to confirm green.

- [ ] **Step 8: Verify docs are clean of stale runtime claims**

Run: `grep -rn "ai-coustics\|ai_coustics\|adaptive interruption" CLAUDE.md backend/nexus/CLAUDE.md docs/security/threat-model.md || echo CLEAN`
Expected: matches only in historical/spec docs (the 2026-05-06 spec body, deployment research, this spec/plan, migration notes) — **not** in the two CLAUDE.md files or the threat-model's active sub-processor list.

- [ ] **Step 9: Commit** (run `git -C <repo root>` paths; the reel/timing.py + CLAUDE.md paths below are repo-root-relative)

```bash
# from repo root /home/ishant/Projects/ProjectX
git add CLAUDE.md backend/nexus/CLAUDE.md docs/security/threat-model.md \
        docs/superpowers/specs/2026-05-06-audio-pipeline-design.md \
        docs/deployment/2026-06-03-deployment-architecture-research.md \
        docs/superpowers/plans/2026-06-04-self-hosted-audio-turn-taking.md \
        backend/nexus/app/modules/reel/timing.py \
        frontend/session/AGENTS.md \
        frontend/session/components/interview/app/app.tsx \
        frontend/session/lib/api/audio-hints.ts \
        frontend/session/tests/lib/api/audio-hints.test.ts
git commit -m "docs: reflect self-hosted audio path (Silero VAD, no server NC, VAD barge-in)"
```

---

## Task 6: Operator validation (manual — required acceptance gate)

These cannot be automated; they are the sign-off for the change.

- [ ] **Step 1: Build & boot the engine**

Run: `docker compose up -d --build nexus-engine`
Then check the logs for a single `engine.vad.prewarmed provider=silero` line and **no** `ai_coustics` import error.

- [ ] **Step 2: Confirm the Silero model was baked**

Run: `docker compose run --rm nexus bash -lc "python -m app.modules.interview_engine download-files && echo DOWNLOAD_OK"`
Expected: completes without network errors (weights already present from the image build); prints `DOWNLOAD_OK`.

- [ ] **Step 3: Live talk-test (the barge-in acceptance gate)** — run a real candidate session and confirm:
  - The agent is cleanly interruptible when you speak ≥2 words over it.
  - It does NOT yield to single-word backchannel ("mm", "yeah").
  - After a false interruption (brief noise, no real speech) it resumes its line.
  - No audible over-suppression / clipping of your own voice.

- [ ] **Step 4: Toggle smoke** — with `AUTO_SCORE_SESSION_REPORTS=false` and `AUTO_ANALYZE_PROCTORING=false` in `.env`, complete a session and confirm:
  - Engine logs `report_scoring_disabled` and `vision_analysis_disabled`.
  - No `report_scoring` / `vision` job runs.
  - The session row still reaches `completed` with `coverage_summary` populated (re-scorable later).

- [ ] **Step 5: Finalize the branch** — once validation passes, use the `superpowers:finishing-a-development-branch` skill to merge/PR.

---

## Notes for the implementer

- **Out of scope (do NOT touch):** the `MultilingualModel` turn detector / `build_turn_detector`, STT (Deepgram), TTS (Sarvam), `engine_v2_endpointing_max_delay`, and the hold-space cue. The EOU over-hold / "take your time" UX bug is a **separate, immediately-following spec** — adding its fixes here would break the surgical scope.
- The two toggles default `True`, so production behavior is byte-for-byte unchanged after this plan.
- Task 3 is the only task that changes dependencies; rebuild the image once at Task 3 Step 2 and reuse it for the rest.
