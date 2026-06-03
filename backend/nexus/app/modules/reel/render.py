"""Assemble the reel: render each EDL beat to a normalized segment, then concat.

Cards (title/match/point/outro) become a still image under Arjun narration; clips
(experience/clip) are cut from the recording with burned captions. Every segment
is normalized to identical codec/params (1280x720, 30fps, H.264 yuv420p, AAC 48k
stereo) so the concat demuxer joins them with a stream copy -- fast, glitch-free.

Imports of cards/tts/clips/timing are top-level but import-light (Pillow / livekit
/ ffmpeg are all lazy or shelled out), so this stays importable in the lean image;
``render_reel`` is only CALLED in the vision image.
"""
from __future__ import annotations

import asyncio
import os

from app.modules.reel import captions, cards, clips, timing, tts

# Render-side minimum card hold times (s) — a card lasts max(floor, narration+tail).
_CARD_FLOOR_S = {"title": 3.0, "match": 4.0, "point": 3.0, "outro": 4.0}
_NARRATION_TAIL_S = 0.5


def build_concat_file(clip_paths: list[str]) -> str:
    """Pure: the concat-demuxer list file body (one `file '<abspath>'` per line)."""
    return "".join(f"file '{os.path.abspath(p)}'\n" for p in clip_paths)


