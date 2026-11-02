from typing import Literal

from pydantic import Field, model_validator

from .common import (
    Description,
    DocumentMetadata,
    Identifier,
    Name,
    ObjectInput,
    Schema,
    unique,
)


class SourceColumn(Schema):
    columnId: Identifier
    name: Name
    physicalType: Name
    nullable: bool = False
    description: Description = ""
    unit: Name | None = None


class SourceDefinition(Schema):
    tableName: Name
    inScope: bool = True
    keyColumns: list[Identifier] = Field(default_factory=list, max_length=32)
    columns: list[SourceColumn] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_columns(self):
        ids = [col.columnId for col in self.columns]
        unique(ids, "column IDs")
        unique([col.name for col in self.columns], "physical column names")
        unique(self.keyColumns, "keyColumns")
        if not set(self.keyColumns) <= set(ids):
            raise ValueError("keyColumns must reference source columns")
        if any(col.nullable for col in self.columns if col.columnId in self.keyColumns):
            raise ValueError("source key columns cannot be nullable")
        return self


class SourceInput(ObjectInput):
    kind: Literal["source"] = "source"
    definition: SourceDefinition


class SourceDocument(SourceInput, DocumentMetadata):
    pass


class SourceRegistration(Schema):
    objectId: Identifier | None = None
    tableName: Name
    keyColumnNames: list[Name] = Field(default_factory=list, max_length=32)
    inScope: bool = True
    description: Description = ""

    @model_validator(mode="after")
    def validate_keys(self):
        unique(self.keyColumnNames, "key column names")
        return self
