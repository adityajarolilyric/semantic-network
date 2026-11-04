from pydantic import Field, model_validator, model_serializer
from .common import Identifier, Schema


class PlanningViewField(Schema):
    dimensionId: Identifier | None = None
    planningLevelId: Identifier | None = None
    columnId: Identifier

    @model_validator(mode="after")
    def one_owner(self):
        if (self.dimensionId is None) == (self.planningLevelId is None):
            raise ValueError(
                "Choose exactly one dimension or planning level for each field"
            )
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class PlanningViewRequest(Schema):
    rows: list[PlanningViewField] = Field(default_factory=list, max_length=8)
    columns: list[PlanningViewField] = Field(default_factory=list, max_length=8)
    measures: list[Identifier] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def distinct_fields(self):
        fields = [
            (f.dimensionId, f.planningLevelId, f.columnId)
            for f in [*self.rows, *self.columns]
        ]
        if len(fields) > 8 or len(fields) != len(set(fields)):
            raise ValueError(
                "Choose at most eight distinct fields across rows and columns."
            )
        if len(self.measures) != len(set(self.measures)):
            raise ValueError("Choose each measure once.")
        return self


class PlanningViewEdit(Schema):
    view: PlanningViewRequest
    measureId: Identifier
    memberValues: list[str | None] = Field(max_length=8)
    value: float | None
    generationId: Identifier
    revisionVersion: int = Field(ge=1)

    @model_validator(mode="after")
    def exact_cell(self):
        if len(self.memberValues) != len(self.view.rows) + len(self.view.columns):
            raise ValueError("Supply one member value per selected pivot field")
        if any(v is not None and len(v) > 65536 for v in self.memberValues):
            raise ValueError("A member value is too long")
        if self.measureId not in self.view.measures:
            raise ValueError("Edit a measure from the displayed view")
        return self
