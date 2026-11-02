from typing import Any


class MetadataError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, details: Any = None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        super().__init__(message)


class NotFound(MetadataError):
    def __init__(self, resource: str):
        super().__init__("not_found", f"{resource} was not found.", 404)


class Conflict(MetadataError):
    def __init__(
        self, message: str = "The resource changed; reload its version before retrying."
    ):
        super().__init__("conflict", message, 409)


class ImmutableRevision(Conflict):
    def __init__(self):
        super().__init__(
            "Published metadata is immutable. Create a new draft revision to make changes."
        )
