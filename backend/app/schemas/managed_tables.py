from typing import Literal
from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr
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
    groupByColumnIds: list[Identifier] = Field(min_length=1, max_length=32)
    pairs: list[PivotPair] = Field(min_length=1, max_length=20)


class ManagedDefinition(Schema):
    primaryKeyColumnIds: list[Identifier] = Field(default_factory=list, max_length=32)
    pipeline: list[PivotStage] = Field(default_factory=list, max_length=1)


class ManagedEnable(Schema):
    tableName: Name
