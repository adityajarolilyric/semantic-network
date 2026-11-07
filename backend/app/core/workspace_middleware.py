from starlette.requests import Request
from starlette.responses import JSONResponse

from .errors import MetadataError
from .http import error_payload


class WorkspaceMiddleware:
    def __init__(self, app, gate):
        self.app, self.gate = app, gate

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "").rstrip("/")
        if (
            scope["type"] != "http"
            or not path.startswith("/api/v1/")
            or path == "/api/v1/workspace/reset"
            or scope["method"] == "OPTIONS"
        ):
            return await self.app(scope, receive, send)
        try:
            async with self.gate.shared():
                await self.app(scope, receive, send)
        except MetadataError as exc:
            response = JSONResponse(
                error_payload(Request(scope), exc.code, exc.message, exc.details),
                status_code=exc.status,
            )
            await response(scope, receive, send)
