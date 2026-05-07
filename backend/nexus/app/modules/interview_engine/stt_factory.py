"""Per-session STT plugin factory — hook seam for keyterm extraction (v2).

v1: returns the global build_stt_plugin() unchanged. Future per-session
keyterm injection swaps only this function — the entrypoint and orchestrator
do not change.
"""
from __future__ import annotations

from typing import Any

from app.ai.realtime import build_stt_plugin


def build_stt_plugin_for_session(*, session_config: Any) -> Any:
    return build_stt_plugin()
