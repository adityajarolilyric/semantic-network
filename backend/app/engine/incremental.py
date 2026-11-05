"""Dependency-scoped representation fingerprints and snapshot-backed compilation."""

from copy import deepcopy

from ..core.errors import MetadataError
from ..domain.measure_policies import MEASURE_TYPE_POLICY, normalize_measure
from .contracts import digest
from .measure_compiler import (
    MATERIALISATION_POLICY,
    MEASURE_COMPILER_VERSION,
    MeasureCompiler,
    MeasureNode,
)
from .relationships import LevelRelationshipAnalyzer
from .table_storage import STORAGE_POLICY

INCREMENTAL_POLICY = "incremental-representations-v1"


def formula_references(node):
    if node.get("type") == "measureRef":
        return {node["measureId"]}
    return set().union(
        *(formula_references(v) for v in node.values() if isinstance(v, dict)),
        *(
            formula_references(v)
            for values in node.values()
            if isinstance(values, list)
            for v in values
            if isinstance(v, dict)
        ),
    )


class MaterialisationManifest:
    """Hashes evaluation inputs separately from output labels/edit permissions."""

    def __init__(self, objects, populations, managed, base):
        self.objects = {o["objectId"]: normalize_measure(o) for o in objects}
        self.measures = {
            k: o for k, o in self.objects.items() if o["kind"] == "measure"
        }
        self.populations, self.managed, self.base = populations, managed, base
        self.relationships = LevelRelationshipAnalyzer(objects)
        self.closures = {}

    def closure(self, mid, seen=frozenset()):
        if mid in self.closures:
            return self.closures[mid]
        if mid in seen or len(seen) > 128:
            raise MetadataError(
                "measure_dependency_cycle",
                "Invalid incremental measure dependency graph.",
            )
        d = self.measures[mid]["definition"]
        refs = (
            formula_references(d["formula"]["expression"])
            if d["measureType"] == "calculated"
            else set()
        )
        result = {mid}
        for dep in refs:
            result |= self.closure(dep, seen | {mid})
        self.closures[mid] = result
        return result

    def evaluation(self, mid, target):
        measures, levels, dimensions, sources = {}, set(), set(), set()
        for dep in self.closure(mid):
            d = deepcopy(self.measures[dep]["definition"])
            d.pop("allocation", None)  # Edit policies do not initialise source values.
            d.pop("editable", None)
            if d.get("formula"):
                d["formula"] = {"expression": d["formula"]["expression"]}
            measures[dep] = d
            native = d["nativePlanningLevelId"]
            levels.add(native)
            if (
                self.relationships.analyze(native, self.base)["relationship"]
                != "incompatible"
            ):
                levels.add(self.base)
            if d["measureType"] == "referenced":
                tx = self.objects[d["transactionId"]]
                measures["transaction:" + tx["objectId"]] = tx["definition"]
                sources.add(tx["definition"]["sourceId"])
                self.find_dimensions(tx["definition"], dimensions)
        levels.add(target)
        pending = list(levels)
        while pending:
            lid = pending.pop()
            level = self.objects[lid]["definition"]
            self.find_dimensions(level, dimensions)
            pop = level["population"]
            parent = pop.get("sourcePlanningLevelId")
            if parent and parent not in levels:
                levels.add(parent)
                pending.append(parent)
        dims = {did: self.objects[did] for did in dimensions}
        sources |= {d["definition"]["sourceId"] for d in dims.values()}
        if any(
            sid not in self.managed or sid not in self.objects for sid in sources
        ) or any(lid not in self.populations for lid in levels):
            raise MetadataError(
                "incremental_input_missing",
                "Incremental evaluation inputs are incomplete.",
            )
        return digest(
            {
                "policy": [
                    INCREMENTAL_POLICY,
                    STORAGE_POLICY,
                    MATERIALISATION_POLICY,
                    MEASURE_TYPE_POLICY,
                    MEASURE_COMPILER_VERSION,
                ],
                "base": self.base,
                "measures": measures,
                "levels": {
                    lid: {
                        "definition": self.objects[lid]["definition"],
                        "artifact": self.populations.get(lid, {}).get("artifactId"),
                    }
                    for lid in sorted(levels)
                },
                "dimensions": {
                    did: {"isTimeDim": d["isTimeDim"], "definition": d["definition"]}
                    for did, d in dims.items()
                },
                "sources": {
                    sid: {
                        "artifact": self.managed.get(sid, {}).get("artifactId"),
                        "definition": self.objects[sid]["definition"],
                    }
                    for sid in sorted(sources)
                },
            }
        )

    @staticmethod
    def find_dimensions(value, result):
        if isinstance(value, dict):
            if value.get("dimensionId"):
                result.add(value["dimensionId"])
            for v in value.values():
                MaterialisationManifest.find_dimensions(v, result)
        elif isinstance(value, list):
            for v in value:
                MaterialisationManifest.find_dimensions(v, result)

    def all(self):
        result = {}
        for lid, population in self.populations.items():
            evaluations = {mid: self.evaluation(mid, lid) for mid in self.measures}
            result[lid] = {
                "policy": INCREMENTAL_POLICY,
                "evaluations": evaluations,
                "layoutHash": digest(
                    {
                        "evaluations": evaluations,
                        "levelName": self.objects[lid]["name"],
                        "population": population["artifactId"],
                        "measures": {
                            mid: {
                                "name": m["name"],
                                "displayName": m.get("displayName"),
                                "definition": m["definition"],
                            }
                            for mid, m in self.measures.items()
                        },
                    }
                ),
            }
        return result


def reusable(snapshot, manifest, mid):
    old = snapshot.get("materialisationManifest", {})
    return (
        old.get("policy") == INCREMENTAL_POLICY
        and old.get("evaluations", {}).get(mid) == manifest["evaluations"][mid]
        and (
            mid in snapshot.get("measureColumns", {})
            or mid in snapshot.get("unavailable", {})
        )
    )


class IncrementalMeasureCompiler(MeasureCompiler):
    def __init__(self, *args, snapshots, manifests, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshots, self.manifests = snapshots, manifests

    def can_reuse(self, mid, level):
        return level in self.manifests and reusable(
            self.snapshots.get(level, {}), self.manifests[level], mid
        )

    def compile_measure_at_level(self, mid, target):
        if not self.can_reuse(mid, target):
            return super().compile_measure_at_level(mid, target)
        key = (mid, target)
        if key not in self.cache:
            snapshot = self.snapshots[target]
            if mid in snapshot.get("unavailable", {}):
                reason = snapshot["unavailable"][mid]
                raise MetadataError(reason["code"], reason["message"])
            self.cache[key] = self.stored_measure(mid, target, self.snapshots)
            self.nodes.append(MeasureNode(mid, target, "reuse"))
        return self.cache[key]

    def compile_seed_measure(self, mid):
        native = self.measures[mid]["definition"]["nativePlanningLevelId"]
        if self.can_reuse(mid, native) and mid in self.snapshots[native].get(
            "measureColumns", {}
        ):
            return self.compile_measure_at_level(mid, native)
        return super().compile_seed_measure(mid)
