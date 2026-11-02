from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .common import DocumentMetadata, Identifier, Name, Schema, SemanticInput, unique
from .filters import FilterDefinition


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
    sourcePlanningLevelId: Identifier


class SourcePopulation(Schema):
    operation: Literal["source"]
    sourceId: Identifier


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
    dimensionColumnId: Identifier

    @model_validator(mode="after")
    def one_left(self):
        if (self.grainColumnId is None) == (self.populationColumnId is None):
            raise ValueError("Choose exactly one population key or attribute mapping")
        return self


class DimensionBinding(Schema):
    dimensionId: Identifier
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
        unique(
            [m.dimensionColumnId for m in self.keyMappings], "binding dimension keys"
        )
        unique(self.selectedAttributeColumnIds, "selected attributes")
        return self


class PlanningLevelDefinition(Schema):
    population: Population
    grain: Grain
    populationAttributes: list[PopulationAttribute] = Field(
        default_factory=list, max_length=2000
    )
    dimensionBindings: list[DimensionBinding] = Field(
        default_factory=list, max_length=32
    )
    filters: FilterDefinition | None = None
    duplicateKeyPolicy: Literal["reject"] = "reject"
    nullKeyPolicy: Literal["reject"] = "reject"

    @model_validator(mode="after")
    def validate_population_refs(self):
        unique([b.dimensionId for b in self.dimensionBindings], "dimension bindings")
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


class PlanningLevelInput(SemanticInput):
    kind: Literal["planningLevel"] = "planningLevel"
    definition: PlanningLevelDefinition


class PlanningLevelDocument(PlanningLevelInput, DocumentMetadata):
    pass


class PlanningLevelDraftRequest(Schema):
    definition: PlanningLevelInput
    expectedVersion: int | None = Field(default=None, ge=1)


class PlanningLevelCommitRequest(Schema):
    definition: PlanningLevelInput
    runId: Identifier
