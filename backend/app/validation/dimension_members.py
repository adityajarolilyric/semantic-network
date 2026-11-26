"""Focused semantic rules; diagnostics and graph state are owned by the shared context."""


class DimensionMembersRules:
    def __init__(self, context):
        self.context = context

    def validate_dimensionMembers(self, obj: dict):
        definition = obj["definition"]
        source = self.context.source(obj, definition["sourceId"])
        if source:
            self.context.require_columns(
                obj,
                [c["sourceColumnId"] for c in definition["columns"]],
                self.context.object_columns(obj, source),
                "definition.columns",
            )
            mapping = {
                c["columnId"]: c["sourceColumnId"] for c in definition["columns"]
            }
            if any(
                self.context.object_columns(obj, source)
                .get(mapping[k], {})
                .get("nullable", False)
                for k in definition["keyColumnIds"]
            ):
                self.context.error(
                    obj,
                    "nullable_dimension_key",
                    "Dimension key columns cannot be nullable.",
                )
        self.context.validate_filters(
            obj,
            definition.get("filters"),
            {
                c["columnId"]: self.context.dimension_type(obj, c["columnId"])
                for c in definition["columns"]
            },
        )
