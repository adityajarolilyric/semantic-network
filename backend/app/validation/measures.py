"""Focused semantic rules; diagnostics and graph state are owned by the shared context."""


class MeasureRules:
    def __init__(self, context):
        self.context = context

    def rolls_up(self, source_level: str, target_level: str) -> bool:
        from ..engine.relationships import LevelRelationshipAnalyzer

        return LevelRelationshipAnalyzer(list(self.context.objects.values())).analyze(
            source_level, target_level
        )["relationship"] in ("same", "equivalent", "roll_up")

    def available(self, measure: dict, target_level: str) -> bool:
        from ..engine.relationships import LevelRelationshipAnalyzer

        definition = measure["definition"]
        relation = LevelRelationshipAnalyzer(
            list(self.context.objects.values())
        ).analyze(definition["nativePlanningLevelId"], target_level)["relationship"]
        if relation in ("same", "equivalent", "roll_up"):
            return True
        if target_level == self.context.base and relation == "roll_down":
            return True
        analyzer = LevelRelationshipAnalyzer(list(self.context.objects.values()))
        return bool(
            self.context.base
            and all(
                analyzer.analyze(self.context.base, lid)["relationship"]
                in ("same", "equivalent", "roll_up")
                for lid in (definition["nativePlanningLevelId"], target_level)
            )
        )

    def validate_measure(self, obj: dict):
        definition = obj["definition"]
        level = self.context.reference(
            obj,
            definition["nativePlanningLevelId"],
            "planningLevel",
            "definition.nativePlanningLevelId",
        )
        if definition["measureType"] == "referenced":
            transaction = self.context.reference(
                obj,
                definition["transactionId"],
                "transaction",
                "definition.transactionId",
            )
            if transaction:
                tx = transaction["definition"]
                if tx["planningLevelId"] != definition["nativePlanningLevelId"]:
                    self.context.error(
                        obj,
                        "measure_grain_mismatch",
                        "Referenced measure and transaction must have the same native planning level.",
                    )
                if tx.get("mappingMode") == "resolve_multi_grain" and definition[
                    "valueColumnId"
                ] not in {v["sourceColumnId"] for v in tx["resolution"]["values"]}:
                    self.context.error(
                        obj,
                        "unresolved_measure_column",
                        "Choose a value column configured for Split/Copy in this transaction.",
                        "definition.valueColumnId",
                    )
                source = self.context.objects.get(tx["sourceId"])
                if source and source["kind"] == "source":
                    columns = self.context.columns(source)
                    if tx.get("calendarResolution") or tx.get("pivot"):
                        from ..core.errors import MetadataError
                        from ..engine.calendar_resolution import (
                            CalendarResolutionPlanner,
                        )

                        try:
                            columns = {
                                c["columnId"]: c
                                for c in CalendarResolutionPlanner(
                                    list(self.context.objects.values()),
                                    transaction,
                                    list(columns.values()),
                                ).schema()
                            }
                        except MetadataError:
                            pass  # The transaction reports its calendar contract error.
                    self.context.require_columns(
                        obj,
                        [definition["valueColumnId"]],
                        columns,
                        "definition.valueColumnId",
                    )
                    col = columns.get(definition["valueColumnId"])
                    if col:
                        if not self.context.numeric_type(col["physicalType"]):
                            self.context.error(
                                obj,
                                "non_numeric_measure",
                                "This version supports numeric measure source columns only.",
                            )
                        if col.get("unit") and col["unit"] != obj["business"]["unit"]:
                            self.context.error(
                                obj,
                                "unit_mismatch",
                                "Measure unit must match the declared source column unit.",
                            )
        else:
            self.context.validate_expression(
                obj, definition["formula"]["expression"], level, depth=0
            )
            unit = self.context.expression_unit(
                obj, definition["formula"]["expression"]
            )
            if unit is not None and unit != obj["business"]["unit"]:
                self.context.error(
                    obj,
                    "unit_mismatch",
                    f"Formula unit {unit} does not match result unit {obj['business']['unit']}.",
                )

        self.context.validate_allocation(obj)

    def validate_allocation(self, obj):
        definition = obj["definition"]
        allocation = definition.get("allocation") or {"mode": "none"}
        if allocation["mode"] == "none":
            return
        # A policy can be configured before a path to Base exists. It remains
        # dormant until a supported propagation/edit path actually uses it.
        if allocation["mode"] == "weighted":
            basis = self.context.reference(
                obj,
                allocation["basisMeasureId"],
                "measure",
                "definition.allocation.basisMeasureId",
            )
            if basis and (
                basis["definition"]["nativePlanningLevelId"] != self.context.base
                or basis["definition"].get("calculationType") == "AggregateFirst"
            ):
                self.context.error(
                    obj,
                    "invalid_allocation_basis",
                    "Dis-aggregation basis must be a stored measure native on Base.",
                )
