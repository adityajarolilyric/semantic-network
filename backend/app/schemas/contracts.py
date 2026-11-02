from datetime import datetime

from pydantic import Field, JsonValue

from .common import Description, Identifier, Name, Schema


class ContractInput(Schema):
    contractId: Identifier
    name: Name
    contractVersion: int = Field(ge=1)
    description: Description
    definition: dict[str, JsonValue]


class ContractDocument(ContractInput):
    version: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
