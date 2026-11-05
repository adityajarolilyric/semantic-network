"""Object transformation previews use the same input operators as execution."""

from enum import StrEnum

from ..core.errors import MetadataError
from ..warehouse.repository import quote_identifier as quote_identifier
from .calendar_resolution import CalendarResolutionCompiler
from .filters import dimension_filter_columns, FilterCompiler
from .transaction_resolution import ResolvedTransaction


class PreviewTarget(StrEnum):
    PIVOT_INPUT = "pivot_input"
    CALENDAR_INPUT = "calendar_input"
    DIMENSION_OUTPUT = "dimension_output"
    TRANSACTION_OUTPUT = "transaction_output"
    UNMAPPED_TRANSACTION = "unmapped_transaction"


class InputPipelineCompiler:
    def __init__(self, context):
        self.context = context

    def compile(self, owner, target):
        if target == PreviewTarget.UNMAPPED_TRANSACTION:
            input_target = (
                PreviewTarget.CALENDAR_INPUT
                if owner["definition"].get("calendarResolution")
                else PreviewTarget.PIVOT_INPUT
            )
            resolved = self.compile(owner, input_target)
            filters = owner["definition"].get("filters")
            if not filters:
                return resolved
            from dataclasses import replace

            query = FilterCompiler.apply(
                "SELECT * FROM " + quote_identifier(resolved.node),
                filters,
                resolved.columns,
            )
            return replace(
                resolved, node=self.context.cte(query), unfiltered_node=resolved.node
            )
        if target == PreviewTarget.CALENDAR_INPUT:
            calendar = CalendarResolutionCompiler(self.context, owner)
            node = calendar.compile()
            return ResolvedTransaction(
                node, calendar.p.schema(), [], node, [node], node
            )
        relation = self.context.managed[owner["definition"]["sourceId"]]
        source_table, input_columns = self.context.object_input(owner, relation)
        if target == PreviewTarget.DIMENSION_OUTPUT:
            return self.compile_dimension(owner, source_table, input_columns)
        if target != PreviewTarget.PIVOT_INPUT:
            raise ValueError(f"Unsupported input preview target: {target}")
        node = self.context.cte(
            "SELECT "
            + ", ".join(
                quote_identifier(column["name"])
                + " AS "
                + quote_identifier(column["columnId"])
                for column in input_columns
            )
            + " FROM "
            + source_table
        )
        return ResolvedTransaction(node, input_columns, [], node, [node], node)

    def compile_dimension(self, owner, source_table, input_columns):
        columns_by_id = {column["columnId"]: column for column in input_columns}
        definition = owner["definition"]
        selected_columns = definition["columns"]
        if any(
            column["sourceColumnId"] not in columns_by_id for column in selected_columns
        ):
            raise MetadataError(
                "unknown_column",
                "A dimension column is missing from the transformed input.",
            )
        node = self.context.cte(
            "SELECT "
            + ", ".join(
                quote_identifier(columns_by_id[column["sourceColumnId"]]["name"])
                + " AS "
                + quote_identifier(column["columnId"])
                for column in selected_columns
            )
            + " FROM "
            + source_table
        )
        if definition.get("filters"):
            node = self.context.cte(
                "SELECT * FROM "
                + quote_identifier(node)
                + " WHERE "
                + self.context.filter_sql(
                    definition["filters"],
                    dimension_filter_columns(definition, input_columns),
                )
            )
        exposed = set(definition["keyColumnIds"]) | set(
            definition["attributeColumnIds"]
        )
        columns = [
            {
                **columns_by_id[column["sourceColumnId"]],
                "columnId": column["columnId"],
                "name": column["displayName"],
            }
            for column in selected_columns
            if column["columnId"] in exposed
        ]
        return ResolvedTransaction(node, columns, [], node, [node], node)
