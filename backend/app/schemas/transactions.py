from typing import Literal

from pydantic import Field, computed_field, model_serializer, model_validator

from ..domain.grains import transaction_grain_id
from .common import DocumentMetadata, Identifier, Name, Schema, SemanticInput, unique
from .filters import FilterDefinition
from .managed_tables import PivotStage
from .measures import Aggregation, Allocation


class TransactionKeyMapping(Schema):
    sourceColumnId: Identifier
    grainColumnId: Identifier


class ResolutionTarget(Schema):
    memberCatalogId: Identifier | None = None
    columnId: Identifier


class SelectorMapping(Schema):
    value: str = Field(min_length=1, max_length=200)
    target: ResolutionTarget


class ResolutionRule(Schema):
    ruleId: Identifier
    kind: Literal["column", "level_value"] = "column"
    sourceColumnId: Identifier | None = None
    target: ResolutionTarget | None = None
    selectorColumnId: Identifier | None = None
    valueColumnId: Identifier | None = None
    selectors: list[SelectorMapping] = Field(default_factory=list, max_length=32)
    blankIsAbsent: bool = True

    @model_validator(mode="after")
    def complete(self):
        if self.kind == "column":
            if (
                not self.sourceColumnId
                or not self.target
                or self.selectorColumnId
                or self.valueColumnId
                or self.selectors
            ):
                raise ValueError(
                    "A column rule requires a source column and target attribute only"
                )
        elif (
            not self.selectorColumnId
            or not self.valueColumnId
            or not self.selectors
            or self.sourceColumnId
            or self.target
        ):
            raise ValueError(
                "A level/value rule requires selector/value columns and explicit selector mappings"
            )
        unique([m.value for m in self.selectors], "selector literals")
        return self


class ResolutionDateRange(Schema):
    memberCatalogId: Identifier
    calendarKeyColumnId: Identifier
    startColumnId: Identifier
    endColumnId: Identifier


class CalendarResolution(Schema):
    memberCatalogId: Identifier
    startColumnId: Identifier
    endColumnId: Identifier
    selectedColumnIds: list[Identifier] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct(self):
        unique(self.selectedColumnIds, "calendar attributes")
        return self


class CalendarSchemaInput(Schema):
    transactionId: Identifier
    sourceId: Identifier
    calendarResolution: CalendarResolution
    pivot: PivotStage | None = None


class ResolutionAxis(Schema):
    grainColumnId: Identifier
    allowAll: bool = False
    dateRange: ResolutionDateRange | None = None
    rules: list[ResolutionRule] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def constrained(self):
        if self.dateRange and (self.rules or self.allowAll):
            raise ValueError(
                "Date ranges replace the matching rules for their time axis"
            )
        if not self.rules and not self.allowAll and not self.dateRange:
            raise ValueError("Provide a matching rule or explicitly allow all members")
        return self


class ResolutionValue(Schema):
    sourceColumnId: Identifier
    mode: Literal["split", "copy"]
    timeMode: Literal["split", "copy"] | None = None


class GrainResolution(Schema):
    axes: list[ResolutionAxis] = Field(min_length=1, max_length=32)
    values: list[ResolutionValue] = Field(min_length=1, max_length=32)
    orderColumnIds: list[Identifier] = Field(min_length=1, max_length=4)
    partitionColumnIds: list[Identifier] = Field(default_factory=list, max_length=8)
    passthroughColumnIds: list[Identifier] = Field(default_factory=list, max_length=32)
    overlapPolicy: Literal["last_applied_wins"] = "last_applied_wins"

    @model_validator(mode="after")
    def distinct(self):
        ranges = [a for a in self.axes if a.dateRange]
        if len(ranges) > 1:
            raise ValueError("Configure at most one date-range axis")
        if ranges and any(v.timeMode is None for v in self.values):
            raise ValueError("Select Across dates Split/Copy for every resolved value")
        unique([a.grainColumnId for a in self.axes], "resolution axes")
        unique([r.ruleId for a in self.axes for r in a.rules], "resolution rule IDs")
        unique([v.sourceColumnId for v in self.values], "resolution values")
        for fields in (
            self.orderColumnIds,
            self.partitionColumnIds,
            self.passthroughColumnIds,
        ):
            unique(fields, "resolution columns")
        if set(self.orderColumnIds) & {v.sourceColumnId for v in self.values}:
            raise ValueError(
                "Application order columns cannot also be distributed values"
            )
        if sum(len(a.rules) for a in self.axes) > 16:
            raise ValueError("Choose at most 16 matching rules")
        return self


