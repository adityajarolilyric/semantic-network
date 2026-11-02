from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import Description, Identifier, Schema


class RevisionCreate(Schema):
    revisionId: Identifier
    expectedModelVersion: int = Field(ge=1)
    parentRevisionId: Identifier | None = None
    basePlanningLevelId: Identifier | None = None
    changeReason: Description = ""
    metadataContractVersion: int = Field(default=1, ge=1, le=2)


class RevisionUpdate(Schema):
    basePlanningLevelId: Identifier | None
    changeReason: Description


class PublishRevision(Schema):
    expectedModelVersion: int = Field(ge=1)


class RevisionDocument(Schema):
    modelId: Identifier
    revisionId: Identifier
    revisionNumber: int = Field(ge=1)
    parentRevisionId: Identifier | None
    basePlanningLevelId: Identifier | None
    changeReason: Description
    metadataContractVersion: int
    approvalStatus: Literal["draft", "published"]
    version: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    publishedAt: datetime | None = None
