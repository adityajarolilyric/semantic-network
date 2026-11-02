from typing import Annotated, Literal, Union

from pydantic import Field

from .common import Identifier, Schema


class MeasureReference(Schema):
    type: Literal["measureRef"]
    measureId: Identifier


class GrainReference(Schema):
    type: Literal["grainRef"]
    grainColumnId: Identifier


class LiteralExpression(Schema):
    type: Literal["literal"]
    value: int | float


class BinaryExpression(Schema):
    type: Literal["binary"]
    operator: Literal["add", "subtract", "multiply", "divide"]
    left: "Expression"
    right: "Expression"


class CallExpression(Schema):
    type: Literal["call"]
    function: Literal["abs", "round", "coalesce", "min", "max"]
    arguments: list["Expression"] = Field(min_length=1, max_length=16)


Expression = Annotated[
    Union[
        MeasureReference,
        GrainReference,
        LiteralExpression,
        BinaryExpression,
        CallExpression,
    ],
    Field(discriminator="type"),
]
BinaryExpression.model_rebuild()
CallExpression.model_rebuild()


class Formula(Schema):
    displayText: str = Field(min_length=1, max_length=4000)
    expression: Expression
