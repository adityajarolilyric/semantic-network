"""Operation-local query budgets captured on durable engine jobs."""

from contextlib import contextmanager
from contextvars import ContextVar

_query_memory = ContextVar("query_memory_bytes", default=None)


def query_memory_bytes(default):
    return _query_memory.get() or default


@contextmanager
def execution_budget(memory_bytes):
    token = _query_memory.set(memory_bytes)
    try:
        yield
    finally:
        _query_memory.reset(token)
