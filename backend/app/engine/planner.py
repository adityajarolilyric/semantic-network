from copy import deepcopy
import re
from ..core.errors import MetadataError
from ..schemas.managed_tables import ManagedDefinition
from .contracts import (
    LogicalPlan,
    Pivot,
    Project,
    RawSourceScan,
    RelationSchema,
    digest,
)


def invalid(message):
    raise MetadataError("invalid_transformation", message)


def pivot_operation(stage):
    if stage.get("recordKeyColumnIds") is not None:
        return "merge_rows" if stage["recordKeyColumnIds"] else "group_values"
    if stage.get("mode") == "auto":
        if not stage.get("resolvedMode"):
            invalid(
                "Legacy automatic pivots require their saved resolvedMode; new pivots must supply recordKeyColumnIds."
            )
        return stage["resolvedMode"]
    return stage.get("mode") or "merge_rows"


class ManagedTablePlanner:
    """Allowlisted operators. Stable output identity excludes alias, order and revision."""

    def plan(self, source, definition: ManagedDefinition):
        spec = definition.model_dump()
        # Omitted mode preserves historical definitions/hashes and merge semantics.
        for stage in spec["pipeline"]:
            if stage.get("mode") is None:
                stage.pop("mode", None)
        columns = source["definition"]["columns"]
        by_id = {c["columnId"]: c for c in columns}
        scan = RawSourceScan(
            source["objectId"], source["definition"]["tableName"], tuple(columns)
        )
        active = next((s for s in spec["pipeline"] if s["enabled"]), None)
        if active is None:
            output = [
                {**c, "lineage": {"kind": "source", "sourceColumnId": c["columnId"]}}
                for c in columns
            ]
            root = Project(scan, tuple(by_id))
        else:
            keys = active["groupByColumnIds"]
            if len(keys) != len(set(keys)) or not set(keys) <= by_id.keys():
                invalid("Choose distinct grouping columns from the raw source.")
            pairs = active["pairs"]
            mode = pivot_operation(active)
            if mode == "merge_rows" and not keys:
                invalid("Merge rows requires at least one retained/group-by column.")
            if (
                mode in ("expand_rows", "group_values")
                or active.get("recordKeyColumnIds") is not None
            ):
                consumed = {
                    column_id
                    for pair in pairs
                    for column_id in (pair["attributeColumnId"], pair["valueColumnId"])
                }
                keys = [key for key in keys if key not in consumed]
                active["groupByColumnIds"] = keys
            record_keys = active.get("recordKeyColumnIds")
            if record_keys is not None and (
                len(record_keys) != len(set(record_keys))
                or not set(record_keys) <= set(keys)
            ):
                invalid(
                    "Record keys must be distinct retained source columns, not consumed attribute/value inputs."
                )
            if len({p["pairId"] for p in pairs}) != len(pairs):
                invalid("Pivot pair identities must be unique.")
            output = [
                {**by_id[k], "lineage": {"kind": "source", "sourceColumnId": k}}
                for k in keys
            ]
            for pair in pairs:
                if (
                    pair["attributeColumnId"] not in by_id
                    or pair["valueColumnId"] not in by_id
                ):
                    invalid("Pivot columns must belong to the raw source.")
                value = by_id[pair["valueColumnId"]]
                physical = value["physicalType"]
                if not re.fullmatch(r"[A-Za-z0-9_(), .]+", physical):
                    invalid("Unsupported pivot physical type.")
                if physical.startswith(
                    ("Array(", "Map(", "Tuple(", "Nested(", "AggregateFunction(")
                ):
                    invalid("Pivot values must have scalar physical types.")
                seen = set()
                for cell in pair["outputs"]:
                    literal = digest(cell["attributeValue"])
                    if literal in seen:
                        invalid("Select each attribute value only once within a pair.")
                    seen.add(literal)
                    cid = (
                        "pv_"
                        + digest(
                            [
                                source["objectId"],
                                active["stageId"],
                                pair["pairId"],
                                cell["attributeValue"],
                            ]
                        )[:28]
                    )
                    if cell["columnId"] not in (None, cid):
                        invalid("Pivot output column identity cannot be reassigned.")
                    cell["columnId"] = cid
                    output.append(
                        {
                            "columnId": cid,
                            "name": cell["name"],
                            "physicalType": physical,
                            "nullable": True,
                            "unit": value.get("unit"),
                            "lineage": {
                                "kind": "pivot",
                                "stageId": active["stageId"],
                                "pairId": pair["pairId"],
                                "attributeColumnId": pair["attributeColumnId"],
                                "valueColumnId": pair["valueColumnId"],
                                "attributeValue": cell["attributeValue"],
                            },
                        }
                    )
            root = Pivot(scan, active)
        if len(output) > 2000 or len({c["name"] for c in output}) != len(output):
            invalid("Managed output names must be unique (maximum 2000 columns).")
        pk = spec["primaryKeyColumnIds"]
        if len(pk) != len(set(pk)) or not set(pk) <= {c["columnId"] for c in output}:
            invalid(
                "The declared managed key must use distinct effective output columns."
            )
        spec["columns"] = deepcopy(output)
        return LogicalPlan(root, RelationSchema(tuple(output)), spec)
