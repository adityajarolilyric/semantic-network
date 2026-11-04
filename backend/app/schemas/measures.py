from typing import Annotated, Literal, Union

from pydantic import Field, field_validator, model_validator

from .common import DocumentMetadata, Identifier, Name, Schema, SemanticInput
from .formulas import Formula


class ResultType(Schema):
    type: Literal["decimal", "number", "integer"] = "number"
    precision: int | None = Field(default=None, ge=1, le=76)
    scale: int | None = Field(default=None, ge=0, le=76)
    nullable: bool = True

    @model_validator(mode="after")
    def validate_decimal(self):
        if self.type == "decimal":
            if (
                self.precision is None
                or self.scale is None
                or self.scale > self.precision
            ):
                raise ValueError(
                    "decimal requires precision and scale with scale <= precision"
                )
        elif self.precision is not None or self.scale is not None:
            raise ValueError("precision and scale are only valid for decimal results")
        return self


class Aggregation(Schema):
    order: Literal["nonTimeThenTime", "timeThenNonTime"] = "nonTimeThenTime"
    nonTime: Literal["sum", "avg", "min", "max", "count", "count_distinct"]
    time: Literal[
        "sum", "first", "last", "avg", "min", "max", "count", "count_distinct"
    ]
    nullInputPolicy: Literal["ignore", "propagate"] = "ignore"
    allNullResult: Literal["null", "zero"] = "null"


class Allocation(Schema):
    mode: Literal["none", "equal", "weighted", "weighted_by_self"] = "weighted_by_self"

    @field_validator("mode", mode="before")
    @classmethod
    def null_means_repeat(cls, value):
        return "none" if value is None else value

    basisMeasureId: Identifier | None = None

    @model_validator(mode="after")
    def validate_basis(self):
        if (self.mode == "weighted") != (self.basisMeasureId is not None):
            raise ValueError(
                "basisMeasureId is required only for weighted dis-aggregation"
            )
        return self


class ReferencedMeasureDefinition(Schema):
    measureType: Literal["referenced"]
    transactionId: Identifier
    valueColumnId: Identifier
    nativePlanningLevelId: Identifier
    resultType: ResultType = Field(default_factory=ResultType)
    aggregation: Aggregation
    missingTransactionValue: Literal["null", "zero"] = "null"
    sourceNullPolicy: Literal["preserve"] = "preserve"

    @field_validator("sourceNullPolicy", mode="before")
    @classmethod
    def preserve_source_nulls(cls, value):
        # Accept the former choice when reading/updating existing metadata.
        return "preserve" if value == "reject" else value

    overflowPolicy: Literal["error"] = "error"
    allocation: Allocation = Field(default_factory=Allocation)

    @field_validator("allocation", mode="before")
    @classmethod
    def default_repeat(cls, value):
        return {"mode": "none"} if value is None else value

    editable: bool = False


class CalculatedMeasureDefinition(Schema):
    measureType: Literal["calculated"]
    nativePlanningLevelId: Identifier
    calculationType: Literal["AggregateFirst", "CalculateFirst"]
    formula: Formula
    resultType: ResultType = Field(default_factory=ResultType)
    aggregation: Aggregation | None = None
    allocation: Allocation | None = None
    nullInputPolicy: Literal["propagate"] = "propagate"
    overflowPolicy: Literal["error"] = "error"
    editable: Literal[False] = False

    @model_validator(mode="after")
    def validate_aggregation(self):
        if (
            self.calculationType == "CalculateFirst"
            and "allocation" not in self.model_fields_set
        ):
            self.allocation = Allocation()
        if self.calculationType == "CalculateFirst" and self.aggregation is None:
            raise ValueError(
                "CalculateFirst requires aggregation rules for its stored result"
            )
        if self.calculationType == "AggregateFirst" and self.allocation is not None:
            raise ValueError(
                "AggregateFirst recalculates dependencies; it does not allocate formula results"
            )
        if self.calculationType == "AggregateFirst" and self.aggregation is not None:
            raise ValueError(
                "AggregateFirst aggregates dependencies, not previously calculated results"
            )
        return self


class MeasureBusiness(Schema):
    unit: Name | None = None
    aliases: list[Name] = Field(default_factory=list, max_length=32)


class MeasureInput(SemanticInput):
    kind: Literal["measure"] = "measure"
    business: MeasureBusiness
    definition: Annotated[
        Union[ReferencedMeasureDefinition, CalculatedMeasureDefinition],
        Field(discriminator="measureType"),
    ]


class MeasureDocument(MeasureInput, DocumentMetadata):
    pass


class MeasureBuildRequest(Schema):
    # Omitted request bodies retain legacy full-refresh API semantics.
    mode: Literal["incremental", "full"] = "incremental"
