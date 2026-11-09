"""Explicit calendar-period semantics for stored temporal calculations."""

from typing import Literal
from pydantic import Field, model_validator
from .common import Identifier, Schema, unique


class TemporalContext(Schema):
    timeKeyId: Identifier
    orderColumnId: Identifier
    periodUnit: Literal["day", "week", "month", "index"] = "week"
    partitionColumnIds: list[Identifier] = Field(default_factory=list, max_length=32)
    resetColumnId: Identifier | None = None
    minimumPeriods: int = Field(default=1, ge=1, le=10000)

    @model_validator(mode="after")
    def distinct_partition(self):
        unique(self.partitionColumnIds, "series keys")
        if self.timeKeyId in self.partitionColumnIds:
            raise ValueError("The time key cannot also partition a time series")
        return self
