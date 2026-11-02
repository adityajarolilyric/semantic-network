from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .common import Description, DocumentMetadata, Identifier, Name, Schema, unique


class EvidenceInput(Schema):
    recordId: Identifier
    objectIds: list[Identifier] = Field(min_length=1, max_length=100)
    rule: Name
    result: Literal["passed", "failed", "unknown"]
    description: Description = ""
    checkedAt: AwareDatetime
    sourceFingerprint: str = Field(min_length=1, max_length=512)
    scope: Literal["full", "sample", "declaration"]
    observations: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence(self):
        unique(self.objectIds, "evidence object IDs")
        if self.scope == "declaration" and self.result == "passed":
            raise ValueError("A declaration is not evidence of a passed data check")
        return self


class EvidenceDocument(EvidenceInput, DocumentMetadata):
    pass
