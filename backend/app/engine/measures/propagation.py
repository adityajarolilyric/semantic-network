from ...core.errors import MetadataError
from ...formula_language.expressions import contains_counts
from .contracts import (
    MeasureNode,
)


class MeasurePropagationCompiler:
    """Resolve native/Base paths and preserve each calculation grain boundary."""

    def __init__(self, compiler):
        self.compiler = compiler

    def compile_seed_measure(self, measure_id):
        self.compiler.dependencies.ordered(measure_id)
        key = (measure_id, "__native_seed")
        if key in self.compiler.cache:
            return self.compiler.cache[key]
        if key in self.compiler.visiting:
            raise MetadataError(
                "measure_dependency_cycle",
                "Native measure/formula/basis dependencies contain a cycle.",
            )
        self.compiler.visiting.add(key)
        try:
            measure = self.compiler.measures[measure_id]
            result = (
                self.compiler.compile_native_measure(measure)
                if measure["definition"]["measureType"] == "referenced"
                else self.compiler.compile_calculated_measure(
                    measure, measure["definition"]["nativeGrainId"]
                )
            )
            self.compiler.cache[key] = result
            return result
        finally:
            self.compiler.visiting.remove(key)

    def compile_measure_at_level(self, measure_id, target):
        self.compiler.dependencies.ordered(measure_id)
        key = (measure_id, target)
        if key in self.compiler.cache:
            return self.compiler.cache[key]
        if key in self.compiler.visiting:
            raise MetadataError(
                "measure_dependency_cycle",
                "Measure propagation/basis/formula dependencies contain a cycle.",
            )
        self.compiler.visiting.add(key)
        try:
            measure = self.compiler.measures[measure_id]
            d = measure["definition"]
            native = d["nativeGrainId"]
            relation = self.compiler.relationships.analyze(native, target)[
                "relationship"
            ]
            # AggregateFirst recalculates operands here; CalculateFirst propagates its native result.
            if d.get("calculationType") == "AggregateFirst":
                if contains_counts(d["formula"]["expression"]):
                    counting = d["nativeGrainId"]
                    if not counting or self.compiler.relationships.analyze(
                        counting, target
                    )["relationship"] not in {"same", "equivalent", "roll_up"}:
                        raise MetadataError(
                            "measure_path_unavailable",
                            "Counts can only recalculate at the measure’s Output Dimension or coarser.",
                        )
                    relation = "same"
                via_base = all(
                    (
                        self.compiler.relationships.analyze(self.compiler.base_id, lid)[
                            "relationship"
                        ]
                        in ("same", "equivalent", "roll_up")
                        for lid in (native, target)
                    )
                )
                if relation == "incompatible" and (not via_base):
                    raise MetadataError(
                        "measure_path_unavailable",
                        "No proven path to this calculation grain.",
                    )
                result = self.compiler.compile_calculated_measure(measure, target)
                operation = "recalculate"
            elif d.get("isConversionFactor") and relation == "roll_down":
                result = self.compiler.disaggregate(
                    self.compiler.compile_seed_measure(measure_id),
                    measure,
                    native,
                    target,
                )
                operation = "repeat_conversion_factor"
            elif d["resultType"]["type"] in {"string", "date", "boolean"}:
                if target == native:
                    result = self.compiler.compile_seed_measure(measure_id)
                    operation = "native_typed"
                elif relation in {"same", "equivalent", "roll_up"}:
                    result = self.compiler.rollup(
                        self.compiler.compile_seed_measure(measure_id),
                        measure,
                        native,
                        target,
                    )
                    operation = "aggregate_typed"
                else:
                    raise MetadataError(
                        "measure_path_unavailable",
                        "String, Date and Boolean results cannot be distributed to finer levels.",
                    )
            elif target == self.compiler.base_id:
                if native == target:
                    result = self.compiler.compile_seed_measure(measure_id)
                    operation = "native"
                elif relation == "roll_down":
                    result = self.compiler.disaggregate(
                        self.compiler.compile_seed_measure(measure_id),
                        measure,
                        native,
                        target,
                    )
                    operation = "disaggregate"
                elif relation in ("same", "equivalent", "roll_up"):
                    result = self.compiler.rollup(
                        self.compiler.compile_seed_measure(measure_id),
                        measure,
                        native,
                        target,
                    )
                    operation = "aggregate_to_base"
                else:
                    raise MetadataError(
                        "measure_path_unavailable",
                        "Required key lineage or dimension binding to Base is missing.",
                    )
            elif (
                self.compiler.relationships.analyze(self.compiler.base_id, target)[
                    "relationship"
                ]
                in ("roll_up", "equivalent")
                and self.compiler.relationships.analyze(native, self.compiler.base_id)[
                    "relationship"
                ]
                != "incompatible"
            ):
                result = self.compiler.rollup(
                    self.compiler.compile_measure_at_level(
                        measure_id, self.compiler.base_id
                    ),
                    measure,
                    self.compiler.base_id,
                    target,
                )
                coverage = self.compiler.coverage_nodes.get(result)
                if coverage:
                    fallback = None
                    if relation in ("same", "equivalent", "roll_up"):
                        fallback = self.compiler.compile_seed_measure(measure_id)
                        if native != target:
                            fallback = self.compiler.rollup(
                                fallback, measure, native, target
                            )
                    result = self.compiler.with_coverage(
                        result, coverage, target, fallback
                    )
                operation = "via_base"
            elif target == native:
                result = self.compiler.compile_seed_measure(measure_id)
                operation = "native_without_base_path"
            elif relation in ("same", "equivalent", "roll_up"):
                result = self.compiler.rollup(
                    self.compiler.compile_seed_measure(measure_id),
                    measure,
                    native,
                    target,
                )
                operation = "aggregate_without_base_path"
            else:
                raise MetadataError(
                    "measure_path_unavailable",
                    "Required key lineage or dimension binding is missing.",
                )
            result = self.compiler.quantize_node(result, measure, target)
            self.compiler.cache[key] = result
            self.compiler.nodes.append(MeasureNode(measure_id, target, operation))
            return result
        finally:
            self.compiler.visiting.remove(key)
