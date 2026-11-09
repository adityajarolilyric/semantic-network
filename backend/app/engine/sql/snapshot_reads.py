"""One SQLGlot representation of a generation-scoped measure snapshot read."""

from dataclasses import dataclass

from sqlglot import exp, parse_one

from ...core.errors import MetadataError
from ...warehouse.sql import quote_identifier as q
from ..measures.contracts import COMPLETE, VALUE
from .storage import GENERATION, column_sql, token


@dataclass(frozen=True)
class SnapshotRead:
    query: exp.Select
    coverage: bool
    mixed: bool


def snapshot_read(table, artifact, measure_id, keys, *, physical=False):
    if measure_id not in artifact.get("measureColumns", {}):
        raise MetadataError(
            "measure_path_unavailable",
            "This measure was not materialised at this level in the captured generation.",
        )
    column = next(
        column
        for column in artifact["columns"]
        if column.get("measureId") == measure_id
    )
    if artifact.get("storageMode") in {"current", "source"} and not physical:
        names = {c["columnId"]: c["name"] for c in artifact["columns"]}
        aliases = {
            c.get("storageName", c["name"]): c["name"] for c in artifact["columns"]
        }
        artifact = {
            **artifact,
            "keyColumns": {key: names[key] for key in keys},
            "coverageColumns": {
                key: aliases.get(value, value)
                for key, value in artifact.get("coverageColumns", {}).items()
            },
            "mixedColumns": {
                key: aliases.get(value, value)
                for key, value in artifact.get("mixedColumns", {}).items()
            },
        }
        column = {**column, "storageName": column["name"]}
    selected = [
        exp.column(artifact["keyColumns"][key], quoted=True).as_(key, quoted=True)
        for key in keys
    ]
    selected.append(
        parse_one(column_sql(column), read="clickhouse").as_(VALUE, quoted=True)
    )
    coverage = artifact.get("coverageColumns", {}).get(measure_id)
    mixed = artifact.get("mixedColumns", {}).get(measure_id)
    flag = q(coverage) if coverage else "toUInt8(1)"
    if mixed:
        flag = f"if(coalesce({q(mixed)},0),toUInt8(2),{flag})"
    selected.append(parse_one(flag, read="clickhouse").as_(COMPLETE, quoted=True))
    query = (
        exp.select(*selected)
        .from_(exp.to_table(table, dialect="clickhouse"))
        .where(exp.column(GENERATION, quoted=True).eq(exp.convert(token(artifact))))
    )
    if artifact.get("storageMode") in {"current", "source"}:
        query.set("where", None)
    return SnapshotRead(query, bool(coverage), bool(mixed))
