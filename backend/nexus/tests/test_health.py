import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    # Top-level status is ok when all checks pass.
    assert body["status"] == "ok"
    # Pub/sub check must be present; value depends on whether Redis is reachable
    # in the test environment (it is — see docker-compose.yml / conftest.py).
    assert "pubsub" in body["checks"]
    assert body["checks"]["pubsub"] == "ok"
