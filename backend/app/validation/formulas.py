"""Focused semantic rules; diagnostics and graph state are owned by the shared context."""


class FormulaRules:
    def __init__(self, context):
        self.context = context

    def validate_counting(self, owner):
        from ..formula_language.expressions import contains_counts

        definition = owner["definition"]
        if (
            contains_counts(definition["formula"]["expression"])
            and definition.get("calculationType") != "AggregateFirst"
        ):
            self.context.error(
                owner,
                "counting_calculation",
                "Count formulas require AggregateFirst and recalculate at Output Planning Level or coarser.",
            )

    def validate_expression(
        self, owner: dict, node: dict, level: dict | None, depth: int
    ):
        if depth > 32:
            self.context.error(
                owner, "formula_depth", "Formula nesting exceeds 32 levels."
            )
            return
        kind = node["type"]
        if kind == "measureRef":
            target = self.context.reference(
                owner, node["measureId"], "measure", "definition.formula.expression"
            )
            if target:
                if not self.context.available(
                    target,
                    level["objectId"]
                    if level
                    else owner["definition"]["nativePlanningLevelId"],
                ):
                    level_name = level.get("name") if level else None
                    at_level = (
                        f'Output Planning Level "{level_name}"'
                        if level_name
                        else "this formula's Output Planning Level"
                    )
                    self.context.error(
                        owner,
                        "measure_unavailable",
                        f'"{target["name"]}" is unavailable at {at_level}.',
                    )
                if (
                    owner["definition"]["calculationType"] == "CalculateFirst"
                    and target["definition"].get("calculationType") == "AggregateFirst"
                ):
                    self.context.error(
                        owner,
                        "runtime_dependency",
                        "CalculateFirst cannot depend on an AggregateFirst result.",
                    )
        elif kind == "grainRef":
            if level:
                self.context.require_columns(
                    owner,
                    [node["grainColumnId"]],
                    {
                        c["grainColumnId"]
                        for c in level["definition"]["grain"]["columns"]
                    },
                    "definition.formula.expression",
                )
        elif kind == "attributeRef":
            from ..engine.relationships import Coordinate, LevelRelationshipAnalyzer

            owner_id = node.get("dimensionId") or node.get("planningLevelId")
            owner_kind = "dimension" if node.get("dimensionId") else "planningLevel"
            self.context.reference(
                owner, owner_id, owner_kind, "definition.formula.expression"
            )
            analyzer = LevelRelationshipAnalyzer(list(self.context.objects.values()))
            coordinate = (
                Coordinate(frozenset({("dimension", owner_id, node["columnId"])}))
                if node.get("dimensionId")
                else analyzer.outputs(owner_id).get(node["columnId"])
            )
            if level and (
                not coordinate or not analyzer.resolve(level["objectId"], coordinate)
            ):
                self.context.error(
                    owner,
                    "formula_attribute_unavailable",
                    "This attribute is not determined by the formula output planning level.",
                )
        elif kind == "binary":
            self.context.validate_expression(owner, node["left"], level, depth + 1)
            self.context.validate_expression(owner, node["right"], level, depth + 1)
        elif kind == "count":
            from ..formula_language.expressions import contains_counts

            counting = self.context.objects.get(
                owner["definition"]["nativePlanningLevelId"]
            )
            for child in node["arguments"]:
                if contains_counts(child):
                    self.context.error(
                        owner, "nested_count", "Nested counts are not supported."
                    )
                self.context.validate_expression(owner, child, counting, depth + 1)
        elif kind == "call":
            n = len(node["arguments"])
            from ..domain.formula_types import FUNCTIONS

            low, high = FUNCTIONS[node["function"]]
            valid = low <= n <= high
            if not valid:
                self.context.error(
                    owner,
                    "function_arity",
                    f"Invalid argument count for {node['function']}.",
                )
            for child in node["arguments"]:
                self.context.validate_expression(owner, child, level, depth + 1)

    def expression_unit(self, owner, node, depth=0):
        if depth > 32:
            return None
        if node["type"] == "measureRef":
            target = self.context.objects.get(node["measureId"])
            return target.get("business", {}).get("unit") if target else None
        if node["type"] == "binary":
            if node["operator"] in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                return None
            left = self.context.expression_unit(owner, node["left"], depth + 1)
            right = self.context.expression_unit(owner, node["right"], depth + 1)
            if node["operator"] in {"add", "subtract"}:
                if left and right and left != right:
                    self.context.error(
                        owner,
                        "incompatible_units",
                        "Addition and subtraction require matching measure units.",
                    )
                return left or right
            if node["operator"] == "multiply":
                return f"{left}*{right}" if left and right else left or right
            if left and right:
                return "1" if left == right else f"{left}/{right}"
            return left if left else (f"1/{right}" if right else None)
        if node["type"] == "call":
            if node["function"] in {
                "and",
                "or",
                "not",
                "is_null",
                "concat",
                "upper",
                "lower",
                "trim",
                "date",
                "add_days",
                "days_between",
            }:
                return None
            if node["function"] == "if":
                left = self.expression_unit(owner, node["arguments"][1], depth + 1)
                right = self.expression_unit(owner, node["arguments"][2], depth + 1)
                if left and right and left != right:
                    self.context.error(
                        owner,
                        "incompatible_units",
                        "IF result branches require matching known units.",
                    )
                return left or right
            units = [
                self.context.expression_unit(owner, arg, depth + 1)
                for arg in node["arguments"]
            ]
            if (
                node["function"] in {"coalesce", "min", "max"}
                and len({unit for unit in units if unit}) > 1
            ):
                self.context.error(
                    owner,
                    "incompatible_units",
                    "Function arguments require matching measure units.",
                )
            return next((unit for unit in units if unit), None)
        return None
