from typing import Literal

from pydantic import Field, model_serializer, model_validator

from .common import DocumentMetadata, Identifier, Name, Schema, SemanticInput, unique
from .filters import FilterDefinition
from .managed_tables import PivotStage


class DimensionMemberColumn(Schema):
    columnId: Identifier
    name: Name
    displayName: Name
    sourceColumnId: Identifier


class TimeDefinition(Schema):
    baseGrain: Literal["day", "week", "month"]
    dateColumnId: Identifier
    orderingColumnId: Identifier


class DimensionMembersDefinition(Schema):
    sourceId: Identifier
    pivot: PivotStage | None = None
    columns: list[DimensionMemberColumn] = Field(min_length=1, max_length=2000)
    keyColumnIds: list[Identifier] = Field(min_length=1, max_length=32)
    labelColumnId: Identifier
    attributeColumnIds: list[Identifier] = Field(default_factory=list, max_length=2000)
    filters: FilterDefinition | None = None
    time: TimeDefinition | None = None

    @model_validator(mode="after")
    def validate_columns(self):
        ids = [col.columnId for col in self.columns]
        unique(ids, "dimension column IDs")
        unique(self.keyColumnIds, "dimension keys")
        unique(self.attributeColumnIds, "dimension attributes")
        refs = [*self.keyColumnIds, self.labelColumnId, *self.attributeColumnIds]
        if self.time:
            refs += [self.time.dateColumnId, self.time.orderingColumnId]
        if not set(refs) <= set(ids):
            raise ValueError("dimension references must name dimension columns")
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if value.get("pivot") is None:
            value.pop("pivot", None)
        return value


class DimensionMembersInput(SemanticInput):
    kind: Literal["dimensionMembers"] = "dimensionMembers"
    isTimeDim: bool
    definition: DimensionMembersDefinition

    @model_validator(mode="after")
    def validate_time(self):
        if self.isTimeDim != (self.definition.time is not None):
            raise ValueError(
                "time configuration must be present exactly when isTimeDim is true"
            )
        return self


class DimensionMembersDocument(DimensionMembersInput, DocumentMetadata):
    pass


class DimensionMembersCreateInput(DimensionMembersInput):
    # Authoring command only; the grain is an engine-owned representation.
    withGrain: bool = Field(default=False, exclude=True)
