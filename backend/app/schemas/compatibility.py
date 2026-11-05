"""Bounded, read-only graph requests; no graph state is persisted."""

from pydantic import Field, model_validator
from .common import Identifier, Schema
from .planning_view import PlanningViewRequest


class CompatibilityView(PlanningViewRequest):
    measures: list[Identifier] = Field(default_factory=list, max_length=12)


class CompatibilityRequest(Schema):
    measures: list[Identifier] = Field(default_factory=list, max_length=12)
    planningLevelId: Identifier | None = None
    view: CompatibilityView | None = None
    search: str = Field(default="", max_length=200)
    measureOffset: int = Field(default=0, ge=0, le=5000)
    levelOffset: int = Field(default=0, ge=0, le=5000)
    measureLimit: int = Field(default=24, ge=1, le=24)
    levelLimit: int = Field(default=8, ge=1, le=8)

    @model_validator(mode="after")
    def unique_selection(self):
        if len(set(self.measures)) != len(self.measures):
            raise ValueError("Select each measure once.")
        if self.view and set(self.measures) != set(self.view.measures):
            raise ValueError("The view must contain exactly the selected measures.")
        if self.view and self.planningLevelId:
            raise ValueError(
                "Choose a planning-level context or a view context, not both."
            )
        return self
