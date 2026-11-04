from collections import defaultdict
import re

from ..engine.contracts import digest
from ..schemas.common import Diagnostic, ValidationReport


class SemanticGraphValidator:
    """Validate a revision snapshot without querying or mutating the warehouse."""

    def __init__(self, objects=()):
        self.objects = {obj["objectId"]: obj for obj in objects}
        self.errors = []

    def validate(
        self,
        objects: list[dict],
        base_level_id: str | None,
        *,
        publishing: bool = False,
    ) -> ValidationReport:
        self.objects = {obj["objectId"]: obj for obj in objects}
        self.errors: list[Diagnostic] = []
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.base = base_level_id
        if publishing:
            if not base_level_id:
                self.error(
                    None,
                    "base_required",
                    "A published revision must select a base planning level.",
                )
            else:
                self.reference(
                    None, base_level_id, "planningLevel", "basePlanningLevelId"
                )
        time_dimensions = [
            o for o in objects if o["kind"] == "dimension" and o["isTimeDim"]
        ]
        if len(time_dimensions) > 1:
            self.error(
                None,
                "multiple_time_dimensions",
                "Only one dimension may have isTimeDim=true.",
            )
        for obj in objects:
            getattr(self, f"validate_{obj['kind']}")(obj)
        if base_level_id in self.objects:
            from ..engine.relationships import LevelRelationshipAnalyzer

            analyzer = LevelRelationshipAnalyzer(objects)
            for obj in objects:
                if (
                    obj["kind"] == "planningLevel"
                    and obj["objectId"] != base_level_id
                    and analyzer.analyze(obj["objectId"], base_level_id)["relationship"]
                    == "roll_up"
                ):
                    self.error(
                        None,
                        "base_not_finest",
                        "The base cannot be a coarser projection of another planning level.",
                    )
        self.validate_cycles()
        return ValidationReport(valid=not self.errors, diagnostics=self.errors)

    def error(self, obj: dict | None, code: str, message: str, path: str | None = None):
        self.errors.append(
            Diagnostic(
                code=code,
                message=message,
                objectId=obj["objectId"] if obj else None,
                path=path,
            )
        )

    def reference(
        self, owner: dict | None, object_id: str, kind: str, path: str
    ) -> dict | None:
        target = self.objects.get(object_id)
        if target is None or target["kind"] != kind:
            self.error(
                owner,
                "invalid_reference",
                f"{object_id} must reference an existing {kind} in this revision.",
                path,
            )
            return None
        if owner:
            self.edges[owner["objectId"]].add(object_id)
        return target

    def source(self, owner: dict, source_id: str) -> dict | None:
        source = self.reference(owner, source_id, "source", "definition.sourceId")
        if source and not source["definition"]["inScope"]:
            self.error(
                owner, "source_out_of_scope", f"Source {source_id} is not in scope."
            )
        return source

    def columns(self, source: dict) -> dict[str, dict]:
        return {c["columnId"]: c for c in source["definition"]["columns"]}

    def object_columns(self, obj, source):
        from ..engine.object_inputs import input_columns
        from ..core.errors import MetadataError

        try:
            return {
                c["columnId"]: c
                for c in input_columns(obj, list(self.columns(source).values()))
            }
        except MetadataError as error:
            self.error(obj, error.code, error.message, "definition.pivot")
            return {}

    def require_columns(
        self, owner: dict, requested: list[str], available: set | dict, path: str
    ):
        missing = set(requested) - set(available)
        if missing:
            self.error(
                owner,
                "unknown_column",
                f"Unknown columns: {', '.join(sorted(missing))}.",
                path,
            )

    def validate_filters(self, owner: dict, filters: dict | None, columns: set | dict):
        from ..engine.filters import FilterCompiler
        from ..core.errors import MetadataError

        if filters:
            try:
                FilterCompiler().compile(filters, columns)
            except MetadataError as error:
                self.error(owner, error.code, error.message, "definition.filters")

    def validate_source(self, obj: dict):
        # Expected source schemas are declarations. Data-level checks belong in evidence.
        return None

    @staticmethod
    def normalized_type(dtype: str) -> str:
        dtype = re.sub(r"\s+", "", dtype).lower()
        while (
            dtype.startswith("nullable(") or dtype.startswith("lowcardinality(")
        ) and dtype.endswith(")"):
            dtype = dtype[dtype.index("(") + 1 : -1]
        return dtype

    @classmethod
    def numeric_type(cls, dtype: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:u?int(?:8|16|32|64|128|256)|float(?:32|64)|decimal(?:32|64|128|256)?\(\d+(?:,\d+)?\))",
                cls.normalized_type(dtype),
            )
        )

    def population_columns(self, owner, source):
        from ..engine.population_inputs import population_columns
        from ..core.errors import MetadataError

        population = owner["definition"]["population"]
        calendar = (population.get("inputTransform") or {}).get("calendarResolution")
        if calendar:
            self.reference(
                owner,
                calendar["dimensionId"],
                "dimension",
                "definition.population.inputTransform.calendarResolution",
            )
        try:
            return {
                c["columnId"]: c
                for c in population_columns(
                    population,
                    list(self.objects.values()),
                    list(self.columns(source).values()),
                )
            }
        except MetadataError as error:
            self.error(
                owner, error.code, error.message, "definition.population.inputTransform"
            )
            return {}

    def grain_type(self, level: dict, column_id: str, seen=None) -> str | None:
        seen = set() if seen is None else seen
        marker = (level["objectId"], column_id)
        if marker in seen or len(seen) > 128:
            return None
        seen.add(marker)
        definition = level["definition"]
        column = next(
            (
                c
                for c in [
                    *definition["grain"]["columns"],
                    *definition.get("populationAttributes", []),
                ]
                if c.get("grainColumnId", c.get("columnId")) == column_id
            ),
            None,
        )
        if not column:
            for binding in definition["dimensionBindings"]:
                dimension = self.objects.get(binding["dimensionId"])
                for cid in binding["selectedAttributeColumnIds"]:
                    if (
                        column_id
                        == "da_"
                        + digest([level["objectId"], binding["dimensionId"], cid])[:28]
                        and dimension
                    ):
                        return self.dimension_type(dimension, cid)
            return None
        population, ref = definition["population"], column["populationRef"]
        if population["operation"] == "distinct_projection":
            parent = self.objects.get(population["sourcePlanningLevelId"])
            return (
                self.grain_type(
                    parent,
                    ref.get("sourceGrainColumnId") or ref.get("sourceOutputColumnId"),
                    seen,
                )
                if parent and parent["kind"] == "planningLevel"
                else None
            )
        if population["operation"] == "cartesian_product":
            factor = next(
                f for f in population["factors"] if f["factorId"] == ref["factorId"]
            )
            source_id = factor["sourceId"]
        else:
            source_id = population["sourceId"]
        source = self.objects.get(source_id)
        if source and source["kind"] == "source":
            return (
                self.population_columns(level, source)
                .get(ref["columnId"], {})
                .get("physicalType")
            )
        return None

    def dimension_type(self, dimension: dict, column_id: str) -> str | None:
        definition = dimension["definition"]
        column = next(
            (c for c in definition["columns"] if c["columnId"] == column_id), None
        )
        source = self.objects.get(definition["sourceId"])
        if column and source and source["kind"] == "source":
            return (
                self.object_columns(dimension, source)
                .get(column["sourceColumnId"], {})
                .get("physicalType")
            )
        return None

    def compatible(self, owner, left, right, path):
        if left and right and self.normalized_type(left) != self.normalized_type(right):
            self.error(
                owner,
                "key_type_mismatch",
                f"Key types {left} and {right} require an explicit conversion.",
                path,
            )

    def validate_dimension(self, obj: dict):
        definition = obj["definition"]
        source = self.source(obj, definition["sourceId"])
        if source:
            self.require_columns(
                obj,
                [c["sourceColumnId"] for c in definition["columns"]],
                self.object_columns(obj, source),
                "definition.columns",
            )
            mapping = {
                c["columnId"]: c["sourceColumnId"] for c in definition["columns"]
            }
            if any(
                self.object_columns(obj, source)
                .get(mapping[k], {})
                .get("nullable", False)
                for k in definition["keyColumnIds"]
            ):
                self.error(
                    obj,
                    "nullable_dimension_key",
                    "Dimension key columns cannot be nullable.",
                )
        self.validate_filters(
            obj,
            definition.get("filters"),
            {
                c["columnId"]: self.dimension_type(obj, c["columnId"])
                for c in definition["columns"]
            },
        )

    def validate_planningLevel(self, obj: dict):
        definition = obj["definition"]
        population = definition["population"]
        grain = definition["grain"]
        columns = {c["grainColumnId"]: c for c in grain["columns"]}
        attributes = definition.get("populationAttributes", [])
        all_columns = {**columns, **{a["columnId"]: a for a in attributes}}
        if population["operation"] == "distinct_projection":
            parent = self.reference(
                obj,
                population["sourcePlanningLevelId"],
                "planningLevel",
                "definition.population",
            )
            if parent:
                parent_grain = parent["definition"]["grain"]
                available = {c["grainColumnId"] for c in parent_grain["columns"]} | {
                    a["columnId"]
                    for a in parent["definition"].get("populationAttributes", [])
                }
                for b in parent["definition"]["dimensionBindings"]:
                    available |= {
                        "da_" + digest([parent["objectId"], b["dimensionId"], cid])[:28]
                        for cid in b["selectedAttributeColumnIds"]
                    }
                self.require_columns(
                    obj,
                    [
                        a["populationRef"].get("sourceOutputColumnId")
                        or a["populationRef"].get("sourceGrainColumnId")
                        for a in attributes
                    ],
                    available,
                    "definition.populationAttributes",
                )
                self.require_columns(
                    obj,
                    [
                        c["populationRef"].get("sourceGrainColumnId")
                        or c["populationRef"].get("sourceOutputColumnId")
                        for c in grain["columns"]
                    ],
                    available,
                    "definition.grain",
                )
        else:
            factors = population.get("factors") or [
                {"sourceId": population.get("sourceId"), "selectedColumnIds": []}
            ]
            for factor in factors:
                source = self.source(obj, factor["sourceId"])
                if source:
                    selected = factor["selectedColumnIds"]
                    if population["operation"] == "source":
                        selected = [
                            c["populationRef"]["columnId"] for c in all_columns.values()
                        ]
                    self.require_columns(
                        obj,
                        selected,
                        self.population_columns(obj, source),
                        "definition.population",
                    )
        for binding in definition["dimensionBindings"]:
            dimension = self.reference(
                obj, binding["dimensionId"], "dimension", "definition.dimensionBindings"
            )
            self.require_columns(
                obj,
                [
                    m.get("grainColumnId") or m.get("populationColumnId")
                    for m in binding["keyMappings"]
                ],
                all_columns,
                "definition.dimensionBindings",
            )
            if dimension:
                dim_def = dimension["definition"]
                self.require_columns(
                    obj,
                    [m["dimensionColumnId"] for m in binding["keyMappings"]],
                    {c["columnId"] for c in dim_def["columns"]},
                    "definition.dimensionBindings.keyMappings",
                )
                self.require_columns(
                    obj,
                    binding["selectedAttributeColumnIds"],
                    set(dim_def["attributeColumnIds"]),
                    "definition.dimensionBindings",
                )
                for mapping in binding["keyMappings"]:
                    self.compatible(
                        obj,
                        self.grain_type(
                            obj,
                            mapping.get("grainColumnId")
                            or mapping.get("populationColumnId"),
                        ),
                        self.dimension_type(dimension, mapping["dimensionColumnId"]),
                        "definition.dimensionBindings",
                    )
        filter_columns = set(all_columns)
        for binding in definition["dimensionBindings"]:
            filter_columns |= {
                "da_" + digest([obj["objectId"], binding["dimensionId"], cid])[:28]
                for cid in binding["selectedAttributeColumnIds"]
            }
        if definition.get("filters"):
            self.validate_filters(
                obj,
                definition["filters"],
                {cid: self.grain_type(obj, cid) for cid in filter_columns},
            )

    def validate_transaction(self, obj: dict):
        definition = obj["definition"]
        source = self.source(obj, definition["sourceId"])
        level = self.reference(
            obj,
            definition["planningLevelId"],
            "planningLevel",
            "definition.planningLevelId",
        )
        if (definition.get("calendarResolution") or definition.get("pivot")) and source:
            from ..engine.calendar_resolution import CalendarResolutionPlanner
            from ..core.errors import MetadataError

            try:
                calendar = CalendarResolutionPlanner(
                    list(self.objects.values()),
                    obj,
                    list(self.columns(source).values()),
                )
                source = {
                    **source,
                    "definition": {
                        **source["definition"],
                        "columns": calendar.schema(),
                    },
                }
            except MetadataError as error:
                self.error(
                    obj, error.code, error.message, "definition.calendarResolution"
                )
                return
        if definition.get("mappingMode") == "resolve_multi_grain":
            if source and level:
                from ..engine.transaction_resolution import TransactionResolutionPlanner
                from ..core.errors import MetadataError

                try:
                    planner = TransactionResolutionPlanner(
                        list(self.objects.values()),
                        obj,
                        list(
                            self.columns(self.objects[definition["sourceId"]]).values()
                        ),
                    )
                    population = [
                        {"columnId": cid, "physicalType": self.grain_type(level, cid)}
                        for cid in level["definition"]["grain"]["keyIds"]
                    ]
                    self.validate_filters(
                        obj, definition.get("filters"), planner.schema(population)
                    )
                except MetadataError as error:
                    self.error(obj, error.code, error.message, "definition.resolution")
            return
        mappings = definition["keyMappings"]
        if source:
            self.validate_filters(obj, definition.get("filters"), self.columns(source))
            self.require_columns(
                obj,
                [m["sourceColumnId"] for m in mappings],
                self.columns(source),
                "definition.keyMappings",
            )
            if (
                definition["expectedRowsPerGrain"] == "zero_or_one"
                and source["definition"]["keyColumns"]
            ) and set(source["definition"]["keyColumns"]) != {
                m["sourceColumnId"] for m in mappings
            }:
                self.error(
                    obj,
                    "transaction_grain_mismatch",
                    "A zero_or_one transaction must map exactly its declared source key.",
                )
        if level:
            if set(level["definition"]["grain"]["keyIds"]) != {
                m["grainColumnId"] for m in mappings
            }:
                self.error(
                    obj,
                    "incomplete_transaction_key",
                    "Transactions must map every native grain key exactly once.",
                )
            if source:
                for mapping in mappings:
                    self.compatible(
                        obj,
                        self.columns(source)
                        .get(mapping["sourceColumnId"], {})
                        .get("physicalType"),
                        self.grain_type(level, mapping["grainColumnId"]),
                        "definition.keyMappings",
                    )

    def rolls_up(self, source_level: str, target_level: str) -> bool:
        from ..engine.relationships import LevelRelationshipAnalyzer

        return LevelRelationshipAnalyzer(list(self.objects.values())).analyze(
            source_level, target_level
        )["relationship"] in ("same", "equivalent", "roll_up")

    def available(self, measure: dict, target_level: str) -> bool:
        from ..engine.relationships import LevelRelationshipAnalyzer

        definition = measure["definition"]
        relation = LevelRelationshipAnalyzer(list(self.objects.values())).analyze(
            definition["nativePlanningLevelId"], target_level
        )["relationship"]
        if relation in ("same", "equivalent", "roll_up"):
            return True
        if target_level == self.base and relation == "roll_down":
            return True
        analyzer = LevelRelationshipAnalyzer(list(self.objects.values()))
        return bool(
            self.base
            and all(
                analyzer.analyze(self.base, lid)["relationship"]
                in ("same", "equivalent", "roll_up")
                for lid in (definition["nativePlanningLevelId"], target_level)
            )
        )

    def validate_measure(self, obj: dict):
        definition = obj["definition"]
        level = self.reference(
            obj,
            definition["nativePlanningLevelId"],
            "planningLevel",
            "definition.nativePlanningLevelId",
        )
        if definition["measureType"] == "referenced":
            transaction = self.reference(
                obj,
                definition["transactionId"],
                "transaction",
                "definition.transactionId",
            )
            if transaction:
                tx = transaction["definition"]
                if tx["planningLevelId"] != definition["nativePlanningLevelId"]:
                    self.error(
                        obj,
                        "measure_grain_mismatch",
                        "Referenced measure and transaction must have the same native planning level.",
                    )
                if tx.get("mappingMode") == "resolve_multi_grain" and definition[
                    "valueColumnId"
                ] not in {v["sourceColumnId"] for v in tx["resolution"]["values"]}:
                    self.error(
                        obj,
                        "unresolved_measure_column",
                        "Choose a value column configured for Split/Copy in this transaction.",
                        "definition.valueColumnId",
                    )
                source = self.objects.get(tx["sourceId"])
                if source and source["kind"] == "source":
                    columns = self.columns(source)
                    if tx.get("calendarResolution") or tx.get("pivot"):
                        from ..engine.calendar_resolution import (
                            CalendarResolutionPlanner,
                        )
                        from ..core.errors import MetadataError

                        try:
                            columns = {
                                c["columnId"]: c
                                for c in CalendarResolutionPlanner(
                                    list(self.objects.values()),
                                    transaction,
                                    list(columns.values()),
                                ).schema()
                            }
                        except MetadataError:
                            pass  # The transaction reports its calendar contract error.
                    self.require_columns(
                        obj,
                        [definition["valueColumnId"]],
                        columns,
                        "definition.valueColumnId",
                    )
                    col = columns.get(definition["valueColumnId"])
                    if col:
                        if not self.numeric_type(col["physicalType"]):
                            self.error(
                                obj,
                                "non_numeric_measure",
                                "This version supports numeric measure source columns only.",
                            )
                        if col.get("unit") and col["unit"] != obj["business"]["unit"]:
                            self.error(
                                obj,
                                "unit_mismatch",
                                "Measure unit must match the declared source column unit.",
                            )
        else:
            self.validate_expression(
                obj, definition["formula"]["expression"], level, depth=0
            )
            unit = self.expression_unit(obj, definition["formula"]["expression"])
            if unit is not None and unit != obj["business"]["unit"]:
                self.error(
                    obj,
                    "unit_mismatch",
                    f"Formula unit {unit} does not match result unit {obj['business']['unit']}.",
                )

        self.validate_allocation(obj)

    def validate_allocation(self, obj):
        definition = obj["definition"]
        allocation = definition.get("allocation") or {"mode": "none"}
        if allocation["mode"] == "none":
            return
        # A policy can be configured before a path to Base exists. It remains
        # dormant until a supported propagation/edit path actually uses it.
        if allocation["mode"] == "weighted":
            basis = self.reference(
                obj,
                allocation["basisMeasureId"],
                "measure",
                "definition.allocation.basisMeasureId",
            )
            if basis and (
                basis["definition"]["nativePlanningLevelId"] != self.base
                or basis["definition"].get("calculationType") == "AggregateFirst"
            ):
                self.error(
                    obj,
                    "invalid_allocation_basis",
                    "Dis-aggregation basis must be a stored measure native on Base.",
                )

    def validate_expression(
        self, owner: dict, node: dict, level: dict | None, depth: int
    ):
        if depth > 32:
            self.error(owner, "formula_depth", "Formula nesting exceeds 32 levels.")
            return
        kind = node["type"]
        if kind == "measureRef":
            target = self.reference(
                owner, node["measureId"], "measure", "definition.formula.expression"
            )
            if target:
                if not self.available(
                    target, owner["definition"]["nativePlanningLevelId"]
                ):
                    self.error(
                        owner,
                        "measure_unavailable",
                        f"{target['objectId']} is unavailable at this formula's output grain.",
                    )
                if (
                    owner["definition"]["calculationType"] == "CalculateFirst"
                    and target["definition"].get("calculationType") == "AggregateFirst"
                ):
                    self.error(
                        owner,
                        "runtime_dependency",
                        "CalculateFirst cannot depend on an AggregateFirst result.",
                    )
        elif kind == "grainRef":
            if level:
                self.require_columns(
                    owner,
                    [node["grainColumnId"]],
                    {
                        c["grainColumnId"]
                        for c in level["definition"]["grain"]["columns"]
                    },
                    "definition.formula.expression",
                )
                dtype = self.grain_type(level, node["grainColumnId"])
                if dtype and not self.numeric_type(dtype):
                    self.error(
                        owner,
                        "non_numeric_formula_input",
                        "Numeric formulas require numeric grain attributes.",
                    )
        elif kind == "binary":
            self.validate_expression(owner, node["left"], level, depth + 1)
            self.validate_expression(owner, node["right"], level, depth + 1)
        elif kind == "call":
            n = len(node["arguments"])
            valid = (
                n == 1
                if node["function"] == "abs"
                else (n in {1, 2} if node["function"] == "round" else n >= 2)
            )
            if not valid:
                self.error(
                    owner,
                    "function_arity",
                    f"Invalid argument count for {node['function']}.",
                )
            for child in node["arguments"]:
                self.validate_expression(owner, child, level, depth + 1)

    def expression_unit(self, owner, node, depth=0):
        if depth > 32:
            return None
        if node["type"] == "measureRef":
            target = self.objects.get(node["measureId"])
            return target.get("business", {}).get("unit") if target else None
        if node["type"] == "binary":
            left = self.expression_unit(owner, node["left"], depth + 1)
            right = self.expression_unit(owner, node["right"], depth + 1)
            if node["operator"] in {"add", "subtract"}:
                if left and right and left != right:
                    self.error(
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
                self.expression_unit(owner, arg, depth + 1) for arg in node["arguments"]
            ]
            if (
                node["function"] in {"coalesce", "min", "max"}
                and len({unit for unit in units if unit}) > 1
            ):
                self.error(
                    owner,
                    "incompatible_units",
                    "Function arguments require matching measure units.",
                )
            return next((unit for unit in units if unit), None)
        return None

    def validate_cycles(self):
        visited, active = set(), set()

        def visit(node: str, path: list[str]):
            if len(path) > 128:
                self.error(
                    self.objects.get(node),
                    "dependency_depth",
                    "Dependency nesting exceeds 128 levels.",
                )
                return
            if node in active:
                self.error(
                    self.objects.get(node),
                    "dependency_cycle",
                    "Dependency cycle: " + " -> ".join(path + [node]),
                )
                return
            if node in visited:
                return
            active.add(node)
            for child in sorted(self.edges[node]):
                visit(child, path + [node])
            active.remove(node)
            visited.add(node)

        for object_id in self.objects:
            visit(object_id, [])
