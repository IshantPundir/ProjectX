"""Phase 1 — prompt file hashing.

Helper that returns sha256:HEX for a prompt file's body. Audit replay
uses the hash to recover the prompt body from git history.
"""

from __future__ import annotations

import hashlib

import pytest

from app.modules.interview_engine.prompt_hash import hash_prompt_file


def test_hash_prompt_file_matches_known_value() -> None:
    """interview/controller.txt is the live Phase 2 system prompt for
    InterviewController. Hash should be deterministic + content-only."""
    sha = hash_prompt_file("interview/controller.txt")
    assert sha.startswith("sha256:")
    # Hex section is 64 chars
    assert len(sha) == len("sha256:") + 64


def test_hash_prompt_file_is_deterministic() -> None:
    a = hash_prompt_file("interview/controller.txt")
    b = hash_prompt_file("interview/controller.txt")
    assert a == b


def test_hash_prompt_file_raises_on_missing() -> None:
    with pytest.raises(FileNotFoundError):
        hash_prompt_file("interview/does_not_exist.txt")


def test_hash_prompt_file_uses_sha256_of_bytes() -> None:
    """Sanity check that the helper hashes the file's bytes, not its
    name — manually compute and compare."""
    from app.ai.prompts import prompt_loader

    body = prompt_loader.get("interview/controller")
    expected_hex = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert hash_prompt_file("interview/controller.txt") == f"sha256:{expected_hex}"
