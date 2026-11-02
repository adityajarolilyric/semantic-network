from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .common import Identifier, Schema


class FilterCondition(Schema):
    type: Literal["condition"]
    columnId: Identifier
    operator: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "between",
        "contains",
        "is_null",
        "is_not_null",
    ]
    values: list[str | int | float | bool] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_arity(self):
        n = len(self.values)
        if self.operator in {"is_null", "is_not_null"}:
            valid = n == 0
        elif self.operator == "between":
            valid = n == 2
        elif self.operator in {"in", "not_in"}:
            valid = n >= 1
        else:
            valid = n == 1
        if not valid:
            raise ValueError("filter operator has an invalid number of values")
        return self


class FilterGroup(Schema):
    type: Literal["group"] = "group"
    combinator: Literal["and", "or"]
    children: list["FilterNode"] = Field(min_length=1, max_length=100)


FilterNode = Annotated[Union[FilterCondition, FilterGroup], Field(discriminator="type")]
FilterGroup.model_rebuild()


class FilterDefinition(Schema):
    root: FilterGroup
