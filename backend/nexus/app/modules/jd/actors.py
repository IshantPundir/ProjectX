"""Phase 2A Dramatiq actors — extract_and_enhance_jd implementation in Task 26.

This stub exists so service.py's lazy import works during tests before Task 26
lands. The real implementation replaces this file completely."""

import dramatiq


@dramatiq.actor(queue_name="jd_extraction")
async def extract_and_enhance_jd(
    job_posting_id: str, tenant_id: str, correlation_id: str
) -> None:
    """Stub — real implementation in Task 26."""
    raise NotImplementedError("extract_and_enhance_jd implementation comes in Task 26")
