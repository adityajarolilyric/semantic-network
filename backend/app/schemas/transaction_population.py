"""Authoring commands; population definitions remain reusable Dimension metadata."""

from pydantic import Field, model_validator

from .common import Schema
from .dimensions import DimensionInput
from .transactions import TransactionCreateInput


class TransactionPopulationCommand(Schema):
    transaction: TransactionCreateInput
    population: DimensionInput | None = None
    expectedVersion: int | None = Field(default=None, ge=1)
    expectedPopulationVersion: int | None = Field(default=None, ge=1)
    expectedRevisionVersion: int = Field(ge=1)
    useAsBase: bool = False

    @model_validator(mode="after")
    def connected_population(self):
        target = self.transaction.definition.dimensionId
        if target == self.transaction.objectId:
            raise ValueError("Transaction and population must have distinct identities")
        if not target or (self.population and self.population.objectId != target):
            raise ValueError("Select a population and map the Transaction to it")
        return self
