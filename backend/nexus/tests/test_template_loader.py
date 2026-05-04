"""Tests for the per-template TemplateLoader.

Layout: <base>/<role>/<name>.<version>.txt — used by the structured
AI Screening Agent so two versions of the same template (e.g. intro.v1
and intro.v2) coexist in the same role directory and pinned sessions
keep running on their pinned versions while new sessions pick up new
versions.

Covers:
- Happy path: load a template, return resolved body.
- Cache: a second call returns the cached identity (no re-read in
  prod-mode default).
- Side-by-side versions: v1 and v2 of the same name load distinctly.
- hash(): stable sha256 of the resolved body, varies across versions.
- Missing file: FileNotFoundError surfaces role + name + version + path.
- Include directive: same-role + same-version sibling resolution works.
- Include cycle: raises RuntimeError.
- Include depth exceeded: raises RuntimeError.
- Cross-role includes are NOT supported (siblings only): missing-file.
- reload_on_change=True (dev-mode): mtime change forces a re-read; no
  re-read when the file is untouched.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from app.ai.prompts import TemplateLoader


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_loads_template_at_role_version_path():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write(base / "speech_agent" / "intro.v1.txt", "Hello, candidate.")
        loader = TemplateLoader(base)
        assert loader.get("speech_agent", "intro", "v1") == "Hello, candidate."


def test_caches_repeated_reads_in_prod_mode():
    """In default (reload_on_change=False) mode, a second call returns
    the cached body — no re-read."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        path = base / "speech_agent" / "intro.v1.txt"
        _write(path, "first body")
        loader = TemplateLoader(base, reload_on_change=False)
        first = loader.get("speech_agent", "intro", "v1")
        # Mutate the file. Without reload_on_change, the loader should
        # still return the cached "first body".
        path.write_text("second body", encoding="utf-8")
        second = loader.get("speech_agent", "intro", "v1")
        assert first is second  # identity → genuinely cached, not re-read
        assert first == "first body"


def test_versions_coexist():
    """v1 and v2 of the same role/name return distinct bodies."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write(base / "speech_agent" / "intro.v1.txt", "v1 body")
        _write(base / "speech_agent" / "intro.v2.txt", "v2 body — adjusted phrasing")
        loader = TemplateLoader(base)
        assert loader.get("speech_agent", "intro", "v1") == "v1 body"
        assert (
            loader.get("speech_agent", "intro", "v2") == "v2 body — adjusted phrasing"
        )


def test_hash_is_sha256_of_resolved_body():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        body = "the resolved body"
        _write(base / "speech_agent" / "intro.v1.txt", body)
        loader = TemplateLoader(base)
        h = loader.hash("speech_agent", "intro", "v1")
        expected = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert h == expected


def test_hash_differs_across_versions():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write(base / "speech_agent" / "intro.v1.txt", "v1")
        _write(base / "speech_agent" / "intro.v2.txt", "v2 — different content")
        loader = TemplateLoader(base)
        assert loader.hash("speech_agent", "intro", "v1") != loader.hash(
            "speech_agent", "intro", "v2"
        )


def test_missing_template_raises_filenotfound_with_full_context():
    with tempfile.TemporaryDirectory() as tmp:
        loader = TemplateLoader(Path(tmp))
        with pytest.raises(FileNotFoundError) as exc:
            loader.get("speech_agent", "missing", "v1")
        msg = str(exc.value)
        assert "role=speech_agent" in msg
        assert "name=missing" in msg
        assert "version=v1" in msg


def test_include_directive_resolves_sibling_at_same_version():
    """{{include:X}} inside intro.v1.txt looks up X.v1.txt in the same role."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write(base / "speech_agent" / "_voice_rules.v1.txt", "Use contractions.")
        _write(
            base / "speech_agent" / "intro.v1.txt",
            "{{include:_voice_rules}}\n\nHi candidate.",
        )
        loader = TemplateLoader(base)
        body = loader.get("speech_agent", "intro", "v1")
        assert "Use contractions." in body
        assert "Hi candidate." in body
        assert "{{include:" not in body


