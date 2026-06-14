"""Guard: the reel module must never re-grow gen-2 event-log timing.

The Candidate Reel was re-plumbed off the gen-2 engine event-log
(``engine.v2.dispatched`` / ``audio.user.state`` / ``turn.captured`` markers and
the VAD cross-correlation / pipeline-lag machinery) onto the gen-3 word-timed
``SessionEvidence`` transcript with a single pure recording offset (see
``timing.py`` / ``test_timing_gen3.py``).

This test locks that deletion: it reads the actual source of the reel package
files and asserts none of the gen-2 event-log markers reappear. If any of these
strings come back, the re-plumb has regressed.
"""
from pathlib import Path

import pytest

import app.modules.reel as reel_pkg

# Source files that carried (or could carry) gen-2 event-log timing.
_REEL_DIR = Path(reel_pkg.__file__).parent
_GUARDED_FILES = (
    "timing.py",
    "render.py",
    "actors.py",
    "transcript.py",
    "director.py",
)

# Gen-2 engine event-log markers + the VAD / pipeline-lag helper names that the
# re-plumb deleted. None of these may appear in the reel package source.
_FORBIDDEN_MARKERS = (
    "engine.v2.dispatched",
    "audio.user.state",
    "turn.captured",
    "engine_t0_wall",
    "_resolve_events",
    "speaking_intervals",
    "answer_span",
    "measure_pipeline_lag",
)


@pytest.mark.parametrize("filename", _GUARDED_FILES)
@pytest.mark.parametrize("marker", _FORBIDDEN_MARKERS)
def test_reel_source_has_no_gen2_eventlog_marker(filename: str, marker: str) -> None:
    path = _REEL_DIR / filename
    assert path.is_file(), f"expected reel source file missing: {path}"
    source = path.read_text(encoding="utf-8")
    assert marker not in source, (
        f"gen-2 event-log marker {marker!r} reappeared in {filename}; "
        "the reel was re-plumbed onto gen-3 word-timed SessionEvidence — "
        "do not reintroduce event-log / pipeline-lag timing."
    )
