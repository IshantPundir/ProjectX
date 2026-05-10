# Sarvam STT/TTS — provider-switchable audio pipeline

**Status:** Draft for user review · **Date:** 2026-05-10

## Summary

Replace Deepgram (STT) and OpenAI `gpt-4o-mini-tts` (TTS) with [Sarvam](https://sarvam.ai/) as the **default** realtime STT and TTS providers for the interview engine, while keeping the existing providers wired and selectable via `.env` for fallback.

Why Sarvam: the candidate population for these interviews is Indian-English-speaking, often code-switching to Hindi mid-sentence. Sarvam's `saaras:v3` STT is purpose-built for that distribution, and `bulbul:v3` TTS produces natural Indian-English voices that match candidate expectations. Deepgram and OpenAI's TTS are both strong on en-US but degrade on heavy Indian-English accents and code-mix.

The change is contained to the `app/ai/realtime.py` plugin-factory carve-out and the env-driven `Settings` / `AIConfig` surface — every other layer of the audio pipeline (VAD, end-of-utterance turn detector, noise cancellation, adaptive interruption, browser audio constraints, endpointing delays) is **untouched**, which is what preserves the recently-tuned VAD + EOU behavior.

## Non-goals

- **Sarvam LLM.** The realtime conversational LLM stays on `gpt-5.3-chat-latest` via OpenAI. The realtime LLM swap surface is unrelated to this spec.
- **Per-session / per-tenant language selection.** `SessionConfig` has no language field today. Adding one would require schema and bank-generation changes that aren't motivated yet — deferred until a tenant requests a non-`en-IN` interview.
- **Replacing the multilingual turn detector.** `livekit-plugins-turn-detector.MultilingualModel` is already language-agnostic and feeds off the upstream PCM, not the STT result. No change needed.
- **VAD swap.** `ai_coustics.VAD()` continues to provide voice activity detection — it operates on PCM frames upstream of any STT, so STT-provider choice has no bearing on it.

## Architecture

### The pattern this extends

`app/ai/realtime.py` already implements provider-switchable TTS:

```python
def build_tts_plugin() -> "_BaseTTS":
    provider = ai_config.interview_tts_provider  # Literal["openai", "cartesia"]
    if provider == "openai":
        return _build_tts_openai()
    if provider == "cartesia":
        return _build_tts_cartesia()
    raise ValueError(...)
```

This spec mirrors that pattern for STT, and adds a third branch (`sarvam`) to TTS.

### After this spec

```python
# build_stt_plugin() — new dispatcher
provider = ai_config.interview_stt_provider  # Literal["sarvam", "deepgram"]
if provider == "sarvam":
    return _build_stt_sarvam()
if provider == "deepgram":
    return _build_stt_deepgram()

# build_tts_plugin() — extended dispatcher
provider = ai_config.interview_tts_provider  # Literal["sarvam", "openai", "cartesia"]
if provider == "sarvam":
    return _build_tts_sarvam()
if provider == "openai":
    return _build_tts_openai()
if provider == "cartesia":
    return _build_tts_cartesia()
```

`stt_factory.build_stt_plugin_for_session()` (the per-session keyterm seam) is unchanged; it still delegates to `build_stt_plugin()`. The existing pass-through test stays valid.

### What is *not* touched (preserves VAD + end-of-sentence)

The user's stated requirement is "without breaking VAD and end-of-sentence capabilities." Below is the full list of audio-pipeline surfaces, with the change posture for each:

| Surface | File / function | Status under this spec |
|---|---|---|
| Voice activity detection | `app/ai/realtime.py::build_vad()` → `ai_coustics.VAD()` | **Unchanged.** VAD operates on PCM frames upstream of STT; STT-provider choice cannot affect it. |
| End-of-utterance turn detection | `app/ai/realtime.py::build_turn_detector()` → `livekit.plugins.turn_detector.multilingual.MultilingualModel` | **Unchanged.** The model takes both VAD signals and STT *interim text* as input; both Deepgram and Sarvam emit standard `livekit.agents.stt.STT` events, so the turn detector's view is identical regardless of STT provider. |
| Endpointing min/max delays | `settings.engine_endpointing_min_delay` (1.0s), `engine_endpointing_max_delay` (6.0s) | **Unchanged.** Plumbed through `TurnHandlingOptions`, independent of STT. |
| Noise cancellation | `app/ai/realtime.py::build_noise_cancellation()` → `ai_coustics.audio_enhancement(QUAIL_L, ...)` | **Unchanged.** Browser → LK Cloud → ai-coustics enhancement → STT pipeline order is preserved. |
| Adaptive interruption | `app/ai/realtime.py::build_interruption_options()` (`mode="adaptive"`, `min_words=2`) | **Unchanged.** LK Cloud's barge-in classifier reads the STT word stream same as before. |
| Browser audio constraints | `audio_processing_hints` on `/start` response (`noiseSuppression: false`, `echoCancellation: true`, `autoGainControl: true`) | **Unchanged.** |

A specific risk worth calling out: Sarvam STT exposes a `high_vad_sensitivity` parameter that runs an *internal* VAD inside the STT pipeline. That would race with our ai-coustics VAD if enabled. **It is left unset (None)** so the STT does not introduce a second VAD into the path.

## Defaults (en-IN, code-mix capable)

Per the brainstorming answer, the default profile is Indian English with code-mix capability:

| Setting | Default | Notes |
|---|---|---|
| `INTERVIEW_STT_PROVIDER` *(new)* | `sarvam` | Literal `{"sarvam","deepgram"}` |
| `INTERVIEW_STT_MODEL` | `saaras:v3` | was `nova-3` for Deepgram. `saaras:v3` is Sarvam's recommended model for advanced mode control + broader language support. |
| `INTERVIEW_STT_LANGUAGE` | `en-IN` | was `en` for Deepgram. Sarvam expects BCP-47 with the country variant. |
| `INTERVIEW_STT_MODE` *(new)* | `transcribe` | Sarvam-only knob; ignored when provider=`deepgram`. Allowed values for `saaras:v3`: `transcribe`, `translate`, `verbatim`, `translit`, `codemix`. `codemix` is available for English+Hindi mixed output if real sessions show transliteration is needed. |
| `INTERVIEW_TTS_PROVIDER` | `sarvam` | was `openai`. Literal `{"sarvam","openai","cartesia"}`. |
| `INTERVIEW_TTS_MODEL` | `bulbul:v3` | was `gpt-4o-mini-tts`. |
| `INTERVIEW_TTS_VOICE` | `shubh` | Sarvam's `speaker` arg. The same `INTERVIEW_TTS_VOICE` env var carries the OpenAI voice preset, the Cartesia voice UUID, and (now) the Sarvam speaker name. The plugin factory rejects mismatched values at construction — same posture as today. |
| `INTERVIEW_TTS_LANGUAGE` | `en-IN` | passed to Sarvam as `target_language_code` (required). Ignored by OpenAI TTS, used by Cartesia. |
| `INTERVIEW_TTS_PACE` *(new)* | `1.0` | Sarvam-only knob. Range 0.5–2.0. Ignored by OpenAI/Cartesia. |
| `INTERVIEW_TTS_TEMPERATURE` *(new)* | `0.6` | Sarvam-only knob (only used by `bulbul:v3`/`bulbul:v3-beta`). |
| `SARVAM_API_KEY` | (existing in `.env`) | Plumbed into Settings; passed explicitly to both `sarvam.STT(...)` and `sarvam.TTS(...)`. |

## Files to change

| File | Change |
|---|---|
| `backend/nexus/pyproject.toml` | Add `livekit-plugins-sarvam~=1.5`. Keep `livekit-plugins-deepgram`, `livekit-plugins-cartesia`, `livekit-plugins-openai` installed (still selectable). |
| `backend/nexus/app/config.py` | Add `sarvam_api_key`. Add `interview_stt_provider: Literal["sarvam","deepgram"] = "sarvam"`. Widen `interview_tts_provider` Literal to `["sarvam","openai","cartesia"]` and flip default to `sarvam`. Update `interview_stt_model`/`interview_tts_model`/`interview_tts_voice`/etc. defaults to the Sarvam values. Add `interview_stt_mode`, `interview_tts_pace`, `interview_tts_temperature`. |
| `backend/nexus/app/ai/config.py` | Expose new fields on `AIConfig` (`interview_stt_provider`, `interview_stt_mode`, `interview_tts_pace`, `interview_tts_temperature`). |
| `backend/nexus/app/ai/realtime.py` | Add `_build_stt_sarvam()` + `_build_stt_deepgram()`; rewrite `build_stt_plugin()` as a dispatcher. Add `_build_tts_sarvam()`; extend `build_tts_plugin()` dispatcher. All Sarvam-plugin imports are lazy (inside the helper) per the existing carve-out discipline. |
| `backend/nexus/.env.example` | Document `INTERVIEW_STT_PROVIDER`, `INTERVIEW_STT_MODE`, Sarvam knobs (`INTERVIEW_TTS_PACE`, `INTERVIEW_TTS_TEMPERATURE`); flip the default block to Sarvam values; rewrite the comment block above the STT/TTS section to describe all three providers. |
| `backend/nexus/app/modules/interview_engine/agent.py` | Audit envelope: `model_versions["stt"]` becomes `f"{provider}/{model}"` for symmetry with the existing `model_versions["tts"]` format. |
| `backend/nexus/tests/ai/test_realtime_factories.py` | Add provider-dispatch tests for STT (sarvam happy path, deepgram happy path, unknown raises) and TTS (sarvam happy path added to the existing openai/cartesia coverage). Mirror the existing `TestBuildNoiseCancellation` pattern (mock `ai_config`, assert plugin module loaded into `sys.modules`). |
| `backend/nexus/CLAUDE.md` | Update the `app/ai/realtime.py` carve-out paragraph to list `sarvam` alongside the existing providers. |

## Risks & rollback

- **Latency budget.** The realtime path has a 1,200ms P50 / 1,500ms P95 budget end-to-end. Sarvam STT supports streaming, and `bulbul:v3` TTS streams over WebSocket, so we expect to stay inside the budget — but the only way to know is to run real sessions and read the new `sessions.audio_tuning_summary` JSONB (already wired by migration 0028). If P95 regresses materially, the `.env` flip back to deepgram/openai is the fast rollback.

- **Provider swap reverts to .env change.** `INTERVIEW_STT_PROVIDER=deepgram` + `INTERVIEW_TTS_PROVIDER=openai` (and matching model/voice values) restores the prior pipeline byte-for-byte. No code change required.

- **Voice mismatch on accidental flip.** If someone flips `INTERVIEW_TTS_PROVIDER` between providers without updating `INTERVIEW_TTS_VOICE`, the plugin factory raises at construction (same posture as today's openai/cartesia mismatch). Fail-fast, no silent fallback.

- **Sarvam outage.** Sarvam is a single vendor. If they go down, the engine cannot dispatch new sessions until the operator flips `INTERVIEW_STT_PROVIDER`/`INTERVIEW_TTS_PROVIDER` back to Deepgram/OpenAI. Acceptable at MVP because Deepgram and OpenAI remain installed and selectable; tracked as a known operational dependency rather than a blocker.

- **Sarvam STT internal VAD.** The plugin's `high_vad_sensitivity` parameter is left unset to avoid layering a second VAD into the path. If a future tuning need surfaces, it's a single-line change at `_build_stt_sarvam()`.

## Validation

- Unit: `tests/ai/test_realtime_factories.py` covers each provider branch.
- Smoke: `docker compose up nexus` starts cleanly with `INTERVIEW_STT_PROVIDER=sarvam` + `INTERVIEW_TTS_PROVIDER=sarvam` set.
- Integration: a real session end-to-end (candidate → engine) plays an opener, asks a question, transcribes a reply, and produces a `sessions.audio_tuning_summary` row. Latency percentiles in that row are the empirical answer to the budget question above.
