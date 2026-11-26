from typing import Annotated, Literal, Union

from pydantic import AliasChoices, Field, model_validator

from .common import Identifier, Schema
from .grains import GrainReference as OutputGrainReference
from .temporal import TemporalContext


class MeasureReference(Schema):
    type: Literal["measureRef"]
    measureId: Identifier


class GrainReference(Schema):
    type: Literal["grainRef"]
    grainColumnId: Identifier


class AttributeReference(Schema):
    type: Literal["attributeRef"]
    memberCatalogId: Identifier | None = None
    dimensionId: Identifier | None = None
    transactionId: Identifier | None = None
    columnId: Identifier

    @model_validator(mode="after")
    def one_owner(self):
        if (
            sum(
                value is not None
                for value in (
                    self.memberCatalogId,
                    self.dimensionId,
                    self.transactionId,
                )
            )
            != 1
        ):
            raise ValueError(
                "Choose exactly one DimensionMembers or Dimension attribute owner."
            )
        return self


class LiteralExpression(Schema):
    type: Literal["literal"]
    value: bool | str | int | float


class BinaryExpression(Schema):
    type: Literal["binary"]
    operator: Literal[
        "add", "subtract", "multiply", "divide", "eq", "ne", "gt", "gte", "lt", "lte"
    ]
    left: "Expression"
    right: "Expression"


class CallExpression(Schema):
    type: Literal["call"]
    function: Literal[
        "abs",
        "sqrt",
        "root",
        "power",
        "ceil",
        "floor",
        "rolling",
        "cumulative",
        "lag",
        "lead",
        "round",
        "coalesce",
        "min",
        "max",
        "if",
        "and",
        "or",
        "not",
        "concat",
        "upper",
        "lower",
        "trim",
        "date",
        "add_days",
        "days_between",
        "is_null",
    ]
    arguments: list["Expression"] = Field(min_length=1, max_length=16)


class CountExpression(Schema):
    type: Literal["count"]
    function: Literal["count", "count_distinct", "count_if", "count_rows"]
    arguments: list["Expression"] = Field(default_factory=list, max_length=1)


Expression = Annotated[
    Union[
        MeasureReference,
        GrainReference,
        AttributeReference,
        LiteralExpression,
        BinaryExpression,
        CallExpression,
        CountExpression,
    ],
    Field(discriminator="type"),
]
BinaryExpression.model_rebuild()
CallExpression.model_rebuild()
CountExpression.model_rebuild()


class Formula(Schema):
    displayText: str = Field(min_length=1, max_length=4000)
    expression: Expression | None = None
    languageVersion: Literal["planning-v1"] | None = None
    astVersion: Literal[1, 2] = 1

    @model_validator(mode="after")
    def require_source(self):
        if self.expression is None and self.languageVersion is None:
            raise ValueError(
                "Provide a saved semantic expression or a versioned formula source."
            )
        return self


class FormulaValidationInput(Schema):
    temporal: TemporalContext | None = None
    requireAssignment: bool = False
    objectId: Identifier
    name: str = Field(default="", max_length=200)
    displayText: str = Field(min_length=1, max_length=4000)
    languageVersion: Literal["planning-v1"] = "planning-v1"
    nativeGrainId: Identifier | None = Field(
        default=None,
        validation_alias=AliasChoices("nativeGrainId", "nativeDimensionId"),
    )
    outputGrain: OutputGrainReference | None = None
    calculationType: Literal["AggregateFirst", "CalculateFirst"] = "AggregateFirst"
