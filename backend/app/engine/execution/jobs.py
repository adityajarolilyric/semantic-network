"""The worker dispatches only code-owned implementations of this job contract."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .worker import EngineWorker


class EngineRunHandler(Protocol):
    async def execute_run(self, run: dict, worker: "EngineWorker") -> None: ...
