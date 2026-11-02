from fastapi import APIRouter


class HealthAPI:
    def __init__(self, store):
        self.store = store
        self.router = APIRouter(tags=["health"])
        self.router.add_api_route("/health/live", self.live, methods=["GET"])
        self.router.add_api_route("/health/ready", self.ready, methods=["GET"])

    async def live(self):
        return {"status": "ok"}

    async def ready(self):
        await self.store.ping()
        return {"status": "ready"}
