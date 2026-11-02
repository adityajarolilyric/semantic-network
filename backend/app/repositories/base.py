from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class DocumentRepository(ABC):
    @abstractmethod
    async def get(self, collection: str, query: dict) -> dict | None: ...

    @abstractmethod
    async def list(
        self,
        collection: str,
        query: dict,
        *,
        key: str,
        after: str | None = None,
        limit: int = 100,
        descending: bool = False,
    ) -> list[dict]: ...

    @abstractmethod
    async def insert(self, collection: str, document: dict) -> dict: ...

    @abstractmethod
    async def replace(self, collection: str, query: dict, document: dict) -> bool: ...

    @abstractmethod
    async def delete(self, collection: str, query: dict) -> bool: ...

    async def delete_all(self, collection: str) -> int:
        count = 0
        while await self.delete(collection, {}):
            count += 1
        return count


class MetadataStore(ABC):
    async def check_transaction(self, repo):
        guard = getattr(self, "transaction_guard", None)
        if guard:
            await guard(repo)

    @property
    @abstractmethod
    def repository(self) -> DocumentRepository: ...

    @abstractmethod
    async def transaction(
        self, operation: Callable[[DocumentRepository], Awaitable[T]]
    ) -> T: ...

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def ping(self) -> None: ...
