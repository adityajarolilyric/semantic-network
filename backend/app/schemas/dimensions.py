from typing import Annotated, Literal, Union

from pydantic import Field, model_serializer, model_validator

from .common import DocumentMetadata, Identifier, Name, Schema, SemanticInput, unique
from .dimension_members import DimensionMembersDefinition
from .filters import FilterDefinition
from .managed_tables import PivotStage
from .transactions import CalendarResolution


class PopulationFactor(Schema):
    factorId: Identifier
    sourceId: Identifier
    selectedColumnIds: list[Identifier] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_selection(self):
        unique(self.selectedColumnIds, "factor selection")
        return self


class CartesianPopulation(Schema):
    operation: Literal["cartesian_product"]
    factors: list[PopulationFactor] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_factors(self):
        unique([factor.factorId for factor in self.factors], "factor IDs")
        return self


class ProjectionPopulation(Schema):
    operation: Literal["distinct_projection"]
    sourceDimensionId: Identifier


class PopulationInputTransform(Schema):
    # Stable namespace for generated pivot/calendar column IDs; not an object reference.
    objectId: Identifier
    pivot: PivotStage | None = None
    calendarResolution: CalendarResolution | None = None
    filters: FilterDefinition | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if value.get("filters") is None:
            value.pop("filters", None)
        return value


class SourcePopulation(Schema):
    operation: Literal["source"]
    sourceId: Identifier
    inputTransform: PopulationInputTransform | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if value.get("inputTransform") is None:
            value.pop("inputTransform", None)
        return value


Population = Annotated[
    Union[CartesianPopulation, ProjectionPopulation, SourcePopulation],
    Field(discriminator="operation"),
]


class PopulationReference(Schema):
    factorId: Identifier | None = None
    columnId: Identifier | None = None
    sourceGrainColumnId: Identifier | None = None
    sourceOutputColumnId: Identifier | None = None


class GrainColumn(Schema):
    grainColumnId: Identifier
    name: Name
    populationRef: PopulationReference


class Grain(Schema):
    keyIds: list[Identifier] = Field(min_length=1, max_length=32)
    columns: list[GrainColumn] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_grain(self):
        ids = [col.grainColumnId for col in self.columns]
        unique(ids, "grain column IDs")
        unique(self.keyIds, "grain key IDs")
        if not set(self.keyIds) <= set(ids):
            raise ValueError("grain keys must reference grain columns")
        return self


class PopulationAttribute(Schema):
    columnId: Identifier
    name: Name
    populationRef: PopulationReference


class DimensionKeyMapping(Schema):
    grainColumnId: Identifier | None = None
    populationColumnId: Identifier | None = None
    memberColumnId: Identifier

    @model_validator(mode="after")
    def one_left(self):
        if (self.grainColumnId is None) == (self.populationColumnId is None):
            raise ValueError("Choose exactly one population key or attribute mapping")
        return self


class MemberBinding(Schema):
    memberCatalogId: Identifier
    lookupCardinality: Literal["many_to_one"] = "many_to_one"
    keyMappings: list[DimensionKeyMapping] = Field(min_length=1, max_length=32)
    selectedAttributeColumnIds: list[Identifier] = Field(
        default_factory=list, max_length=2000
    )
    unmatchedMemberPolicy: Literal["reject"] = "reject"

    @model_validator(mode="after")
    def validate_mapping(self):
        unique(
            [m.grainColumnId or m.populationColumnId for m in self.keyMappings],
            "binding grain keys",
        )
        unique([m.memberColumnId for m in self.keyMappings], "binding dimension keys")
        unique(self.selectedAttributeColumnIds, "selected attributes")
        return self


class DimensionDefinition(Schema):
    population: Population
    grain: Grain
    populationAttributes: list[PopulationAttribute] = Field(
        default_factory=list, max_length=2000
    )
    memberBindings: list[MemberBinding] = Field(default_factory=list, max_length=32)
    filters: FilterDefinition | None = None
    duplicateKeyPolicy: Literal["reject"] = "reject"
    nullKeyPolicy: Literal["reject"] = "reject"

    @model_validator(mode="after")
    def validate_population_refs(self):
        unique([b.memberCatalogId for b in self.memberBindings], "dimension bindings")
        unique(
            [a.columnId for a in self.populationAttributes], "population attribute IDs"
        )
        if set(a.columnId for a in self.populationAttributes) & set(
            c.grainColumnId for c in self.grain.columns
        ):
            raise ValueError("Population attribute IDs must differ from grain IDs")
        selected_refs = [
            (
                c.populationRef.factorId,
                c.populationRef.columnId,
                c.populationRef.sourceGrainColumnId
                or c.populationRef.sourceOutputColumnId,
            )
            for c in [*self.grain.columns, *self.populationAttributes]
        ]
        if len(set(selected_refs)) != len(selected_refs):
            raise ValueError(
                "Select each population column only once as a key or attribute"
            )
        for col in [*self.grain.columns, *self.populationAttributes]:
            ref = col.populationRef
            if isinstance(self.population, ProjectionPopulation):
                valid = (
                    (
                        (ref.sourceGrainColumnId is not None)
                        != (ref.sourceOutputColumnId is not None)
                    )
                    and ref.columnId is None
                    and ref.factorId is None
                )
            elif isinstance(self.population, CartesianPopulation):
                factors = {f.factorId: f for f in self.population.factors}
                factor = factors.get(ref.factorId)
                valid = (
                    factor is not None
                    and ref.columnId in factor.selectedColumnIds
                    and ref.sourceGrainColumnId is None
                    and ref.sourceOutputColumnId is None
                )
            else:
                valid = (
                    ref.columnId is not None
                    and ref.factorId is None
                    and ref.sourceGrainColumnId is None
                    and ref.sourceOutputColumnId is None
                )
            if not valid:
                raise ValueError(
                    "grain populationRef does not match the population operation"
                )
        return self


class DimensionInput(SemanticInput):
    kind: Literal["dimension"] = "dimension"
    definition: DimensionDefinition
    memberCatalogId: Identifier | None = None
    memberCatalog: DimensionMembersDefinition | None = None
    isTimeDim: bool = False

    @model_validator(mode="after")
    def validate_member_catalog(self):
        if (self.memberCatalog is None) != (self.memberCatalogId is None):
            raise ValueError(
                "Member catalog identity and metadata must be provided together"
            )
        if self.memberCatalog and self.isTimeDim != (
            self.memberCatalog.time is not None
        ):
            raise ValueError(
                "Time Dimension metadata must agree with its calendar configuration"
            )
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if value.get("memberCatalogId") is None:
            value.pop("memberCatalogId", None)
        if value.get("memberCatalog") is None:
            value.pop("memberCatalog", None)
        return value


class DimensionDocument(DimensionInput, DocumentMetadata):
    pass


class DimensionDraftRequest(Schema):
    definition: DimensionInput
    expectedVersion: int | None = Field(default=None, ge=1)


class DimensionCommitRequest(Schema):
    definition: DimensionInput
    runId: Identifier