def build_card_segment_cmd(*, image_path: str, out_path: str, duration_ms: int,
                           audio_path: str | None = None,
                           w: int = cards.CARD_W, h: int = cards.CARD_H,
                           fps: int = 30) -> list[str]:
    """Pure: ffmpeg argv turning a still card (+ optional narration) into a segment.

    Output params match ``clips.cut_clip`` so concat can stream-copy. Without
    narration, a silent stereo source keeps every segment's audio stream present.
    """
    dur = max(0.1, duration_ms / 1000.0)
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", image_path]
    if audio_path:
        cmd += ["-i", audio_path]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += [
        "-map", "0:v", "-map", "1:a", "-t", f"{dur:.3f}",
        "-vf", (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", out_path,
    ]
    return cmd


async def card_segment(*, image_path: str, out_path: str, duration_ms: int,
                       audio_path: str | None = None) -> str:
    cmd = build_card_segment_cmd(image_path=image_path, out_path=out_path,
                                 duration_ms=duration_ms, audio_path=audio_path)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg card segment failed ({proc.returncode}): "
                           f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return out_path


_CARD_KINDS = ("title", "match", "point", "outro")


async def prepare_anchor(events: list[dict], recording_path: str,
                         recording_started_at_ms: int
                         ) -> tuple[int, list[tuple[int, int]]]:
    """Compute ``anchor`` (video_ms = t_ms + anchor) + the VAD speaking intervals.

    ``anchor = wall_anchor - pipeline_lag``; the lag is measured per session by
    cross-correlating the candidate VAD envelope against the recording's speech
    envelope (calibration only). Requires ffmpeg — call only in the vision image.
    """
    wall_anchor = timing.wall_anchor(events, recording_started_at_ms)
    speaking = timing.speaking_intervals(events)
    rec_speech = await timing.recording_speech_intervals(recording_path)
    pipeline_lag = timing.measure_pipeline_lag(speaking, rec_speech, wall_anchor)
    return wall_anchor - pipeline_lag, speaking


async def probe_duration_ms(path: str) -> int:
    """Exact media duration (ms) via ffprobe — for accurate chapter offsets."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return int(float(out.decode().strip()) * 1000)
    except (ValueError, AttributeError):
        return 0


def _chapter_label(beat) -> str:
    if beat.on_screen_text:
        return beat.on_screen_text.lstrip("★ ").strip()[:60]
    return {"clip": "Candidate", "experience": "Candidate"}.get(beat.kind, beat.kind.title())


def _clip_to_video(beat, events: list[dict], speaking: list[tuple[int, int]],
                   anchor: int) -> tuple[int, int, list[dict]] | None:
    """Map a clip beat's words -> (video_start, video_end, cleaned caption words).

    Each word maps to video via ITS OWN turn's VAD span (so a multi-turn clip is
    one contiguous cut). Returns None if any source turn's span can't be resolved.
    """
    span_cache: dict[int, tuple[int, int] | None] = {}

    def base(turn_commit: int) -> int | None:
        if turn_commit not in span_cache:
            span_cache[turn_commit] = timing.answer_span(events, speaking, turn_commit)
        sp = span_cache[turn_commit]
        return None if sp is None else sp[0] + anchor

    mapped: list[dict] = []
    for w in beat.words:
        b = base(int(w["turn_commit"]))
        if b is None:
            return None
        mapped.append({"text": w["text"],
                       "start_ms": b + int(w["rel_start_ms"]),
                       "end_ms": b + int(w["rel_end_ms"])})
    if not mapped:
        return None
    video_start, video_end = mapped[0]["start_ms"], mapped[-1]["end_ms"]
    return video_start, video_end, captions.clean_caption_words(mapped)


async def render_reel(*, beats: list, recording_path: str, events: list[dict],
                      speaking: list[tuple[int, int]], anchor: int,
                      tmp_dir: str, out_path: str, tts_enabled: bool = True
                      ) -> tuple[str, list[dict]]:
    """Render a validated EDL into one MP4 + chapter metadata.

    Card+narration beats are interleaved with candidate clips. ``anchor`` maps
    engine t_ms to the video clock (``video_ms = t_ms + anchor``; see timing.py).
    Clip beats whose VAD span can't be resolved are skipped. Returns
    ``(out_path, chapters)`` where chapters = ``[{kind, label, start_ms}]`` at the
    exact (ffprobe-measured) offset of each rendered segment.
    """
    rendered: list[tuple[str, object]] = []   # (segment_path, beat)
    for i, b in enumerate(beats):
        seg = os.path.join(tmp_dir, f"seg_{i:02d}.mp4")
        if b.kind in _CARD_KINDS:
            png = os.path.join(tmp_dir, f"card_{i:02d}.png")
            cards.render_card(kind=b.kind, out_path=png,
                              on_screen_text=b.on_screen_text or "")
            audio_path, audio_dur = None, 0
            if tts_enabled and b.narration_text:
                res = await tts.synthesize_to_wav(
                    b.narration_text, os.path.join(tmp_dir, f"narr_{i:02d}.wav"))
                if res:
                    audio_path, audio_dur = res
            floor_ms = int(_CARD_FLOOR_S.get(b.kind, 3.0) * 1000)
            duration_ms = (max(floor_ms, audio_dur + int(_NARRATION_TAIL_S * 1000))
                           if audio_dur else max(floor_ms, b.duration_ms))
            await card_segment(image_path=png, out_path=seg,
                               duration_ms=duration_ms, audio_path=audio_path)
            rendered.append((seg, b))
        else:  # clip / experience
            mapped = _clip_to_video(b, events, speaking, anchor)
            if mapped is None:
                continue
            video_start, video_end, caption_words = mapped
            await clips.cut_clip(
                recording_path=recording_path, out_path=seg, words=caption_words,
                start_ms=video_start, end_ms=video_end, offset_ms=0)
            rendered.append((seg, b))

    if not rendered:
        raise RuntimeError("render_reel: no renderable segments")

    chapters: list[dict] = []
    cursor = 0
    for seg, beat in rendered:
        chapters.append({"kind": beat.kind, "label": _chapter_label(beat),
                         "start_ms": cursor})
        cursor += await probe_duration_ms(seg)

    await concat_clips([s for s, _ in rendered], out_path)
    return out_path, chapters


async def concat_clips(clip_paths: list[str], out_path: str) -> str:
    if not clip_paths:
        raise ValueError("concat_clips: no clips")
    list_path = out_path + ".concat.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        f.write(build_concat_file(clip_paths))
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", "-movflags", "+faststart", out_path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg concat failed ({proc.returncode}): "
                           f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return out_path