class TransactionDefinition(Schema):
    filters: FilterDefinition | None = None
    sourceId: Identifier
    pivot: PivotStage | None = None
    dimensionId: Identifier | None = None
    grainColumnIds: list[Identifier] = Field(default_factory=list, max_length=32)
    timeColumnId: Identifier | None = None
    mappingMode: Literal["direct", "resolve_multi_grain"] = "direct"
    resolution: GrainResolution | None = None
    calendarResolution: CalendarResolution | None = None
    keyMappings: list[TransactionKeyMapping] = Field(
        default_factory=list, max_length=32
    )
    expectedRowsPerGrain: Literal["zero_or_one", "many"] = "many"
    unmatchedGrainPolicy: Literal["reject"] = "reject"

    @model_validator(mode="after")
    def validate_mapping(self):
        unique(self.grainColumnIds, "transaction grain columns")
        if self.dimensionId is None:
            if (
                not self.grainColumnIds
                or self.mappingMode != "direct"
                or self.resolution
                or self.keyMappings
            ):
                raise ValueError(
                    "Standalone Transactions require grain columns and no Dimension mappings or multi-grain resolution"
                )
            if self.timeColumnId and self.timeColumnId not in self.grainColumnIds:
                raise ValueError(
                    "The Time key must be one of the selected grain columns"
                )
        elif self.mappingMode == "direct" and (
            not self.keyMappings or self.resolution is not None
        ):
            raise ValueError(
                "Connected Transactions require complete key mappings and no grain resolution"
            )
        elif self.mappingMode == "resolve_multi_grain" and (
            self.resolution is None or self.keyMappings
        ):
            raise ValueError(
                "Multi-grain resolution requires a target Dimension and resolution metadata"
            )
        if self.calendarResolution and self.resolution:
            if any(a.dateRange for a in self.resolution.axes):
                raise ValueError(
                    "Use calendar resolution or legacy date-range axes, not both"
                )
            if any(v.timeMode is None for v in self.resolution.values):
                raise ValueError(
                    "Select Across dates Split/Copy for every resolved value"
                )
        unique([m.sourceColumnId for m in self.keyMappings], "transaction source keys")
        unique([m.grainColumnId for m in self.keyMappings], "transaction grain keys")
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if value.get("filters") is None:
            value.pop("filters", None)
        if value.get("pivot") is None:
            value.pop("pivot", None)
        if value.get("calendarResolution") is None:
            value.pop("calendarResolution", None)
        return value


class TransactionMeasureSelection(Schema):
    valueColumnId: Identifier
    editable: bool = False
    aggregation: Aggregation
    allocation: Allocation | None = Field(default_factory=Allocation)


class TransactionInput(SemanticInput):
    kind: Literal["transaction"] = "transaction"
    definition: TransactionDefinition

    @model_validator(mode="before")
    @classmethod
    def strip_computed_context(cls, value):
        if isinstance(value, dict) and "effectiveGrainId" in value:
            return {
                key: item for key, item in value.items() if key != "effectiveGrainId"
            }
        return value


class TransactionCreateInput(TransactionInput):
    measures: list[TransactionMeasureSelection] = Field(
        default_factory=list, max_length=128
    )

    @model_validator(mode="after")
    def unique_measure_columns(self):
        unique([measure.valueColumnId for measure in self.measures], "measure columns")
        return self


class TransactionDocument(TransactionInput, DocumentMetadata):
    @computed_field
    @property
    def effectiveGrainId(self) -> str:
        return self.definition.dimensionId or transaction_grain_id(self.objectId)


class TransactionFromGrainInput(SemanticInput):
    """Creation command only; no onboarding fields persist on semantic definitions."""

    kind: Literal["transaction"] = "transaction"
    sourceId: Identifier
    grainColumnIds: list[Identifier] = Field(min_length=1, max_length=32)
    dimensionName: Name | None = None
    measures: list[TransactionMeasureSelection] = Field(
        default_factory=list, max_length=128
    )
    filters: FilterDefinition | None = None
    pivot: PivotStage | None = None
    calendarResolution: CalendarResolution | None = None

    @model_validator(mode="after")
    def distinct_grain(self):
        unique(self.grainColumnIds, "grain columns")
        unique([measure.valueColumnId for measure in self.measures], "measure columns")
        return self
