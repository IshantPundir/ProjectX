from fastapi import APIRouter

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def get_current_user() -> dict[str, str]:
    """Return the current authenticated user's profile. Stub."""
    return {"status": "not_implemented"}
