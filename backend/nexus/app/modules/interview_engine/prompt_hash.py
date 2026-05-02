"""Prompt-file SHA-256 helper.

Audit replay records ``sha256:<hex>`` for each prompt file the agent
loaded at session start. Recovery of the exact prompt body for a
historical session is then ``git show <hash>:prompts/v1/<relpath>`` —
git is durable, content-addressed, and access-controlled.

The hash space is the prompt file BYTES, not the path. Two files with
identical content have identical hashes (intentional — same prompt,
same hash, regardless of where it's mounted).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# The repo's prompt root. `app.ai.prompts.prompt_loader` reads from this
# same directory; we resolve the path the same way it does so test and
# runtime see identical bytes.
_PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "prompts" / "v1"


def hash_prompt_file(relative_path: str) -> str:
    """Return ``sha256:<hex>`` of the prompt file at
    ``backend/nexus/prompts/v1/<relative_path>``.

    Raises FileNotFoundError if the path does not exist.
    """
    path = _PROMPTS_ROOT / relative_path
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    return f"sha256:{digest}"
