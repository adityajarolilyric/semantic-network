"""Focused semantic rules; diagnostics and graph state are owned by the shared context."""

from ..core.errors import MetadataError
from ..engine.calendar_resolution import CalendarResolutionPlanner
from ..engine.transaction_resolution import TransactionResolutionPlanner


class TransactionRules:
    def __init__(self, context):
        self.context = context

    def validate_transaction(self, obj: dict):
        definition = obj["definition"]
        source = self.context.source(obj, definition["sourceId"])
        level = (
            self.context.reference(
                obj, definition["dimensionId"], "dimension", "definition.dimensionId"
            )
            if definition.get("dimensionId")
            else self.context.grain_reference(
                obj, definition["nativeGrainId"], "definition.grainColumnIds"
            )
        )
        if (definition.get("calendarResolution") or definition.get("pivot")) and source:
            try:
                calendar = CalendarResolutionPlanner(
                    list(self.context.objects.values()),
                    obj,
                    list(self.context.columns(source).values()),
                )
                source = {
                    **source,
                    "definition": {
                        **source["definition"],
                        "columns": calendar.schema(),
                    },
                }
            except MetadataError as error:
                self.context.error(
                    obj, error.code, error.message, "definition.calendarResolution"
                )
                return
        if definition.get("mappingMode") == "resolve_multi_grain":
            if source and level:
                try:
                    planner = TransactionResolutionPlanner(
                        list(self.context.objects.values()),
                        obj,
                        list(
                            self.context.columns(
                                self.context.objects[definition["sourceId"]]
                            ).values()
                        ),
                    )
                    population = [
                        {
                            "columnId": cid,
                            "physicalType": self.context.grain_type(level, cid),
                        }
                        for cid in level["definition"]["grain"]["keyIds"]
                    ]
                    self.context.validate_filters(
                        obj, definition.get("filters"), planner.schema(population)
                    )
                except MetadataError as error:
                    self.context.error(
                        obj, error.code, error.message, "definition.resolution"
                    )
            return
        mappings = definition["keyMappings"]
        if source:
            self.context.validate_filters(
                obj, definition.get("filters"), self.context.columns(source)
            )
            self.context.require_columns(
                obj,
                [m["sourceColumnId"] for m in mappings],
                self.context.columns(source),
                "definition.keyMappings",
            )
            if (
                definition["expectedRowsPerGrain"] == "zero_or_one"
                and source["definition"]["keyColumns"]
            ) and set(source["definition"]["keyColumns"]) != {
                m["sourceColumnId"] for m in mappings
            }:
                self.context.error(
                    obj,
                    "transaction_grain_mismatch",
                    "A zero_or_one transaction must map exactly its declared source key.",
                )
        if level:
            if set(level["definition"]["grain"]["keyIds"]) != {
                m["grainColumnId"] for m in mappings
            }:
                self.context.error(
                    obj,
                    "incomplete_transaction_key",
                    "Transactions must map every native grain key exactly once.",
                )
            if source:
                for mapping in mappings:
                    self.context.compatible(
                        obj,
                        self.context.columns(source)
                        .get(mapping["sourceColumnId"], {})
                        .get("physicalType"),
                        self.context.grain_type(level, mapping["grainColumnId"]),
                        "definition.keyMappings",
                    )
