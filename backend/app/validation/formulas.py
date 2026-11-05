"""Focused semantic rules; diagnostics and graph state are owned by the shared context."""


class FormulaRules:
    def __init__(self, context):
        self.context = context

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
                    target, owner["definition"]["nativePlanningLevelId"]
                ):
                    self.context.error(
                        owner,
                        "measure_unavailable",
                        f"{target['objectId']} is unavailable at this formula's output grain.",
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
                dtype = self.context.grain_type(level, node["grainColumnId"])
                if dtype and not self.context.numeric_type(dtype):
                    self.context.error(
                        owner,
                        "non_numeric_formula_input",
                        "Numeric formulas require numeric grain attributes.",
                    )
        elif kind == "binary":
            self.context.validate_expression(owner, node["left"], level, depth + 1)
            self.context.validate_expression(owner, node["right"], level, depth + 1)
        elif kind == "call":
            n = len(node["arguments"])
            valid = (
                n == 1
                if node["function"] == "abs"
                else (n in {1, 2} if node["function"] == "round" else n >= 2)
            )
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
