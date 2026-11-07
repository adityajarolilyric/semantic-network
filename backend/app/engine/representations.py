"""Storage placement is independent of semantic viewing compatibility."""

from .grain_contexts import resolve_grain_contexts
from .relationships import LevelRelationshipAnalyzer

ROLLUPS = {"same", "equivalent", "roll_up"}


class RepresentationPolicy:
    """Share values at Transaction grains and Base, never every lookup Dimension."""

    def __init__(self, objects, base_id):
        objects = list(objects.values()) if isinstance(objects, dict) else objects
        objects = resolve_grain_contexts(objects)
        self.measures = {
            obj["objectId"]: obj for obj in objects if obj["kind"] == "measure"
        }
        self.base_id = base_id
        self.native_targets = {
            obj["definition"].get("nativeGrainId")
            or obj["definition"].get("dimensionId")
            for obj in objects
            if obj["kind"] == "transaction"
        } - {None}
        self.relationships = LevelRelationshipAnalyzer(objects)

    def includes(self, measure_id, grain_id):
        native = self.measures[measure_id]["definition"]["nativeGrainId"]
        if grain_id in {native, self.base_id}:
            return True
        if grain_id not in self.native_targets:
            return False
        if self.relationships.analyze(native, grain_id)["relationship"] in ROLLUPS:
            return True
        return bool(
            self.base_id
            and self.relationships.analyze(native, self.base_id)["relationship"]
            != "incompatible"
            and self.relationships.analyze(self.base_id, grain_id)["relationship"]
            in ROLLUPS
        )

    def targets(self):
        return (
            self.native_targets
            | {
                measure["definition"]["nativeGrainId"]
                for measure in self.measures.values()
            }
            | ({self.base_id} if self.base_id else set())
        )
