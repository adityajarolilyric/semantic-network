"""Pure SQL planning for resolved-transaction previews and diagnostics."""

from dataclasses import dataclass

from sqlglot import parse_one

from ..core.errors import MetadataError
from ..warehouse.sql import quote_identifier
from .contracts import Check, ExecutionPlan, RelationSchema


@dataclass(frozen=True)
class ResolutionPreviewPlan:
    rows_sql: str
    input_count_sql: str
    expanded_count_sql: str | None
    matched_count_sql: str
    resolved_count_sql: str
    winner_count_sql: str | None
    latest_count_sql: tuple[str, ...]
    profile_count: int

    @classmethod
    def compile(cls, compiler, sql, source_table_sql, resolved, profile_count):
        def count(node):
            return compiler.with_sql("SELECT count() FROM " + quote_identifier(node))

        return cls(
            sql + " ORDER BY ALL LIMIT 50",
            "SELECT count() FROM " + source_table_sql,
            count(resolved.input_node) if resolved.input_node else None,
            count(resolved.matches),
            count(resolved.node),
            count(resolved.unfiltered_node) if resolved.unfiltered_node else None,
            tuple(count(node) for node in resolved.latest_nodes),
            profile_count,
        )


@dataclass(frozen=True)
class CompiledResolutionPreview:
    execution: ExecutionPlan
    queries: ResolutionPreviewPlan


def compile_resolution_preview(
    compiler, resolved, source_table_name, profile_count, max_sql_bytes
):
    sql = compiler.with_sql(
        "SELECT "
        + ", ".join(quote_identifier(column["columnId"]) for column in resolved.columns)
        + " FROM "
        + quote_identifier(resolved.node)
    )
    if len(sql.encode()) > max_sql_bytes:
        raise MetadataError(
            "resolution_sql_limit",
            "The resolution plan is too complex. Reduce matching profiles.",
        )
    checks = tuple(
        Check(check.code, compiler.with_sql(check.sql)) for check in compiler.assertions
    )
    execution = ExecutionPlan(
        parse_one(sql, read="clickhouse"),
        checks,
        RelationSchema(tuple(resolved.columns)),
    )
    queries = ResolutionPreviewPlan.compile(
        compiler, sql, compiler.table(source_table_name), resolved, profile_count
    )
    return CompiledResolutionPreview(execution, queries)
