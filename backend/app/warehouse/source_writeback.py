"""Allocate native edits into physical source records, entirely in ClickHouse."""

from ..core.errors import MetadataError
from ..engine.measures.contracts import VALUE
from .sql import quote_identifier as q, quote_literal as lit


class SourceWriteback:
    def __init__(self, warehouse):
        self.warehouse = warehouse

    async def apply(
        self,
        artifact,
        binding,
        measure,
        delta_sql,
        settings,
        *,
        basis_column=None,
        factor_sql=None,
    ):
        definition = measure["definition"]
        mode = (definition.get("allocation") or {"mode": "none"})["mode"]
        method = definition["aggregation"]["nonTime"]
        if method in {"count", "count_distinct"}:
            raise MetadataError(
                "source_edit_aggregation",
                "Source-backed counts are read-only: changing a count requires adding or removing source records.",
            )
        if mode == "weighted" and not basis_column:
            raise MetadataError(
                "source_edit_basis",
                "Source-row weighted edits require a referenced basis column from the same Transaction.",
            )
        column = binding["columns"][definition["valueColumnId"]]["name"]
        if definition["valueColumnId"] in binding["keyColumns"] or definition[
            "valueColumnId"
        ] in binding.get("primaryKeyColumnIds", []):
            raise MetadataError(
                "source_edit_key",
                "A Transaction grain-key column cannot be edited as a measure.",
            )
        keys = list(binding["keyColumns"].items())
        lookup_name = artifact["sourceCloneName"] + "_lookup"
        lookup = self.warehouse.qualify(lookup_name)
        table = self.warehouse.qualify(artifact["sourceCloneName"])
        source_keys = ", ".join("s." + q(name) for _, name in keys)
        joins = " AND ".join(f"s.{q(name)}=d.{q(key)}" for key, name in keys)
        raw = f"toFloat64(s.{q(column)})"
        weight = f"toFloat64(s.{q(basis_column)})" if mode == "weighted" else raw
        # Normalise by the configured native reducer for self weights, retaining
        # AVG/MIN/MAX semantics when there are several physical records per grain.
        native_reducer = "singleValueOrNull" if method == "single_value" else method
        normalizer = (
            f"{native_reducer}({weight})"
            if mode in {"weighted", "weighted_by_self"}
            else f"sum({weight})"
        )
        value = f"d.{q(VALUE)}"
        if factor_sql:
            factor = "f." + q(VALUE)
            value = (
                f"({value})/nullIf({factor},0)"
                if definition["conversion"]["operation"] == "multiply"
                else f"({value})*{factor}"
            )
        factor_join = (
            f" LEFT JOIN ({factor_sql}) f ON "
            + " AND ".join(f"d.{q(key)}=f.{q(key)}" for key, _ in keys)
            if factor_sql
            else ""
        )
        selected_key = "toJSONString(tuple(" + source_keys + "))"
        relation = f"SELECT {selected_key} AS __key, any({value}) AS __target, {normalizer} AS __normalizer, count() AS __rows FROM {table} s INNER JOIN ({delta_sql}) d ON {joins}{factor_join} GROUP BY {source_keys}"
        arguments = (
            "toJSONString(tuple(" + ", ".join(q(name) for _, name in keys) + "))"
        )
        lookup_identity = (
            self.warehouse.settings.clickhouse_database + "." + lookup_name
        )

        def get(field):
            return f"joinGetOrNull({lit(lookup_identity)}, {lit(field)}, {arguments})"

        target, total, count = get("__target"), get("__normalizer"), get("__rows")
        expression = target
        if mode == "equal" and method == "sum":
            expression = f"({target})/nullIf({count},0)"
        elif mode in {"weighted", "weighted_by_self"}:
            source_weight = q(basis_column) if mode == "weighted" else q(column)
            expression = f"if(isNull({target}),NULL,if(isNull({source_weight}),{q(column)},toFloat64({source_weight})*({target})/nullIf({total},0)))"
        expression = f"roundBankers(toFloat64({expression}),6)"

        def write(connect):
            client = connect()
            client.command(
                f"ALTER TABLE {table} MODIFY COLUMN {q(column)} Nullable(Float64)",
                settings=settings,
            )
            client.command(
                f"CREATE TABLE {lookup} (__key String, __target Nullable(Float64), __normalizer Nullable(Float64), __rows UInt64) ENGINE=Join(ANY,LEFT,__key) COMMENT {lit(artifact['buildOwner'] + ':lookup')}"
            )
            client.command(f"INSERT INTO {lookup} {relation}", settings=settings)
            if mode in {"weighted", "weighted_by_self"}:
                invalid = int(
                    client.command(
                        f"SELECT countIf(isNotNull(__target) AND (isNull(__normalizer) OR __normalizer=0)) FROM {lookup}",
                        settings=settings,
                    )
                )
                if invalid:
                    raise MetadataError(
                        "source_edit_weights",
                        "Source-row weights must have a nonzero configured aggregate for each edited native cell.",
                    )
            affected = f"{arguments} IN (SELECT __key FROM {lookup})"
            client.command(
                f"ALTER TABLE {table} UPDATE {q(column)}={expression} WHERE {affected}",
                settings={**settings, "mutations_sync": 2},
            )
            if mode in {"weighted", "weighted_by_self"}:
                # A sign change with MIN/MAX or conflicting single values may
                # not be representable by proportional source-row weights.
                # Refuse publication rather than acknowledge a different value.
                actual = f"{native_reducer}(toFloat64(s.{q(column)}))"
                reduced = f"SELECT {selected_key} AS __key, {actual} AS actual, count() AS rows FROM {table} s GROUP BY {source_keys}"
                mismatch = client.command(
                    f"SELECT count() FROM ({reduced}) a ANY LEFT JOIN {lookup} d USING (__key) WHERE isNotNull(d.__target) AND (isNull(a.actual) OR NOT isFinite(a.actual) OR abs(a.actual-d.__target)>greatest(0.000001*a.rows,abs(d.__target)*1e-12))",
                    settings={**settings, "join_use_nulls": 0},
                )
                if int(mismatch):
                    raise MetadataError(
                        "source_edit_weights",
                        "The source-row weights cannot reproduce this edited value with the configured aggregation. Correct the weights or choose another dis-aggregation policy.",
                    )
            client.command(f"DROP TABLE {lookup} SYNC")

        await self.warehouse.run(write)
        return {
            definition["valueColumnId"]: {"physicalType": "Float64", "nullable": True}
        }
