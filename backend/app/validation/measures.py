"""Focused semantic rules; diagnostics and graph state are owned by the shared context."""

from ..core.errors import MetadataError
from .conversions import validate_conversion
from ..domain.formula_types import attribute_physical_type, infer
from ..domain.typed_values import NUMERIC, methods, result_type
from ..engine.transformations.dates.calendar import CalendarResolutionPlanner
from ..engine.graph.compatibility import MeasureCompatibilityAnalyzer
from ..engine.graph.relationships import LevelRelationshipAnalyzer


class MeasureRules:
    def __init__(self, context):
        self.context = context

    def rolls_up(self, source_level: str, target_level: str) -> bool:
        return LevelRelationshipAnalyzer(list(self.context.objects.values())).analyze(
            source_level, target_level
        )["relationship"] in ("same", "equivalent", "roll_up")

    def availability(self, measure, target_level):
        if not hasattr(self, "_availability"):
            self._availability = MeasureCompatibilityAnalyzer(
                list(self.context.objects.values()), getattr(self.context, "base", None)
            )
        return self._availability.outcome(measure["objectId"], target_level)

    def available(self, measure: dict, target_level: str, seen=frozenset()) -> bool:
        return self.availability(measure, target_level).available

    def validate_measure(self, obj: dict):
        definition = obj["definition"]
        level = self.context.grain_reference(
            obj, definition["nativeGrainId"], "definition.outputGrain"
        )
        if definition["measureType"] == "calculated" and definition.get("outputGrain"):
            reference = definition["outputGrain"]
            self.context.reference(
                obj, reference["objectId"], reference["kind"], "definition.outputGrain"
            )
        if definition["measureType"] == "referenced":
            validate_conversion(self.context, obj)
            transaction = self.context.reference(
                obj,
                definition["transactionId"],
                "transaction",
                "definition.transactionId",
            )
            if transaction:
                tx = transaction["definition"]
                if tx["nativeGrainId"] != definition["nativeGrainId"]:
                    self.context.error(
                        obj,
                        "measure_grain_mismatch",
                        "Referenced measure and transaction must have the same native dimension.",
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
                        if not result_type(col["physicalType"]):
                            self.context.error(
                                obj,
                                "unsupported_measure_type",
                                "Choose a Number, String, Date or Boolean source column.",
                            )
                        if (
                            definition["resultType"]["type"]
                            in {"number", "decimal", "integer"}
                            and not definition.get("conversion")
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
                basis["definition"]["nativeGrainId"] != self.context.base
                or basis["definition"].get("calculationType") == "AggregateFirst"
                or basis["definition"]["resultType"]["type"]
                not in {"number", "integer", "decimal"}
            ):
                self.context.error(
                    obj,
                    "invalid_allocation_basis",
                    "Dis-aggregation basis must be a stored measure native on Base.",
                )
