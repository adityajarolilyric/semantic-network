"""Compatibility exports; warehouse I/O lives in warehouse/dimension_execution.py."""

from ..warehouse.dimension_execution import (
    DimensionTableExecutor,
)

__all__ = ["DimensionTableExecutor"]
