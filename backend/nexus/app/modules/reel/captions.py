"""Burned-in caption generation from word timings -> ASS subtitle string.

Pure functions; no ffmpeg here. Timings in the emitted ASS are CLIP-RELATIVE
(ms since the clip's first frame), so the caller passes the clip's session-ms
start. `words[]` is the source of truth (it may be a superset of the turn text).
"""
from __future__ import annotations

# Non-lexical fillers dropped from DISPLAYED captions (the audio keeps the real
# voice — standard broadcast captioning). Conservative: only true noise tokens,
# never content words like "so"/"like" (those may carry meaning mid-sentence).
_CAPTION_FILLERS = {"um", "umm", "uh", "uhh", "mm", "mmm", "er", "erm", "ah", "hmm"}


def clean_caption_words(words: list[dict]) -> list[dict]:
    """Readable, honest caption words: drop fillers, collapse stutters, sentence-case.

    Preserves each kept word's timing; never invents words or punctuation. Meaning
    is preserved — this only removes non-lexical noise and re-cases for readability.
    """
    out: list[dict] = []
    prev: str | None = None
    for w in words:
        token = str(w["text"])
        low = token.lower().strip(".,?!")
        if low in _CAPTION_FILLERS:
            continue
        if low and low == prev:          # collapse adjacent duplicate (stutter)
            continue
        out.append({**w, "text": token})
        prev = low
    for i, w in enumerate(out):
        token = w["text"]
        low = token.lower()
        if low == "i" or low.startswith("i'"):
            w["text"] = token[0].upper() + token[1:]
        elif i == 0 and token[:1].isalpha():
            w["text"] = token[0].upper() + token[1:]
    return out


_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Default,Arial,48,&H00FFFFFF,&H00000000,&H64000000,1,3,1,2,60,60,60

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def group_caption_lines(words: list[dict], *, max_words: int = 5) -> list[list[dict]]:
    """Chunk words into caption lines of at most ``max_words`` words each."""
    return [words[i:i + max_words] for i in range(0, len(words), max_words)]


def _ass_ts(ms: int) -> str:
    """Milliseconds -> ASS timestamp ``H:MM:SS.CC`` (centiseconds, floored)."""
    ms = max(0, int(ms))
    cs = (ms % 1000) // 10
    s = (ms // 1000) % 60
    m = (ms // 60_000) % 60
    h = ms // 3_600_000
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(words: list[dict], *, clip_start_ms: int, max_words: int = 5) -> str:
    """Render an ASS subtitle file for one clip span.

    Each caption line spans from its first word's start to its last word's end,
    expressed relative to ``clip_start_ms``.
    """
    out = [_ASS_HEADER]
    for line in group_caption_lines(words, max_words=max_words):
        if not line:
            continue
        start = _ass_ts(int(line[0]["start_ms"]) - clip_start_ms)
        end = _ass_ts(int(line[-1]["end_ms"]) - clip_start_ms)
        text = " ".join(str(w["text"]) for w in line).replace("\n", " ")
        out.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    return "\n".join(out) + "\n"
