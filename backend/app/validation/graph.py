import re
from collections import defaultdict

from ..core.errors import MetadataError
from ..engine.filters import FilterCompiler
from ..engine.grain_contexts import resolve_grain_contexts
from ..engine.object_inputs import input_columns
from ..engine.relationships import LevelRelationshipAnalyzer
from ..schemas.common import Diagnostic, ValidationReport
from .dimension_members import DimensionMembersRules
from .dimensions import DimensionRules
from .formulas import FormulaRules
from .measures import MeasureRules
from .transactions import TransactionRules


class SemanticGraphValidator:
    """Validate a revision snapshot without querying or mutating the warehouse."""

    def __init__(self, objects=()):
        self.objects = {obj["objectId"]: obj for obj in resolve_grain_contexts(objects)}
        self.errors = []
        self.dimension_members_rules = DimensionMembersRules(self)
        self.dimensions_rules = DimensionRules(self)
        self.transactions_rules = TransactionRules(self)
        self.measures_rules = MeasureRules(self)
        self.formulas_rules = FormulaRules(self)

    def validate(
        self,
        objects: list[dict],
        base_level_id: str | None,
        *,
        publishing: bool = False,
    ) -> ValidationReport:
        objects = resolve_grain_contexts(objects)
        self.objects = {obj["objectId"]: obj for obj in objects}
        if hasattr(self.measures_rules, "_availability"):
            del self.measures_rules._availability
        self.errors: list[Diagnostic] = []
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.base = base_level_id
        if publishing:
            if not base_level_id:
                self.error(
                    None,
                    "base_required",
                    "A published revision must select a base dimension.",
                )
            else:
                self.reference(None, base_level_id, "dimension", "baseDimensionId")
        time_dimensions = [
            o for o in objects if o["kind"] == "dimensionMembers" and o["isTimeDim"]
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
            analyzer = LevelRelationshipAnalyzer(objects)
            for obj in objects:
                if (
                    obj["kind"] == "dimension"
                    and obj["objectId"] != base_level_id
                    and analyzer.analyze(obj["objectId"], base_level_id)["relationship"]
                    == "roll_up"
                ):
                    self.error(
                        None,
                        "base_not_finest",
                        "The base cannot be a coarser projection of another dimension.",
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

    def grain_reference(self, owner, grain_id, path):
        target = self.objects.get(grain_id)
        if not target or target["kind"] not in {"dimension", "grain"}:
            self.error(
                owner,
                "invalid_grain_reference",
                "Choose an existing Transaction or Dimension grain.",
                path,
            )
            return None
        if owner:
            self.edges[owner["objectId"]].add(grain_id)
        return target

    def validate_grain(self, obj):
        self.reference(None, obj["owner"]["objectId"], "transaction", "owner")

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
        if filters:
            try:
                FilterCompiler.compile(filters, columns)
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
        return self.dimensions_rules.population_columns(owner, source)

    def grain_type(self, level: dict, column_id: str, seen=None) -> str | None:
        return self.dimensions_rules.grain_type(level, column_id, seen)

    def dimension_type(self, dimension: dict, column_id: str) -> str | None:
        return self.dimensions_rules.dimension_type(dimension, column_id)

    def compatible(self, owner, left, right, path):
        if left and right and self.normalized_type(left) != self.normalized_type(right):
            self.error(
                owner,
                "key_type_mismatch",
                f"Key types {left} and {right} require an explicit conversion.",
                path,
            )

    def validate_dimensionMembers(self, obj: dict):
        return self.dimension_members_rules.validate_dimensionMembers(obj)

    def validate_dimension(self, obj: dict):
        return self.dimensions_rules.validate_dimension(obj)

    def validate_transaction(self, obj: dict):
        return self.transactions_rules.validate_transaction(obj)

    def rolls_up(self, source_level: str, target_level: str) -> bool:
        return self.measures_rules.rolls_up(source_level, target_level)

    def available(self, measure: dict, target_level: str) -> bool:
        return self.measures_rules.available(measure, target_level)

    def validate_measure(self, obj: dict):
        return self.measures_rules.validate_measure(obj)

    def validate_allocation(self, obj):
        return self.measures_rules.validate_allocation(obj)

    def validate_expression(
        self, owner: dict, node: dict, level: dict | None, depth: int
    ):
        return self.formulas_rules.validate_expression(owner, node, level, depth)

    def expression_unit(self, owner, node, depth=0):
        return self.formulas_rules.expression_unit(owner, node, depth)

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
                    "Dependency cycle: "
                    + " → ".join(
                        self.objects.get(mid, {}).get("name", mid)
                        for mid in path[path.index(node) :] + [node]
                    ),
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
