from typing import Literal

from pydantic import Field, model_validator, model_serializer

from .common import DocumentMetadata, Identifier, Schema, SemanticInput, unique


class TransactionKeyMapping(Schema):
    sourceColumnId: Identifier
    grainColumnId: Identifier


class ResolutionTarget(Schema):
    dimensionId: Identifier | None = None
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
    dimensionId: Identifier
    calendarKeyColumnId: Identifier
    startColumnId: Identifier
    endColumnId: Identifier


class CalendarResolution(Schema):
    dimensionId: Identifier
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
    sourceId: Identifier
    planningLevelId: Identifier
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
        if self.mappingMode == "direct" and (
            not self.keyMappings or self.resolution is not None
        ):
            raise ValueError(
                "Direct transactions require key mappings and no grain resolution"
            )
        if self.mappingMode == "resolve_multi_grain" and (
            self.resolution is None or self.keyMappings
        ):
            raise ValueError(
                "Multi-grain transactions require resolution metadata; native keys are generated"
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
        if value.get("calendarResolution") is None:
            value.pop("calendarResolution", None)
        return value


class TransactionInput(SemanticInput):
    kind: Literal["transaction"] = "transaction"
    definition: TransactionDefinition


class TransactionDocument(TransactionInput, DocumentMetadata):
    pass
