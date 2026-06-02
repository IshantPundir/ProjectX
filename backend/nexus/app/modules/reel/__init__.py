"""Candidate Reel module.

Phase 2 clips-only core. Keep this __init__ free of heavy/optional imports
(Pillow, livekit TTS plugins) so the clip path imports cleanly in the lean
vision worker image. Submodules are imported directly by callers.
"""
