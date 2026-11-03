from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any


def digest(value):
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ColumnRef:
    relation_id: str
    column_id: str


@dataclass(frozen=True)
class RawSourceScan:
    relation_id: str
    table_name: str
    columns: tuple[dict, ...]


@dataclass(frozen=True)
class Project:
    source: RawSourceScan
    column_ids: tuple[str, ...]


@dataclass(frozen=True)
class Pivot:
    source: RawSourceScan
    stage: dict


@dataclass(frozen=True)
class RelationSchema:
    columns: tuple[dict, ...]


@dataclass(frozen=True)
class LogicalPlan:
    root: Project | Pivot
    schema: RelationSchema
    definition: dict


@dataclass(frozen=True)
class Check:
    code: str
    sql: str


@dataclass(frozen=True)
class ExecutionPlan:
    query: Any  # SQLGlot Select AST, generated only from analyzed metadata.
    checks: tuple[Check, ...]
    schema: RelationSchema
    compiler_version: str = "managed-v1-sqlglot-27.28.1"

    @property
    def sql(self):
        return self.query.sql(dialect="clickhouse")


@dataclass(frozen=True)
class RelationScan:
    table_name: str
    columns: tuple[tuple[str, str], ...]  # logical output ID, physical input name
    filters: dict | None = None
    generation: str | None = None
    nonnullable_ids: tuple[str, ...] = ()
    transform: LogicalPlan | None = None


@dataclass(frozen=True)
class CrossJoin:
    factors: tuple["RelationScan", ...]


@dataclass(frozen=True)
class DistinctProject:
    source: RelationScan


@dataclass(frozen=True)
class LookupJoin:
    source: Any
    lookup: RelationScan
    mappings: tuple[tuple[str, str], ...]
    outputs: tuple[tuple[str, str], ...]  # output ID, lookup ID
    match_id: str


@dataclass(frozen=True)
class Filter:
    source: Any
    definition: dict


@dataclass(frozen=True)
class PlanningPlan:
    root: Any
    schema: RelationSchema
    key_ids: tuple[str, ...]
    scans: tuple[RelationScan, ...]
    lookups: tuple[RelationScan, ...]
    joins: tuple[LookupJoin, ...]
    projection: bool
