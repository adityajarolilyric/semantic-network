import hmac
import logging
from time import monotonic
from uuid import uuid4

from fastapi import Header
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .config import Settings
from .errors import MetadataError

logger = logging.getLogger("metadata.http")


def expected_version(
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> int:
    if if_match is None:
        raise MetadataError(
            "precondition_required",
            'Send If-Match: "<version>" from the current resource.',
            428,
        )
    value = if_match.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if (
        not value.isascii()
        or not value.isdecimal()
        or len(value) > 18
        or int(value) < 1
    ):
        raise MetadataError(
            "invalid_if_match",
            "If-Match must contain one positive integer version.",
            400,
        )
    return int(value)


class RequestMiddleware:
    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        started = monotonic()
        scope.setdefault("state", {})["request_id"] = request_id
        headers = Headers(scope=scope)

        async def respond(status, code, message):
            response = JSONResponse(
                {"error": {"code": code, "message": message, "requestId": request_id}},
                status_code=status,
                headers={"X-Request-ID": request_id},
            )
            await response(scope, receive, send)

        if self.settings.api_key and scope["path"].startswith("/api/"):
            expected = "Bearer " + self.settings.api_key.get_secret_value()
            if not hmac.compare_digest(
                headers.get("authorization", "").encode(), expected.encode()
            ):
                return await respond(
                    401, "unauthorized", "A valid bearer API key is required."
                )
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            body_limit = self.settings.max_request_bytes
            if scope["path"].rstrip("/") == "/api/v1/warehouse/imports/csv":
                body_limit = self.settings.csv_max_file_bytes + 65_536
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > body_limit:
                    return await respond(
                        413,
                        "request_too_large",
                        "Request body exceeds the configured size limit.",
                    )
                if not message.get("more_body", False):
                    break
            consumed = False

            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            request_receive = replay
        else:
            request_receive = receive

        async def with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", request_id.encode())
                ]
                logger.info(
                    "request_completed",
                    extra={
                        "request_id": request_id,
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": message["status"],
                        "duration_ms": round((monotonic() - started) * 1000, 2),
                    },
                )
            await send(message)

        await self.app(scope, request_receive, with_headers)


def error_payload(request, code, message, details=None):
    error = {
        "code": code,
        "message": message,
        "requestId": getattr(request.state, "request_id", None),
    }
    if details is not None:
        # Never return request bodies, credentials, internal exception contexts, or Mongo details.
        error["details"] = details
    return {"error": error}
