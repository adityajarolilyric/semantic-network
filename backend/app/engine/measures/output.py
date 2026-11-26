from sqlglot import parse_one

from ...core.errors import MetadataError
from ...warehouse.sql import quote_identifier as q
from ..graph.representations import RepresentationPolicy
from ..sql.compilation_checkpoint import CompilationCheckpoint
from ..sql.contracts import Check, ExecutionPlan, RelationSchema
from ..sql.shared_reads import combine_shared_reads
from .contracts import COMPLETE, MEASURE_COMPILER_VERSION


class MeasureOutputCompiler:
    """Assemble one materialised population output and its validation checks."""

    def __init__(self, compiler):
        self.compiler = compiler

    def compile_level(self, target, *, include_checks=True):
        available, unavailable = ({}, {})
        placement = RepresentationPolicy(
            self.compiler.context.graph, self.compiler.base_id
        )
        for mid, measure in self.compiler.measures.items():
            if not placement.includes(mid, target):
                continue
            checkpoint = CompilationCheckpoint.capture(self.compiler)
            try:
                available[mid] = self.compiler.compile_measure_at_level(mid, target)
            except MetadataError as exc:
                if exc.code not in (
                    "measure_path_unavailable",
                    "formula_grain_unavailable",
                ):
                    raise
                checkpoint.restore()
                unavailable[mid] = {"code": exc.code, "message": exc.message}
                if measure["definition"]["nativeGrainId"] == target:
                    raise
        columns = [dict(c) for c in self.compiler.populations[target]["columns"]]
        used = {c["name"] for c in columns}
        selected = ["p." + q(c["columnId"]) + " AS " + q(c["name"]) for c in columns]
        joins = []
        for index, (mid, node) in enumerate(available.items()):
            measure = self.compiler.measures[mid]
            name = measure["name"]
            suffix = index + 1
            while name in used:
                name = "Measure__" + measure["name"] + "__" + str(suffix)
                suffix += 1
            used.add(name)
            alias = "v" + str(index)
            value, typ = self.compiler.cast_node_value(alias, node, measure)
            selected.append(value + " AS " + q(name))
            joins.append(
                f"LEFT JOIN {q(node)} {alias} ON "
                + self.compiler.join_keys("p", alias, target)
            )
            columns.append(
                {
                    "columnId": mid,
                    "name": name,
                    "displayName": measure.get("displayName", measure["name"]),
                    "physicalType": typ,
                    "nullable": node in self.compiler.coverage_nodes
                    or measure["definition"]["resultType"]["nullable"],
                    "role": "measure",
                    "measureId": mid,
                    "nativeGrainId": measure["definition"]["nativeGrainId"],
                    "editableAtLevel": self.compiler.relationships.can_edit(
                        measure, target
                    ),
                }
            )
        for index, (mid, node) in enumerate(available.items()):
            coverage = self.compiler.coverage_nodes.get(node)
            if not coverage:
                continue
            name = "__coverage_" + mid
            while name in used:
                name += "_"
            used.add(name)
            alias = "v" + str(index)
            selected.append(f"toUInt8(coalesce({alias}.{q(COMPLETE)},0)) AS {q(name)}")
            columns.append(
                {
                    "columnId": "coverage_" + mid,
                    "name": name,
                    "physicalType": "UInt8",
                    "nullable": False,
                    "role": "coverage",
                    "coverageFor": mid,
                }
            )
        for index, (mid, node) in enumerate(available.items()):
            if node not in self.compiler.mixed_nodes:
                continue
            name = "__mixed_" + mid
            while name in used:
                name += "_"
            used.add(name)
            selected.append(
                f"toUInt8(coalesce(v{index}.{q(COMPLETE)},1)=2) AS {q(name)}"
            )
            columns.append(
                {
                    "columnId": "mixed_" + mid,
                    "name": name,
                    "physicalType": "UInt8",
                    "nullable": False,
                    "role": "mixed",
                    "mixedFor": mid,
                }
            )
        sql = self.compiler.with_sql(
            f"SELECT {', '.join(selected)} FROM ({self.compiler.output_population(target)}) p {' '.join(joins)}"
        )
        checks = tuple(
            (
                Check(
                    c.code,
                    combine_shared_reads(
                        parse_one(self.compiler.with_sql(c.sql), read="clickhouse"),
                        self.compiler.shared_relations,
                    ).sql(dialect="clickhouse"),
                )
                for c in (self.compiler.assertions if include_checks else ())
            )
        )
        return (
            ExecutionPlan(
                combine_shared_reads(
                    parse_one(sql, read="clickhouse"), self.compiler.shared_relations
                ),
                checks,
                RelationSchema(tuple(columns)),
                MEASURE_COMPILER_VERSION,
            ),
            unavailable,
        )
