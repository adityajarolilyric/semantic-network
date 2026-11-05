"""Focused semantic rules; diagnostics and graph state are owned by the shared context."""


class MeasureRules:
    def __init__(self, context):
        self.context = context

    def rolls_up(self, source_level: str, target_level: str) -> bool:
        from ..engine.relationships import LevelRelationshipAnalyzer

        return LevelRelationshipAnalyzer(list(self.context.objects.values())).analyze(
            source_level, target_level
        )["relationship"] in ("same", "equivalent", "roll_up")

    def available(self, measure: dict, target_level: str, seen=frozenset()) -> bool:
        from ..engine.relationships import LevelRelationshipAnalyzer

        definition = measure["definition"]
        from ..formula_language.expressions import contains_counts

        if contains_counts((definition.get("formula") or {}).get("expression")):
            counting = definition["nativePlanningLevelId"]
            return bool(counting and self.rolls_up(counting, target_level))
        relation = LevelRelationshipAnalyzer(
            list(self.context.objects.values())
        ).analyze(definition["nativePlanningLevelId"], target_level)["relationship"]
        if relation in ("same", "equivalent", "roll_up"):
            return True
        from ..domain.typed_values import is_numeric

        if not is_numeric(definition):
            if (
                definition.get("calculationType") != "AggregateFirst"
                or measure["objectId"] in seen
            ):
                return False
            analyzer = LevelRelationshipAnalyzer(list(self.context.objects.values()))
            if relation == "incompatible" and not (
                self.context.base
                and all(
                    analyzer.analyze(self.context.base, lid)["relationship"]
                    in {"same", "equivalent", "roll_up"}
                    for lid in (definition["nativePlanningLevelId"], target_level)
                )
            ):
                return False
            from ..engine.relationships import Coordinate

            def available_expression(node):
                if node["type"] == "measureRef":
                    dependency = self.context.objects.get(node["measureId"])
                    return bool(
                        dependency
                        and dependency["kind"] == "measure"
                        and self.available(
                            dependency, target_level, seen | {measure["objectId"]}
                        )
                    )
                if node["type"] == "attributeRef":
                    coordinate = (
                        Coordinate(
                            frozenset(
                                {("dimension", node["dimensionId"], node["columnId"])}
                            )
                        )
                        if node.get("dimensionId")
                        else analyzer.outputs(node["planningLevelId"]).get(
                            node["columnId"]
                        )
                    )
                    return bool(
                        coordinate and analyzer.resolve(target_level, coordinate)
                    )
                if node["type"] == "grainRef":
                    coordinate = analyzer.outputs(
                        definition["nativePlanningLevelId"]
                    ).get(node["grainColumnId"])
                    return bool(
                        coordinate and analyzer.resolve(target_level, coordinate)
                    )
                if node["type"] == "binary":
                    return available_expression(node["left"]) and available_expression(
                        node["right"]
                    )
                if node["type"] == "call":
                    return all(available_expression(arg) for arg in node["arguments"])
                return True

            return available_expression(definition["formula"]["expression"])
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
                ] not in (
                    {v["sourceColumnId"] for v in tx["resolution"]["values"]}
                    | set(tx["resolution"].get("passthroughColumnIds", []))
                ):
                    self.context.error(
                        obj,
                        "unresolved_measure_column",
                        "Choose a configured numeric value or retained typed column from this transaction.",
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
                        from ..domain.typed_values import result_type

                        if not result_type(col["physicalType"]):
                            self.context.error(
                                obj,
                                "unsupported_measure_type",
                                "Choose a Number, String, Date or Boolean source column.",
                            )
                        if (
                            definition["resultType"]["type"]
                            in {"number", "decimal", "integer"}
                            and col.get("unit")
                            and col["unit"] != obj["business"]["unit"]
                        ):
                            self.context.error(
                                obj,
                                "unit_mismatch",
                                "Measure unit must match the declared source column unit.",
                            )
        else:
            self.context.formulas_rules.validate_counting(obj)
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

        from ..domain.typed_values import NUMERIC, methods
        from ..domain.formula_types import infer, attribute_physical_type
        from ..core.errors import MetadataError

        kind = definition["resultType"]["type"]
        policy = definition.get("aggregation")
        if kind not in NUMERIC:
            if definition.get("editable") or definition.get("allocation") is not None:
                self.context.error(
                    obj,
                    "typed_measure_distribution",
                    "String, Date and Boolean measures are read-only and have no dis-aggregation policy.",
                )
            if (
                definition.get("missingTransactionValue") == "zero"
                or policy
                and policy.get("allNullResult") == "zero"
            ):
                self.context.error(
                    obj,
                    "typed_measure_null",
                    "Nonnumeric measures preserve missing/all-null values as NULL.",
                )
        if policy:
            for axis in ("nonTime", "time"):
                if policy[axis] not in methods(kind, axis == "time"):
                    self.context.error(
                        obj,
                        "typed_aggregation",
                        f"{policy[axis]} is not a supported {axis} aggregation for {kind}.",
                    )
        if definition["measureType"] == "calculated":
            try:
                inferred = infer(
                    definition["formula"]["expression"],
                    lambda mid: self.context.objects[mid]["definition"]["resultType"][
                        "type"
                    ]
                    if mid in self.context.objects
                    and self.context.objects[mid]["kind"] == "measure"
                    else "number",
                    lambda node: attribute_physical_type(self.context, obj, node),
                )
                if inferred != kind and not (inferred in NUMERIC and kind in NUMERIC):
                    self.context.error(
                        obj,
                        "formula_result_type",
                        "Formula result type does not match its declared type.",
                    )
            except MetadataError as exc:
                self.context.error(obj, exc.code, exc.message)
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
                or basis["definition"]["resultType"]["type"]
                not in {"number", "integer", "decimal"}
            ):
                self.context.error(
                    obj,
                    "invalid_allocation_basis",
                    "Dis-aggregation basis must be a stored measure native on Base.",
                )
