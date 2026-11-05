"""Metadata-only measure availability shared by validation, views and context graphs."""

from dataclasses import dataclass
from collections import OrderedDict
from .relationships import Coordinate, LevelRelationshipAnalyzer
from ..formula_language.expressions import contains_counts

ROLLUPS = {"same", "equivalent", "roll_up"}


@dataclass(frozen=True)
class MeasureAvailability:
    available: bool
    operation: str
    reason: str


class MeasureCompatibilityAnalyzer:
    def __init__(self, objects, base_id, relationships=None):
        self.objects = {obj["objectId"]: obj for obj in objects}
        self.base_id = base_id
        self.relationships = relationships or LevelRelationshipAnalyzer(objects)
        self.memo = OrderedDict()

    def via_base(self, native, target):
        return bool(
            self.base_id
            and all(
                self.relationships.analyze(self.base_id, level)["relationship"]
                in ROLLUPS
                for level in (native, target)
            )
        )

    def outcome(self, measure_id, target, seen=frozenset()):
        key = (measure_id, target)
        if key in self.memo:
            self.memo.move_to_end(key)
            return self.memo[key]
        measure = self.objects.get(measure_id)
        if not measure or measure["kind"] != "measure":
            return MeasureAvailability(
                False, "unavailable", "The selected measure no longer exists."
            )
        if key in seen or len(seen) > 128:
            return MeasureAvailability(
                False, "unavailable", "The formula has a dependency cycle."
            )
        definition = measure["definition"]
        native = definition["nativePlanningLevelId"]
        relation = self.relationships.analyze(native, target)["relationship"]
        target_name = self.objects.get(target, {}).get("name", "the requested grain")
        name = measure["name"]
        failure = MeasureAvailability(
            False,
            "unavailable",
            f'"{name}" has no proven planning-level path to "{target_name}". Map shared dimensions or use a compatible planning level.',
        )
        expression = (definition.get("formula") or {}).get("expression")
        if contains_counts(expression) and relation not in ROLLUPS:
            result = MeasureAvailability(
                False,
                "unavailable",
                f'"{name}" counts intersections at its Output Planning Level and cannot be distributed to finer or unrelated grains.',
            )
        elif definition.get("calculationType") == "AggregateFirst":
            if relation == "incompatible" and not self.via_base(native, target):
                result = failure
            else:
                problem = self.expression_problem(
                    expression, native, target, seen | {key}
                )
                result = (
                    MeasureAvailability(False, "unavailable", problem)
                    if problem
                    else MeasureAvailability(
                        True,
                        "recalculate",
                        "Recalculate the formula from inputs aligned to this grain.",
                    )
                )
        elif definition["resultType"]["type"] in {"string", "date", "boolean"}:
            result = (
                MeasureAvailability(
                    True,
                    "native" if native == target else "aggregate",
                    "Use native values or their configured typed aggregation.",
                )
                if relation in ROLLUPS
                else MeasureAvailability(
                    False,
                    "unavailable",
                    f'"{name}" has a {definition["resultType"]["type"]} result that cannot be distributed to finer or unrelated grains.',
                )
            )
        elif relation in ROLLUPS:
            result = MeasureAvailability(
                True,
                "native" if native == target else "aggregate",
                "Use native values or aggregate with the measure’s saved policies.",
            )
        elif (
            target == self.base_id
            and relation == "roll_down"
            or self.via_base(native, target)
        ):
            result = MeasureAvailability(
                True,
                "via_base",
                "Use the proven Base path with the measure’s materialisation and aggregation policies.",
            )
        else:
            result = failure
        self.memo[key] = result
        if len(self.memo) > 8192:
            self.memo.popitem(last=False)
        return result

    def expression_problem(self, node, native, target, seen):
        kind = node["type"]
        if kind == "measureRef":
            result = self.outcome(node["measureId"], target, seen)
            return None if result.available else result.reason
        if kind == "count":
            if (
                self.relationships.analyze(native, target)["relationship"]
                not in ROLLUPS
            ):
                return "Count results cannot be distributed below their Output Planning Level."
            return next(
                (
                    problem
                    for arg in node["arguments"]
                    if (problem := self.expression_problem(arg, native, native, seen))
                ),
                None,
            )
        if kind in {"attributeRef", "grainRef"}:
            if kind == "grainRef":
                coordinate = self.relationships.outputs(native).get(
                    node["grainColumnId"]
                )
            elif node.get("dimensionId"):
                coordinate = Coordinate(
                    frozenset({("dimension", node["dimensionId"], node["columnId"])})
                )
            else:
                coordinate = self.relationships.outputs(node["planningLevelId"]).get(
                    node["columnId"]
                )
            return (
                None
                if coordinate and self.relationships.resolve(target, coordinate)
                else "A formula attribute is not uniquely determined at this grain."
            )
        if kind == "binary":
            return self.expression_problem(
                node["left"], native, target, seen
            ) or self.expression_problem(node["right"], native, target, seen)
        if kind == "call":
            return next(
                (
                    problem
                    for arg in node["arguments"]
                    if (problem := self.expression_problem(arg, native, target, seen))
                ),
                None,
            )
        return None
