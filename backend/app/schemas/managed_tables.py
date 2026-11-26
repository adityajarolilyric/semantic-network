from typing import Literal

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_serializer,
    model_validator,
)

from .common import Identifier, Name, Schema

AttributeValue = StrictStr | StrictBool | StrictInt | StrictFloat | None


class PivotOutput(Schema):
    attributeValue: AttributeValue
    name: Name
    columnId: Identifier | None = None  # Server recomputes and verifies identity.


class PivotPair(Schema):
    pairId: Identifier
    attributeColumnId: Identifier
    valueColumnId: Identifier
    outputs: list[PivotOutput] = Field(min_length=1, max_length=200)


class PivotStage(Schema):
    stageId: Identifier
    type: Literal["pivot"] = "pivot"
    enabled: bool = True
    # Legacy fields are read for compatibility; new pivots use explicit record keys.
    mode: Literal["auto", "merge_rows", "expand_rows"] | None = None
    resolvedMode: Literal["merge_rows", "expand_rows"] | None = None
    recordKeyColumnIds: list[Identifier] | None = Field(default=None, max_length=32)
    groupByColumnIds: list[Identifier] = Field(default_factory=list, max_length=32)
    pairs: list[PivotPair] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def resolved_mode_is_automatic(self):
        if self.recordKeyColumnIds is not None and (
            self.mode is not None or self.resolvedMode is not None
        ):
            raise ValueError(
                "Explicit record grouping cannot be combined with legacy pivot modes"
            )
        if self.resolvedMode is not None and self.mode != "auto":
            raise ValueError("resolvedMode is only valid for automatic pivots")
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if value.get("recordKeyColumnIds") is None:
            value.pop("recordKeyColumnIds", None)
        if value.get("mode") is None:
            value.pop("mode", None)
        if value.get("resolvedMode") is None:
            value.pop("resolvedMode", None)
        return value


class ManagedDefinition(Schema):
    primaryKeyColumnIds: list[Identifier] = Field(default_factory=list, max_length=32)
    pipeline: list[PivotStage] = Field(default_factory=list, max_length=1)


class ManagedEnable(Schema):
    tableName: Name


class ObjectInputTransform(Schema):
    objectId: Identifier
    sourceId: Identifier
    pivot: PivotStage | None = None
