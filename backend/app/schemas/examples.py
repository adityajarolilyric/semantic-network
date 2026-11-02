from pydantic import Field, JsonValue, model_validator

from .common import Description, DocumentMetadata, Identifier, Name, Schema, unique


class Fixture(Schema):
    sourceId: Identifier
    rows: list[dict[str, JsonValue]] = Field(max_length=100)


class ExampleInput(Schema):
    recordId: Identifier
    name: Name
    objectIds: list[Identifier] = Field(min_length=1, max_length=100)
    businessQuestion: Description
    fixtures: list[Fixture] = Field(default_factory=list, max_length=16)
    expectedGrainColumnIds: list[Identifier] = Field(
        default_factory=list, max_length=32
    )
    expectedRows: list[dict[str, JsonValue]] = Field(
        default_factory=list, max_length=100
    )
    rationale: Description

    @model_validator(mode="after")
    def validate_refs(self):
        unique(self.objectIds, "example object IDs")
        if not {fixture.sourceId for fixture in self.fixtures} <= set(self.objectIds):
            raise ValueError("fixture sources must be included in objectIds")
        return self


class ExampleDocument(ExampleInput, DocumentMetadata):
    pass
