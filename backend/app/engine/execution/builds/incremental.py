"""Dependency-scoped representation fingerprints and snapshot-backed compilation."""

from copy import deepcopy

from ....core.errors import MetadataError
from ....domain.measure_policies import MEASURE_TYPE_POLICY, TYPED_MEASURE_TYPE_POLICY
from ....domain.typed_values import uses_typed_features
from ....formula_language.expressions import contains_counts, measure_references
from ....warehouse.current_storage import STORAGE_POLICY
from ...graph.grain_contexts import resolve_grain_contexts
from ...graph.relationships import LevelRelationshipAnalyzer
from ...graph.representations import RepresentationPolicy
from ...graph.source_storage import SourceStoragePolicy
from ...measures.compiler import MeasureCompiler
from ...measures.contracts import (
    LEGACY_MEASURE_COMPILER_VERSION,
    MATERIALISATION_POLICY,
    MEASURE_COMPILER_VERSION,
    TYPED_MEASURE_COMPILER_VERSION,
    MeasureNode,
)
from ...measures.dependencies import MeasureDependencies
from ...sql.contracts import digest

INCREMENTAL_POLICY = "incremental-representations-v3-transaction-targets"


def formula_references(node):
    return set(measure_references(node))


class MaterialisationManifest:
    """Hashes evaluation inputs separately from output labels/edit permissions."""

    def __init__(self, objects, populations, managed, base):
        graph = resolve_grain_contexts(objects)
        self.graph = graph
        self.objects = dict(graph.by_id)
        self.measures = {
            k: o for k, o in self.objects.items() if o["kind"] == "measure"
        }
        self.populations, self.managed, self.base = populations, managed, base
        self.source_storage = SourceStoragePolicy(graph, managed)
        self.relationships = LevelRelationshipAnalyzer(graph)
        self.dependencies = MeasureDependencies(graph)
        self.closures = {}

    def closure(self, mid):
        if mid not in self.closures:
            self.closures[mid] = set(self.dependencies.ordered(mid))
        return self.closures[mid]

    def evaluation(self, mid, target):
        measures, levels, dimension_members, sources = {}, set(), set(), set()
        for dep in self.closure(mid):
            d = deepcopy(self.measures[dep]["definition"])
            d.pop("allocation", None)  # Edit policies do not initialise source values.
            d.pop("editable", None)
            for optional in ("temporal", "conversion", "isConversionFactor"):
                if not d.get(optional):
                    d.pop(optional, None)
            if d.get("formula"):
                d["formula"] = {"expression": d["formula"]["expression"]}
            # Retain the v6 evaluation fingerprint for semantically identical
            # saved counts, preserving existing manually edited dependencies.
            if contains_counts((d.get("formula") or {}).get("expression")):
                d["countingDimensionId"] = d["nativeGrainId"]
            measures[dep] = d
            native = d["nativeGrainId"]
            levels.add(native)
            self.find_dimensions(d, dimension_members)

            def attribute_levels(node):
                if isinstance(node, dict):
                    if node.get("type") == "attributeRef" and node.get("dimensionId"):
                        levels.add(node["dimensionId"])
                    for child in node.values():
                        attribute_levels(child)
                elif isinstance(node, list):
                    for child in node:
                        attribute_levels(child)

            attribute_levels(d)
            if (
                self.relationships.analyze(native, self.base)["relationship"]
                != "incompatible"
            ):
                levels.add(self.base)
            if d["measureType"] == "referenced":
                tx = self.objects[d["transactionId"]]
                measures["transaction:" + tx["objectId"]] = tx["definition"]
                sources.add(tx["definition"]["sourceId"])
                self.find_dimensions(tx["definition"], dimension_members)
        levels.add(target)
        pending = list(levels)
        while pending:
            lid = pending.pop()
            level = self.objects[lid]["definition"]
            self.find_dimensions(level, dimension_members)
            pop = level["population"]
            parent = pop.get("sourceDimensionId")
            if parent and parent not in levels:
                levels.add(parent)
                pending.append(parent)
        dims = {did: self.objects[did] for did in dimension_members}
        sources |= {d["definition"]["sourceId"] for d in dims.values()}
        if any(
            sid not in self.managed or sid not in self.objects for sid in sources
        ) or any(lid not in self.populations for lid in levels):
            raise MetadataError(
                "incremental_input_missing",
                "Incremental evaluation inputs are incomplete.",
            )
        typed = any(
            uses_typed_features(self.measures[dep]) for dep in self.closure(mid)
        )
        return digest(
            {
                "policy": [
                    INCREMENTAL_POLICY,
                    STORAGE_POLICY,
                    MATERIALISATION_POLICY,
                    TYPED_MEASURE_TYPE_POLICY if typed else MEASURE_TYPE_POLICY,
                    MEASURE_COMPILER_VERSION
                    if any(
                        contains_counts(
                            (self.measures[dep]["definition"].get("formula") or {}).get(
                                "expression"
                            )
                        )
                        for dep in self.closure(mid)
                    )
                    else TYPED_MEASURE_COMPILER_VERSION
                    if typed
                    else LEGACY_MEASURE_COMPILER_VERSION,
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
                "dimension-members": {
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
            if value.get("memberCatalogId"):
                result.add(value["memberCatalogId"])
            for v in value.values():
                MaterialisationManifest.find_dimensions(v, result)
        elif isinstance(value, list):
            for v in value:
                MaterialisationManifest.find_dimensions(v, result)

    def all(self):
        result = {}
        placement = RepresentationPolicy(self.graph, self.base)
        targets = placement.targets()
        for lid, population in self.populations.items():
            if lid not in targets:
                continue
            evaluations = {
                mid: self.evaluation(mid, lid)
                for mid in self.measures
                if placement.includes(mid, lid)
            }
            result[lid] = {
                **(
                    {"storageMode": "source"}
                    if self.source_storage.binding(lid)
                    else {}
                ),
                "policy": INCREMENTAL_POLICY,
                "evaluations": evaluations,
                "layoutHash": digest(
                    {
                        "evaluations": evaluations,
                        **(
                            {"storage": "source-view-v1"}
                            if self.source_storage.binding(lid)
                            else {}
                        ),
                        "levelName": self.objects[lid]["name"],
                        "population": population["artifactId"],
                        "measures": {
                            mid: {
                                "name": m["name"],
                                "displayName": m.get("displayName"),
                                "definition": m["definition"],
                            }
                            for mid, m in self.measures.items()
                            if mid in evaluations
                        },
                    }
                ),
            }
        return result


def reusable(snapshot, manifest, mid):
    old = snapshot.get("materialisationManifest", {})
    return (
        old.get("policy") == INCREMENTAL_POLICY
        and old.get("evaluations", {}).get(mid) == manifest["evaluations"].get(mid)
        and (
            mid in snapshot.get("measureColumns", {})
            or mid in snapshot.get("unavailable", {})
        )
    )


class IncrementalMeasureCompiler(MeasureCompiler):
    def __init__(self, *args, snapshots, manifests, forced_measures=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshots, self.manifests = snapshots, manifests
        self.forced_measures = set(forced_measures)

    def can_reuse(self, mid, level):
        return (
            mid not in self.forced_measures
            and level in self.manifests
            and mid in self.manifests[level]["evaluations"]
            and reusable(self.snapshots.get(level, {}), self.manifests[level], mid)
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
        native = self.measures[mid]["definition"]["nativeGrainId"]
        if self.can_reuse(mid, native) and mid in self.snapshots[native].get(
            "measureColumns", {}
        ):
            return self.compile_measure_at_level(mid, native)
        return super().compile_seed_measure(mid)
