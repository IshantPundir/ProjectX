from fastapi import APIRouter

router = APIRouter(prefix="/api/question-banks", tags=["question_bank"])


@router.get("/{job_id}")
async def get_question_bank(job_id: str) -> dict[str, str]:
    """Get the question bank for a job. Stub."""
    return {"status": "not_implemented", "job_id": job_id}


@router.post("/{job_id}/generate")
async def generate_question_bank(job_id: str) -> dict[str, str]:
    """Trigger AI generation of a question bank. Stub."""
    return {"status": "not_implemented", "job_id": job_id}
