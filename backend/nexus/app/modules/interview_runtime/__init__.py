"""Phase 3C.2 — Interview engine internal API.

Exposes /api/internal/sessions/{id}/config + /results to the
backend/interview_engine/ worker process. Authed by single-use HS256
engine JWT (verify_engine_token).
"""
