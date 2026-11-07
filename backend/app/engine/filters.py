"""Typed, bounded filter trees compiled against stable output column IDs."""

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError
from sqlglot import exp

from ..core.errors import MetadataError
from ..schemas.filters import FilterDefinition
from ..warehouse.sql import quote_identifier as q

NULL_OPS = {"is_null", "is_not_null"}
EMPTY_OPS = {"is_empty", "is_not_empty"}
COMPARE_OPS = {"eq", "ne", "in", "not_in"}
ORDER_OPS = {"gt", "gte", "lt", "lte", "between", "not_between"}
TEXT_OPS = {
    "contains",
    "not_contains",
    "starts_with",
    "not_starts_with",
    "ends_with",
    "not_ends_with",
}


def filter_type(physical_type):
    typ = physical_type or ""
    while typ.startswith(("Nullable(", "LowCardinality(")):
        typ = typ[typ.index("(") + 1 : -1]
    if typ in {"Bool", "Boolean"}:
        return "boolean", typ
    if re.fullmatch(
        r"(?:U?Int(?:8|16|32|64|128|256)|Float(?:32|64)|Decimal(?:32|64|128|256)?\(\d+(?:,\s*\d+)?\))",
        typ,
    ):
        return "number", typ
    if typ in {"Date", "Date32"} or re.fullmatch(
        r"DateTime(?:64\([0-9](?:,\s*'[A-Za-z0-9_+./-]+')?\)|\('[A-Za-z0-9_+./-]+'\))?",
        typ,
    ):
        return "date", typ
    if typ == "String" or re.fullmatch(r"FixedString\(\d+\)", typ):
        return "string", typ
    return "unknown", typ


class FilterCompiler:
    """No SQL from users: only checked operators, identifiers and typed literals."""

    @staticmethod
    def columns(columns):
        if isinstance(columns, dict):
            return {
                cid: value.get("physicalType") if isinstance(value, dict) else value
                for cid, value in columns.items()
            }
        return {
            column["columnId"] if isinstance(column, dict) else column: column.get(
                "physicalType"
            )
            if isinstance(column, dict)
            else None
            for column in columns
        }

    @classmethod
    def compile(cls, definition, columns):
        if not definition:
            return "1"
        try:
            definition = FilterDefinition.model_validate(definition).model_dump()
        except ValidationError as error:
            raise MetadataError(
                "invalid_filter",
                "Complete every filter rule and keep groups within the supported limits.",
            ) from error
        available = cls.columns(columns)

        def visit(node):
            if node["type"] == "group":
                return (
                    "("
                    + (" AND " if node["combinator"] == "and" else " OR ").join(
                        visit(c) for c in node["children"]
                    )
                    + ")"
                )
            cid, op = node["columnId"], node["operator"]
            if cid not in available:
                raise MetadataError(
                    "invalid_filter",
                    "Filter references an unavailable output column. Remove or update that rule.",
                )
            kind, typ = filter_type(available[cid])
            allowed = NULL_OPS | COMPARE_OPS
            if kind in {"number", "date", "unknown"}:
                allowed |= ORDER_OPS
            if kind in {"string", "unknown"}:
                allowed |= TEXT_OPS | EMPTY_OPS
            if op not in allowed:
                raise MetadataError(
                    "invalid_filter",
                    "The selected operator is not supported for this column type.",
                )
            col = q(cid)
            if op in NULL_OPS:
                return f"{col} IS {'NOT ' if op == 'is_not_null' else ''}NULL"
            values = [cls.literal(value, kind, typ) for value in node["values"]]
            if op in EMPTY_OPS:
                predicate = f"{col} {'!=' if op == 'is_not_empty' else '='} ''"
            elif op in {"in", "not_in"}:
                predicate = (
                    f"{col} {'NOT ' if op == 'not_in' else ''}IN ({', '.join(values)})"
                )
            elif op in {"between", "not_between"}:
                predicate = f"{col} {'NOT ' if op == 'not_between' else ''}BETWEEN {values[0]} AND {values[1]}"
            elif op in TEXT_OPS:
                if op.endswith("contains"):
                    predicate = f"position(toString({col}), {values[0]}) {'=' if op.startswith('not_') else '>'} 0"
                else:
                    fn = "startsWith" if op.endswith("starts_with") else "endsWith"
                    predicate = f"{'NOT ' if op.startswith('not_') else ''}{fn}(toString({col}), {values[0]})"
            else:
                symbol = {
                    "eq": "=",
                    "ne": "!=",
                    "gt": ">",
                    "gte": ">=",
                    "lt": "<",
                    "lte": "<=",
                }[op]
                predicate = f"{col} {symbol} {values[0]}"
            # ClickHouse NOT IN treats NULL differently from comparisons; keep rules consistent.
            return f"(isNotNull({col}) AND ({predicate}))"

        return visit(definition["root"])

    @staticmethod
    def literal(value, kind, typ):
        try:
            if kind == "number":
                if isinstance(value, bool):
                    raise ValueError()
                number = Decimal(str(value))
                if not number.is_finite() or (
                    typ.startswith("Float") and not math.isfinite(float(number))
                ):
                    raise ValueError()
                return str(number)
            if kind == "boolean":
                if type(value) is not bool:
                    raise ValueError()
            if kind == "string" and not isinstance(value, str):
                raise ValueError()
            if kind == "date":
                if not isinstance(value, str):
                    raise ValueError()
                if typ in {"Date", "Date32"}:
                    value = date.fromisoformat(value).isoformat()
                else:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo:
                        return f"parseDateTime64BestEffort({exp.convert(value).sql(dialect='clickhouse')}, 6, 'UTC')"
                    value = parsed.isoformat(sep=" ")
                return f"CAST({exp.convert(value).sql(dialect='clickhouse')} AS {typ})"
            return exp.convert(value).sql(dialect="clickhouse")
        except (ValueError, TypeError, InvalidOperation) as error:
            raise MetadataError(
                "invalid_filter_value", f"Enter a valid {kind} value for this filter."
            ) from error

    @classmethod
    def apply(cls, sql, definition, columns):
        return (
            f"SELECT * FROM ({sql}) WHERE {cls.compile(definition, columns)}"
            if definition
            else sql
        )


def dimension_filter_columns(definition, input_columns):
    columns = {c["columnId"]: c for c in input_columns}
    return [
        {**columns.get(c["sourceColumnId"], {}), "columnId": c["columnId"]}
        for c in definition["columns"]
    ]
