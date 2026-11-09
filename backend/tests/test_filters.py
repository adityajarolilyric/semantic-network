import pytest
from pydantic import ValidationError

from backend.app.core.errors import MetadataError
from backend.app.engine.transformations.filters import FilterCompiler
from backend.app.schemas.filters import FilterDefinition
from backend.app.schemas.transactions import TransactionDefinition


def rule(cid, op, *values):
    return {
        "type": "condition",
        "columnId": cid,
        "operator": op,
        "values": list(values),
    }


def filters(*children, combinator="and"):
    return {
        "root": {"type": "group", "combinator": combinator, "children": list(children)}
    }


@pytest.mark.parametrize(
    "operator,values",
    [
        ("eq", ["O'Reilly"]),
        ("ne", ["x"]),
        ("in", ["x", "y"]),
        ("not_in", ["x"]),
        ("contains", ["x%_"]),
        ("not_contains", ["x"]),
        ("starts_with", ["x"]),
        ("not_starts_with", ["x"]),
        ("ends_with", ["x"]),
        ("not_ends_with", ["x"]),
        ("is_empty", []),
        ("is_not_empty", []),
        ("is_null", []),
        ("is_not_null", []),
    ],
)
def test_string_operators_and_literal_escaping(operator, values):
    sql = FilterCompiler().compile(
        filters(rule("name", operator, *values)), {"name": "Nullable(String)"}
    )
    assert "`name`" in sql
    assert "DROP" not in sql
    if operator == "contains":
        assert "LIKE" not in sql and "x%_" in sql
    if operator == "eq":
        assert "O'Reilly" not in sql  # quote must be escaped


@pytest.mark.parametrize(
    "typ,op,value",
    [
        ("Float64", "contains", "12"),
        ("Bool", "gt", True),
        ("Date", "eq", "2026-02-30"),
        ("Float64", "eq", "NaN"),
        ("Float64", "eq", "1e9999"),
        ("Float64", "eq", "12); DROP TABLE x"),
        ("Bool", "eq", "true"),
        ("String", "eq", 3),
        ("Float64", "eq", True),
    ],
)
def test_rejects_incompatible_operators_and_values(typ, op, value):
    with pytest.raises(MetadataError):
        FilterCompiler().compile(filters(rule("value", op, value)), {"value": typ})


def test_numeric_date_boolean_and_nested_groups():
    definition = filters(
        rule("qty", "between", "1.25", "200"),
        filters(
            rule("day", "gte", "2026-01-01"),
            rule("active", "eq", True),
            combinator="or",
        )["root"],
    )
    sql = FilterCompiler().compile(
        definition,
        {"qty": "Nullable(Decimal(38, 6))", "day": "Date32", "active": "Bool"},
    )
    assert " AND " in sql and " OR " in sql
    assert "CAST('2026-01-01' AS Date32)" in sql
    assert "BETWEEN 1.25 AND 200" in sql
    assert FilterCompiler().compile(None, {}) == "1"


def test_invalid_columns_arity_and_tree_limits():
    with pytest.raises(MetadataError):
        FilterCompiler().compile(
            filters(rule("missing", "eq", "x")), {"name": "String"}
        )
    with pytest.raises(ValidationError):
        FilterDefinition.model_validate(filters(rule("qty", "between", 1)))
    tree = filters(rule("name", "eq", "x"))
    for _ in range(17):
        tree = filters(tree["root"])
    with pytest.raises(ValidationError):
        FilterDefinition.model_validate(tree)


def test_absent_transaction_filters_preserve_old_serialization():
    old = {
        "sourceId": "source",
        "dimensionId": "level",
        "keyMappings": [{"sourceColumnId": "key", "grainColumnId": "key"}],
    }
    result = TransactionDefinition.model_validate(old).model_dump()
    assert "filters" not in result


@pytest.mark.parametrize("children", [None, "invalid", 1])
def test_malformed_children_are_validation_errors(children):
    with pytest.raises(ValidationError):
        FilterDefinition.model_validate(
            {"root": {"type": "group", "combinator": "and", "children": children}}
        )
