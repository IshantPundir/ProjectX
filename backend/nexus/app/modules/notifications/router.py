from fastapi import APIRouter

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.post("/test-email")
async def send_test_email() -> dict[str, str]:
    """Send a test email (admin only). Stub."""
    return {"status": "not_implemented"}
