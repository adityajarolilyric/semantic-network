from datetime import datetime
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
]
Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Description = Annotated[str, StringConstraints(max_length=4000)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ObjectInput(Schema):
    objectId: Identifier
    name: Name
    description: Description = ""
    schemaVersion: int = Field(default=1, ge=1, le=1)


class SemanticInput(ObjectInput):
    displayName: Name


class DocumentMetadata(Schema):
    modelId: Identifier
    revisionId: Identifier
    version: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime


T = TypeVar("T")


class Page(Schema, Generic[T]):
    items: list[T]
    nextCursor: str | None = None


class Diagnostic(Schema):
    code: str
    message: str
    objectId: str | None = None
    path: str | None = None


class ValidationReport(Schema):
    valid: bool
    diagnostics: list[Diagnostic]


def unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")
