from ...core.errors import MetadataError
from ..graph.grain_contexts import resolve_grain_contexts
from .relationships import TARGET, ViewRelationships


class MeasureEditPlanner:
    def __init__(self, objects, base_id, fields):
        self.objects = {o["objectId"]: o for o in resolve_grain_contexts(objects)}
        self.base_id, self.fields = base_id, fields
        self.relationships = ViewRelationships(objects, fields)

    def capability(self, mid, snapshots, draft=True):
        d = self.objects[mid]["definition"]
        allowed = ("same", "equivalent", "roll_up")
        native = d["nativeGrainId"]
        anchor = (
            self.base_id
            if all(
                self.relationships.analyze(self.base_id, target)["relationship"]
                in allowed
                for target in (native, TARGET)
            )
            else native
        )
        artifact = snapshots.get(anchor, {})
        reason = None
        if not draft:
            reason = "Published revisions are read-only."
        elif d["measureType"] != "referenced":
            reason = "Calculated measures are derived; edit an input measure instead."
        elif d.get("resultType", {"type": "number"})["type"] not in {
            "number",
            "decimal",
            "integer",
        }:
            reason = "String, Date and Boolean measures are read-only."
        elif not d.get("editable"):
            reason = "This measure is not marked editable."
        elif self.relationships.analyze(native, TARGET)["relationship"] not in allowed:
            reason = (
                "Editing is allowed only at the native grain or a proven coarser grain."
            )
        elif artifact.get("storageMode") == "source" and d["aggregation"][
            "nonTime"
        ] in {"count", "count_distinct"}:
            reason = "Source-backed counts are read-only; changing a count requires adding or removing source records."
        elif (
            artifact.get("storageMode") == "source"
            and d["valueColumnId"]
            in self.objects[native]["definition"]["grain"]["keyIds"]
        ):
            reason = "A Transaction grain-key column cannot be edited as a measure."
        elif artifact.get("storageMode") == "source" and d[
            "valueColumnId"
        ] in self.objects[artifact["sourceId"]]["definition"].get("keyColumns", []):
            reason = (
                "A declared source primary-key column cannot be edited as a measure."
            )
        elif mid not in artifact.get("measureColumns", {}):
            reason = "Materialise this measure at its write level before editing."
        elif (d.get("allocation") or {}).get("mode") == "weighted" and d["allocation"][
            "basisMeasureId"
        ] not in artifact.get("measureColumns", {}):
            reason = "Materialise the disaggregation basis at the write level before editing."
        return {
            "editable": reason is None,
            "editReason": reason,
            "writeLevelId": anchor,
            "writeLevelName": self.objects[anchor].get("name", anchor),
            "writesToBase": anchor == self.base_id,
        }

    def validate(self, payload, snapshots, draft=True):
        if (
            payload.measureId not in self.objects
            or self.objects[payload.measureId]["kind"] != "measure"
        ):
            raise MetadataError(
                "invalid_edit_measure", "The selected measure does not exist."
            )
        capability = self.capability(payload.measureId, snapshots, draft)
        if not capability["editable"]:
            raise MetadataError("measure_not_editable", capability["editReason"])
        if (
            payload.value is None
            and not self.objects[payload.measureId]["definition"]["resultType"][
                "nullable"
            ]
        ):
            raise MetadataError(
                "edit_null_rejected", "This measure does not allow NULL values."
            )
