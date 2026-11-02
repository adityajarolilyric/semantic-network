from datetime import datetime

from pydantic import Field

from .common import Description, Identifier, Name, Schema


class ModelCreate(Schema):
    modelId: Identifier
    name: Name
    displayName: Name
    description: Description = ""


class ModelUpdate(Schema):
    name: Name
    displayName: Name
    description: Description = ""


class ModelDocument(ModelCreate):
    draftRevisionId: Identifier | None = None
    publishedRevisionId: Identifier | None = None
    revisionCounter: int = 0
    version: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
