"""Read-only pivot inference commands and reviewable configuration results."""

from typing import Literal
from pydantic import Field

from .common import Identifier, Schema
from .managed_tables import PivotStage


class PivotSuggestionInput(Schema):
    tableId: Identifier


class PivotSuggestionResult(Schema):
    tableId: Identifier
    pivot: PivotStage | None = None
    confidence: Literal["high", "medium", "none"] = "none"
    rowsExamined: int
    complete: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
