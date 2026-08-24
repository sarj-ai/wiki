from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import create_app


async def test_maintenance_blocks_every_dynamic_route_but_keeps_health() -> None:
    application = create_app(settings.model_copy(update={"MAINTENANCE_MODE": True}))
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        blocked_get = await client.get("/files/private-id")
        blocked_post = await client.post("/auth/refresh")
        health = await client.get("/health")

    assert blocked_get.status_code == 503
    assert blocked_post.status_code == 503
    assert blocked_get.headers["retry-after"] == "60"
    assert health.status_code == 200
