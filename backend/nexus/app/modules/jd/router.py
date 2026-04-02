from fastapi import APIRouter

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/")
async def list_jobs() -> dict[str, str]:
    """List job descriptions for the current tenant. Stub."""
    return {"status": "not_implemented"}


@router.post("/")
async def create_job() -> dict[str, str]:
    """Create a new job description. Stub."""
    return {"status": "not_implemented"}
