"""Database-side measure validation and column writes; never returns fact rows."""

from ..core.errors import MetadataError
from .sql import quote_identifier as q, quote_literal as lit


def lookup_name(artifact):
    # Deterministic per candidate so crash recovery can remove the lookup too.
    return artifact["physicalName"] + "_lookup"


def result_name(artifact):
    return artifact["physicalName"] + "_values"


def key_expression(columns, field, alias=None):
    prefix = q(alias) + "." if alias else ""
    return (
        "toJSONString(tuple(" + ", ".join(prefix + q(c[field]) for c in columns) + "))"
    )


class MeasureColumnSql:
    """SQL operations over one fenced build candidate, using the shared client."""

    def __init__(self, warehouse):
        self.warehouse = warehouse

    def validate(self, client, relation, keys, columns, settings, *, expected=None):
        key = key_expression(keys, "name")
        null_keys = " OR ".join("isNull(" + q(c["name"]) + ")" for c in keys)
        nulls = ["countIf(" + null_keys + ")"] + [
            "countIf(isNull(" + q(c["name"]) + "))"
            for c in columns
            if not c["nullable"]
        ]
        result = client.query(
            "SELECT count(), uniqExact("
            + key
            + ")"
            + (", " + ", ".join(nulls) if nulls else "")
            + " FROM ("
            + relation
            + ")",
            settings=settings,
        ).first_row
        count, unique = map(int, result[:2])
        if unique != count or (expected is not None and count != expected):
            raise MetadataError(
                "measure_cardinality",
                "Column updates must cover each native intersection exactly once.",
            )
        if result[2]:
            raise MetadataError(
                "null_planning_key", "Dimension grain keys cannot be NULL."
            )
        if any(result[3:]):
            raise MetadataError(
                "measure_null_result", "A non-nullable measure produced NULL."
            )
        return count

    def write(self, client, artifact, relation, keys, columns, settings, expected):
        """One lookup-backed mutation for a compatible group of changed measures."""
        columns = [
            {**column, "lookupName": f"__value_{index}"}
            for index, column in enumerate(columns)
        ]
        name = lookup_name(artifact)
        result = result_name(artifact)
        lookup = self.warehouse.qualify(name)
        staged = self.warehouse.qualify(result)
        table = self.warehouse.qualify(artifact["physicalName"])
        marker = artifact["buildOwner"]
        for temporary in (name, result):
            if self.warehouse.table_info(client, temporary):
                raise MetadataError(
                    "artifact_ownership",
                    "Column staging already exists. Recover the interrupted build before retrying.",
                    409,
                )
        mutation_started = False
        mutation_completed = False
        try:
            # Capture the expensive expression exactly once. Later validation,
            # lookup construction and verification read this narrow disk stage.
            selected = ", ".join(
                q(c["name"]) + " AS " + q(c["lookupName"]) for c in columns
            )
            null_keys = " OR ".join("isNull(" + q(c["name"]) + ")" for c in keys)
            declarations = ", ".join(
                q(c["lookupName"]) + " Nullable(" + c["physicalType"] + ")"
                for c in columns
            )
            client.command(
                f"CREATE TABLE {staged} (__update_key String, __valid_key UInt8, {declarations}) ENGINE=MergeTree ORDER BY tuple() COMMENT {lit(marker + ':values')}"
            )
            client.command(
                f"INSERT INTO {staged} SELECT {key_expression(keys, 'name')}, toUInt8(NOT ({null_keys})), {selected} FROM ({relation}) LIMIT {self.warehouse.settings.planning_max_rows + 1}",
                settings=settings,
            )
            nulls = [
                f"countIf(isNull({q(c['lookupName'])}))"
                for c in columns
                if not c["nullable"]
            ]
            summary = client.query(
                "SELECT count(), uniqExact(__update_key), countIf(__valid_key=0)"
                + (", " + ", ".join(nulls) if nulls else "")
                + f" FROM {staged}",
                settings=settings,
            ).first_row
            if summary[0] != expected or summary[1] != expected:
                raise MetadataError(
                    "measure_cardinality",
                    "Column updates must cover each native intersection exactly once.",
                )
            if summary[2]:
                raise MetadataError(
                    "null_planning_key", "Dimension grain keys cannot be NULL."
                )
            if any(summary[3:]):
                raise MetadataError(
                    "measure_null_result", "A non-nullable measure produced NULL."
                )
            declarations = ", ".join(
                q(c["lookupName"]) + " Nullable(" + c["physicalType"] + ")"
                for c in columns
            )
            memory = int(settings["max_memory_usage"])
            client.command(
                f"CREATE TABLE {lookup} (__update_key String, __present UInt8, {declarations}) "
                f"ENGINE=Join(ANY, LEFT, __update_key) SETTINGS join_use_nulls=1, max_bytes_in_join={memory}, join_overflow_mode='throw' COMMENT {lit(marker + ':lookup')}"
            )
            selected = ", ".join(q(c["lookupName"]) for c in columns)
            client.command(
                f"INSERT INTO {lookup} SELECT __update_key, toUInt8(1), {selected} FROM {staged}",
                settings=settings,
            )
            # Join(ANY) collapses duplicate keys. Compare with the validated input
            # count, then prove full target coverage before changing any values.
            stored_key = key_expression(keys, "storageName")
            lookup_literal = lit(
                self.warehouse.settings.clickhouse_database + "." + name
            )
            present = f"joinGet({lookup_literal}, {lit('__present')}, {stored_key})"
            rows, covered, unique = client.query(
                f"SELECT count(), countIf({present}=1), uniqExact({stored_key}) FROM {table}",
                settings=settings,
            ).first_row
            if rows != expected or covered != expected or unique != expected:
                raise MetadataError(
                    "measure_cardinality",
                    "Column updates must cover each native intersection exactly once.",
                )
            assignments = ", ".join(
                q(c["storageName"])
                + "=joinGet("
                + lookup_literal
                + ", "
                + lit(c["lookupName"])
                + ", "
                + stored_key
                + ")"
                for c in columns
            )
            mutation_started = True
            client.command(
                f"ALTER TABLE {table} UPDATE {assignments} WHERE 1",
                settings={**settings, "mutations_sync": 2},
            )
            mutation_completed = True
            # All equality checks happen in ClickHouse; Python receives one count.
            differences = []
            for c in columns:
                actual = q(c["storageName"])
                wanted = (
                    f"joinGet({lookup_literal}, {lit(c['lookupName'])}, {stored_key})"
                )
                differences.append(
                    f"NOT ifNull({actual}={wanted}, isNull({actual}) AND isNull({wanted}))"
                )
            mismatches = int(
                client.command(
                    f"SELECT countIf({' OR '.join(differences)}) FROM {table}",
                    settings=settings,
                )
            )
            if mismatches:
                raise MetadataError(
                    "measure_update_mismatch",
                    "Persisted measure values do not match the completed update.",
                )
        finally:
            # A timeout can leave a mutation running. Cancel it before releasing
            # its lookup. Recovery also cleans up this owned deterministic name.
            if mutation_started and not mutation_completed:
                client.command(
                    "KILL MUTATION WHERE database={db:String} AND table={table:String} AND is_done=0 SYNC",
                    parameters={
                        "db": self.warehouse.settings.clickhouse_database,
                        "table": artifact["physicalName"],
                    },
                )
            for temporary, role in ((name, "lookup"), (result, "values")):
                info = self.warehouse.table_info(client, temporary)
                if info and info[1] == marker + ":" + role:
                    client.command(
                        f"DROP TABLE {self.warehouse.qualify(temporary)} SYNC"
                    )
