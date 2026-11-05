"""Compatibility exports; warehouse I/O lives in warehouse/retention.py."""

from ..warehouse.retention import (
    ArtifactCollector,
)

__all__ = ["ArtifactCollector"]
