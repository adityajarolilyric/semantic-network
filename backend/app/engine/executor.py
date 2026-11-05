"""Compatibility exports; warehouse I/O lives in warehouse/managed_execution.py."""

from ..warehouse.managed_execution import (
    ClickHouseExecutor,
)

__all__ = ["ClickHouseExecutor"]
