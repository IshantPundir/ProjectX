from fastapi import APIRouter

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.post("/invite")
async def create_invite() -> dict[str, str]:
    """Create a session invite for a candidate. Stub."""
    return {"status": "not_implemented"}


@router.post("/verify-otp")
async def verify_otp() -> dict[str, str]:
    """Verify candidate OTP. Stub."""
    return {"status": "not_implemented"}
