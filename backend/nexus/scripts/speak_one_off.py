"""speak_one_off.py — Speaker prompt + Sarvam TTS A/B bench (no LiveKit session).

Usage:
  # Pure TTS A/B on a literal utterance (skips the LLM)
  python scripts/speak_one_off.py --utterance "See — kindly walk me through your design" \\
      --voices shubh,rahul,amit,aditya

  # End-to-end Speaker → TTS on a SpeakerInput JSON file
  python scripts/speak_one_off.py --speaker-input ./inputs/example.json --voices shubh

  # Override model/language/pace/temperature
  python scripts/speak_one_off.py --utterance "Hello." --voices shubh \\
      --model bulbul:v3 --language en-IN --pace 0.95 --temperature 0.6

Outputs:
  /tmp/speak_one_off/<voice>.wav for each voice. Plays each via `aplay`
  if available (Linux) or `afplay` (macOS).

Model / voice compatibility (Sarvam bulbul family):
  bulbul:v2  male:  abhilash, karun, hitesh
             female: anushka, manisha, vidya, arya
  bulbul:v3  male:  shubh, rahul, amit, ratan, rohan, dev, manan, sumit,
                    aditya, kabir, varun, aayan, ashutosh, advait
             female: ritu, pooja, simran, kavya, ishita, shreya, priya,
                     neha, roopa, amelia, sophia

  The script default model is bulbul:v3, default voices shubh,rahul,amit,aditya.
  If you pass --voices manoj or --voices arvind those names are NOT valid for
  bulbul:v3 — use --model bulbul:v2 --voices abhilash,karun,hitesh for v2 voices.

Requires: SARVAM_API_KEY in env (or .env loaded manually). Run with
`uv run` from backend/nexus/ for venv resolution.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


async def _synthesize_to_wav(
    text: str,
    voice: str,
    out_path: Path,
    *,
    model: str,
    language: str,
    pace: float,
    temperature: float,
) -> None:
    """Synthesize ``text`` via Sarvam with the given voice and write a WAV.

    Passes an explicit ``aiohttp.ClientSession`` so the plugin does not try
    to pull one from the LiveKit job context (which does not exist here).
    Uses ``ChunkedStream.collect()`` to get a single combined ``AudioFrame``,
    then calls ``AudioFrame.to_wav_bytes()`` — no manual wave-module math
    needed; the frame knows its own sample rate and channel count.
    """
    import aiohttp
    from livekit.plugins import sarvam

    async with aiohttp.ClientSession() as session:
        tts = sarvam.TTS(
            model=model,
            target_language_code=language,
            speaker=voice,
            pace=pace,
            temperature=temperature,
            http_session=session,
        )
        try:
            stream = tts.synthesize(text)
            # collect() combines all SynthesizedAudio frames into one AudioFrame.
            # AudioFrame.to_wav_bytes() returns a well-formed WAV with the correct
            # RIFF header, sample rate, channel count, and 16-bit PCM body.
            combined_frame = await stream.collect()
            wav_bytes = combined_frame.to_wav_bytes()
        finally:
            await tts.aclose()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(wav_bytes)


async def _speaker_then_text(speaker_input_path: Path) -> str:
    """Run the SpeakerService LLM on a SpeakerInput JSON, return final text."""
    # These imports reach into the nexus app layer — they need the app on sys.path.
    # When invoked via `uv run python scripts/speak_one_off.py` from backend/nexus/,
    # the venv is active and nexus/app is importable.
    from app.ai.config import ai_config
    from app.ai.client import get_openai_client
    from app.ai.prompts import PromptLoader
    from app.modules.interview_engine.models.speaker import SpeakerInput
    from app.modules.interview_engine.speaker.service import SpeakerService

    payload = json.loads(speaker_input_path.read_text())
    speaker_input = SpeakerInput.model_validate(payload)
    client = await get_openai_client()
    # Use v2 prompt loader (same as the live engine)
    loader = PromptLoader(version=2)
    service = SpeakerService(
        openai_client=client,
        model=ai_config.engine_speaker_model,
        loader=loader,
    )
    handle = await service.stream(
        turn_id="one-off",
        speaker_input=speaker_input,
        correlation_id="one-off",
        tenant_id="00000000-0000-0000-0000-000000000000",
    )
    return await handle.final_text()


def _play(path: Path) -> None:
    if shutil.which("aplay"):
        subprocess.run(["aplay", "-q", str(path)], check=False)
    elif shutil.which("afplay"):
        subprocess.run(["afplay", str(path)], check=False)
    else:
        print(f"  (no player found — wav at {path})")


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Sarvam TTS A/B bench — synthesize text for one or more voices.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--utterance",
        help="Literal text to synthesize (skips the Speaker LLM).",
    )
    g.add_argument(
        "--speaker-input",
        type=Path,
        metavar="PATH",
        help="Path to a SpeakerInput JSON — runs the full Speaker LLM first.",
    )
    parser.add_argument(
        "--voices",
        default="shubh,rahul,amit,aditya",
        help=(
            "Comma-separated Sarvam speaker names. Default: shubh,rahul,amit,aditya "
            "(all bulbul:v3 voices). See module docstring for v2 names."
        ),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("INTERVIEW_TTS_MODEL", "bulbul:v3"),
        help="Sarvam TTS model (default: $INTERVIEW_TTS_MODEL or bulbul:v3).",
    )
    parser.add_argument(
        "--language",
        default=os.environ.get("INTERVIEW_TTS_LANGUAGE", "en-IN"),
        help="BCP-47 language code (default: $INTERVIEW_TTS_LANGUAGE or en-IN).",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=float(os.environ.get("INTERVIEW_TTS_PACE", "1.0")),
        help="Speech rate multiplier 0.3–3.0 (default: $INTERVIEW_TTS_PACE or 1.0).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=float(os.environ.get("INTERVIEW_TTS_TEMPERATURE", "0.6")),
        help="Sampling temperature 0.01–2.0, v3/v3-beta only (default: 0.6).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("/tmp/speak_one_off"),
        help="Output directory for WAV files (default: /tmp/speak_one_off).",
    )
    parser.add_argument(
        "--no-play",
        action="store_true",
        help="Write WAV files but do not play them.",
    )
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve text — either literal or Speaker LLM output.
    if args.utterance:
        text = args.utterance
    else:
        print("Running Speaker LLM …")
        text = await _speaker_then_text(args.speaker_input)
        print(f"Speaker output: {text!r}\n")

    voices = [v.strip() for v in args.voices.split(",") if v.strip()]

    print(
        f"Model: {args.model}  Language: {args.language}  "
        f"Pace: {args.pace}  Temperature: {args.temperature}"
    )
    print(f"Text: {text!r}\n")

    failed: list[str] = []
    for voice in voices:
        out_path = out_dir / f"{voice}.wav"
        print(f"[{voice}] synthesizing → {out_path}")
        try:
            await _synthesize_to_wav(
                text,
                voice,
                out_path,
                model=args.model,
                language=args.language,
                pace=args.pace,
                temperature=args.temperature,
            )
            size_kb = out_path.stat().st_size // 1024
            print(f"[{voice}] saved ({size_kb} kB)")
            if not args.no_play:
                _play(out_path)
        except Exception as exc:
            print(f"[{voice}] FAILED: {exc}", file=sys.stderr)
            failed.append(voice)

    if failed:
        print(f"\nFailed voices: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(f"\nDone. WAVs in {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