def test_include_directive_uses_caller_version_not_a_separate_pin():
    """If intro.v2 includes _voice_rules, it must look up _voice_rules.v2,
    NOT _voice_rules.v1. Versions are pinned per call, not globally."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write(base / "speech_agent" / "_voice_rules.v1.txt", "v1 voice rules.")
        _write(base / "speech_agent" / "_voice_rules.v2.txt", "v2 voice rules.")
        _write(
            base / "speech_agent" / "intro.v2.txt",
            "{{include:_voice_rules}} Greeting.",
        )
        loader = TemplateLoader(base)
        body = loader.get("speech_agent", "intro", "v2")
        assert "v2 voice rules." in body
        assert "v1 voice rules." not in body


def test_include_cycle_raises():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write(base / "r" / "a.v1.txt", "{{include:b}}")
        _write(base / "r" / "b.v1.txt", "{{include:a}}")
        loader = TemplateLoader(base)
        with pytest.raises(RuntimeError, match="cycle"):
            loader.get("r", "a", "v1")


def test_include_depth_exceeded_raises():
    """A chain of 9 includes (depth > 8) raises RuntimeError."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for i in range(10):
            next_token = (
                f"{{{{include:t{i + 1}}}}}" if i < 9 else "leaf"
            )
            _write(base / "r" / f"t{i}.v1.txt", next_token)
        loader = TemplateLoader(base)
        with pytest.raises(RuntimeError, match="depth"):
            loader.get("r", "t0", "v1")


def test_cross_role_includes_not_supported():
    """Includes are scoped to the calling template's role. Pointing at a
    name that exists only under a different role surfaces FileNotFoundError
    (the loader looks up the same-role sibling and finds nothing)."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write(base / "shared" / "_rules.v1.txt", "shared rules")
        _write(
            base / "speech_agent" / "intro.v1.txt",
            "{{include:_rules}} Greeting.",
        )
        loader = TemplateLoader(base)
        with pytest.raises(FileNotFoundError, match="_rules"):
            loader.get("speech_agent", "intro", "v1")


def test_reload_on_change_picks_up_mtime_bumps():
    """In dev-mode (reload_on_change=True), modifying the file on disk
    causes the next get() to re-read the body."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        path = base / "speech_agent" / "intro.v1.txt"
        _write(path, "original body")
        loader = TemplateLoader(base, reload_on_change=True)
        first = loader.get("speech_agent", "intro", "v1")
        assert first == "original body"

        # Touch the file with an explicitly-newer mtime so the test is
        # robust on filesystems with low mtime resolution (some FSes
        # round to 1s; mid-test rapid-write would otherwise produce
        # equal mtimes and look like "no change").
        path.write_text("revised body", encoding="utf-8")
        new_mtime = time.time() + 1
        os.utime(path, (new_mtime, new_mtime))

        second = loader.get("speech_agent", "intro", "v1")
        assert second == "revised body"


def test_reload_on_change_returns_cache_when_unchanged():
    """In dev-mode, an untouched file returns the cached body — no re-read."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        path = base / "speech_agent" / "intro.v1.txt"
        _write(path, "body")
        loader = TemplateLoader(base, reload_on_change=True)
        first = loader.get("speech_agent", "intro", "v1")
        second = loader.get("speech_agent", "intro", "v1")
        # File was never touched — same identity (cache hit).
        assert first is second


def test_role_directory_separation():
    """speech_agent/intro.v1 and intent_classifier/intro.v1 are distinct."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write(base / "speech_agent" / "intro.v1.txt", "speech body")
        _write(base / "intent_classifier" / "intro.v1.txt", "intent body")
        loader = TemplateLoader(base)
        assert loader.get("speech_agent", "intro", "v1") == "speech body"
        assert loader.get("intent_classifier", "intro", "v1") == "intent body"


def test_include_directive_textdedent_preserved():
    """Multi-line include bodies preserve their formatting verbatim."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        rules = textwrap.dedent("""
            - Use contractions.
            - Be brief.
        """).strip()
        _write(base / "r" / "_rules.v1.txt", rules)
        _write(base / "r" / "intro.v1.txt", "Header\n{{include:_rules}}\nFooter")
        loader = TemplateLoader(base)
        body = loader.get("r", "intro", "v1")
        assert "Header" in body
        assert "- Use contractions." in body
        assert "- Be brief." in body
        assert "Footer" in body
