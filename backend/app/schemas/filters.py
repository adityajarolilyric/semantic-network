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
        "not_contains",
        "starts_with",
        "not_starts_with",
        "ends_with",
        "not_ends_with",
        "not_between",
        "is_empty",
        "is_not_empty",
        "is_null",
        "is_not_null",
    ]
    values: list[str | int | float | bool] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_arity(self):
        n = len(self.values)
        if self.operator in {"is_null", "is_not_null", "is_empty", "is_not_empty"}:
            valid = n == 0
        elif self.operator in {"between", "not_between"}:
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

    @model_validator(mode="before")
    @classmethod
    def bounded_tree(cls, value):
        if not isinstance(value, dict):
            return value
        pending, count = [(value.get("root"), 0)], 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if depth > 16 or count > 1000:
                raise ValueError(
                    "Filter trees support at most 16 levels and 1000 nodes"
                )
            if isinstance(node, dict) and isinstance(node.get("children"), list):
                pending.extend((child, depth + 1) for child in node["children"])
        return value
