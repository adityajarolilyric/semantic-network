from typing import Annotated, Literal, Union

from pydantic import AliasChoices, Field, field_validator, model_validator

from ..formula_language.parser import formula_parser

from .common import DocumentMetadata, Identifier, Name, Schema, SemanticInput
from .formulas import Formula
from .temporal import TemporalContext
from .grains import GrainReference


class ResultType(Schema):
    type: Literal["decimal", "number", "integer", "string", "date", "boolean"] = (
        "number"
    )
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
    nonTime: Literal[
        "sum",
        "avg",
        "min",
        "max",
        "count",
        "count_distinct",
        "single_value",
        "any_true",
        "all_true",
    ]
    time: Literal[
        "sum",
        "first",
        "last",
        "avg",
        "min",
        "max",
        "count",
        "count_distinct",
        "single_value",
        "any_true",
        "all_true",
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


class MeasureConversion(Schema):
    factorMeasureId: Identifier
    operation: Literal["multiply", "divide"] = "multiply"
    targetUnit: Name


class ReferencedMeasureDefinition(Schema):
    isConversionFactor: bool = False
    conversion: MeasureConversion | None = None

    measureType: Literal["referenced"]
    transactionId: Identifier
    valueColumnId: Identifier
    legacyNativeGrainId: Identifier | None = Field(
        default=None,
        validation_alias=AliasChoices("nativeDimensionId", "nativeGrainId"),
        exclude=True,
    )
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
    allocation: Allocation | None = Field(default_factory=Allocation)

    @field_validator("allocation", mode="before")
    @classmethod
    def default_repeat(cls, value):
        return {"mode": "none"} if value is None else value

    editable: bool = False

    @model_validator(mode="after")
    def factor_contract(self):
        if self.isConversionFactor:
            if self.conversion or self.resultType.type not in {
                "number",
                "decimal",
                "integer",
            }:
                raise ValueError(
                    "Conversion factors must be numeric and cannot themselves apply a conversion"
                )
            self.editable = False
            self.allocation = Allocation(mode="none")
            self.aggregation = Aggregation(nonTime="single_value", time="single_value")
        return self


class CalculatedMeasureDefinition(Schema):
    temporal: TemporalContext | None = None
    measureType: Literal["calculated"]
    outputGrain: GrainReference | None = None
    legacyNativeGrainId: Identifier | None = Field(
        default=None,
        validation_alias=AliasChoices("nativeDimensionId", "nativeGrainId"),
        exclude=True,
    )
    calculationType: Literal["AggregateFirst", "CalculateFirst"]
    formula: Formula
    resultType: ResultType = Field(default_factory=ResultType)
    aggregation: Aggregation | None = None
    allocation: Allocation | None = None
    nullInputPolicy: Literal["propagate"] = "propagate"
    overflowPolicy: Literal["error"] = "error"
    editable: Literal[False] = False

    @model_validator(mode="after")
    def resolve_output_grain(self):
        if self.outputGrain is None:
            if not self.legacyNativeGrainId:
                raise ValueError(
                    "Choose a Transaction or Dimension for the calculation grain"
                )
            self.outputGrain = GrainReference(
                kind="dimension", objectId=self.legacyNativeGrainId
            )
        return self

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
    # Authoring command only: persist the canonical name and expression separately.
    formulaDefinition: str | None = Field(default=None, max_length=4000, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def derive_formula_identity(cls, value):
        if not isinstance(value, dict) or value.get("formulaDefinition") is None:
            return value
        if not isinstance(value["formulaDefinition"], str):
            raise ValueError("formulaDefinition must be a string")
        definition = value.get("definition", {})
        if not isinstance(definition, dict):
            raise ValueError("definition must be an object")
        if definition.get("measureType") != "calculated":
            raise ValueError(
                "Named formulas are only supported for calculated measures"
            )
        formula = definition.get("formula", {})
        if not isinstance(formula, dict):
            raise ValueError("formula must be an object")
        parsed = formula_parser().parse_definition(value["formulaDefinition"])
        return {
            **value,
            "name": parsed.name,
            "displayName": parsed.name,
            "definition": {
                **definition,
                "formula": {
                    **formula,
                    "displayText": parsed.expression_text,
                    "languageVersion": "planning-v1",
                },
            },
        }

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
