from pydantic import Field, model_validator
from .common import Identifier, Schema


class PlanningViewField(Schema):
    dimensionId: Identifier
    columnId: Identifier


class PlanningViewRequest(Schema):
    rows: list[PlanningViewField] = Field(default_factory=list, max_length=8)
    columns: list[PlanningViewField] = Field(default_factory=list, max_length=8)
    measures: list[Identifier] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def distinct_fields(self):
        fields = [(f.dimensionId, f.columnId) for f in [*self.rows, *self.columns]]
        if len(fields) > 8 or len(fields) != len(set(fields)):
            raise ValueError(
                "Choose at most eight distinct dimension attributes across rows and columns."
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
