from ...core.errors import MetadataError
from ...formula_language.expressions import contains_counts, walk
from ..graph.compatibility import MeasureCompatibilityAnalyzer
from ..graph.grain_contexts import resolve_grain_contexts
from ..graph.relationships import LevelRelationshipAnalyzer
from .contracts import ViewPlan
from .fields import DecisionFieldResolver
from .relationships import TARGET, ViewRelationships


class DecisionViewPlanner:
    def __init__(self, objects, base_id):
        self.graph = resolve_grain_contexts(objects)
        self.objects = dict(self.graph.by_id)
        self.relationships = LevelRelationshipAnalyzer(self.graph)
        self.base_id = base_id
        self.field_resolver = DecisionFieldResolver(self)

    def fields(self, request):
        return self.field_resolver.fields(request)

    def leaves(self, measures, exact, artifact):
        leaves, visiting = (set(), set())

        def visit(mid):
            obj = self.objects.get(mid)
            if not obj or obj["kind"] != "measure":
                raise MetadataError(
                    "invalid_view_measure", "A selected measure no longer exists."
                )
            if mid in visiting:
                raise MetadataError(
                    "measure_dependency_cycle",
                    "The selected formula has a dependency cycle.",
                )
            d = obj["definition"]
            counted = contains_counts((d.get("formula") or {}).get("expression"))
            if d.get("calculationType") != "AggregateFirst" or (
                exact and mid in artifact.get("measureColumns", {}) and (not counted)
            ):
                leaves.add(mid)
                return
            visiting.add(mid)

            def expression(node):
                if node["type"] == "measureRef":
                    visit(node["measureId"])
                elif node["type"] == "binary":
                    expression(node["left"])
                    expression(node["right"])
                elif node["type"] == "call":
                    for child in node["arguments"]:
                        expression(child)

            expression(d["formula"]["expression"])
            visiting.remove(mid)

        for mid in measures:
            visit(mid)
        return leaves

    def complete_projection(self, level, seen=None):
        if level == self.base_id:
            return True
        seen = set() if seen is None else seen
        if level in seen:
            return False
        definition = self.objects[level]["definition"]
        population = definition["population"]
        return (
            population["operation"] == "distinct_projection"
            and (not definition.get("filters"))
            and self.complete_projection(
                population["sourceDimensionId"], seen | {level}
            )
        )

    def common_sources(self, request, fields):
        """Require a real semantic context, even for an axis-free grand total."""
        relationships = ViewRelationships(self.graph, fields)
        availability = MeasureCompatibilityAnalyzer(
            list(self.objects.values()), self.base_id, relationships
        )
        sources = {
            lid
            for lid in self.relationships.levels
            if all(relationships.resolve(lid, field["coordinate"]) for field in fields)
            and all(
                availability.outcome(mid, lid).available for mid in request.measures
            )
        }
        if not sources:
            names = ", ".join(
                f'"{self.objects[mid]["name"]}"' for mid in request.measures
            )
            raise MetadataError(
                "decision_view_incompatible",
                f"{names} cannot be plotted together with the selected attributes: "
                "no common dimension has a proven semantic path for the complete selection.",
            )
        return sources

    def plan(self, request, populations, values):
        fields = self.fields(request)
        count_source_levels = set()
        visited = set()

        def collect_counts(mid):
            if mid in visited:
                return
            visited.add(mid)
            obj = self.objects.get(mid)
            if not obj or obj["kind"] != "measure":
                return
            d = obj["definition"]
            expression = (d.get("formula") or {}).get("expression")
            if contains_counts(expression):
                count_source_levels.add(d["nativeGrainId"])
            for node in walk(expression):
                if node["type"] == "measureRef":
                    collect_counts(node["measureId"])

        for mid in request.measures:
            collect_counts(mid)
        relationships = ViewRelationships(self.graph, fields)
        availability = MeasureCompatibilityAnalyzer(
            list(self.objects.values()), self.base_id, relationships
        )
        for mid in request.measures:
            result = availability.outcome(mid, TARGET)
            if not result.available:
                raise MetadataError("decision_view_unavailable", result.reason)
        for counting in count_source_levels:
            if (
                counting not in populations
                or counting not in values
                or any(
                    (
                        not relationships.resolve(counting, f["coordinate"])
                        for f in fields
                    )
                )
            ):
                raise MetadataError(
                    "counting_view_unavailable",
                    "The measure’s Output Dimension must be built and determine every selected pivot attribute.",
                )
        common_sources = self.common_sources(request, fields)
        base_outputs = relationships.outputs(self.base_id)
        removed = [
            c
            for c in base_outputs.values()
            if c.column_id
            in relationships.levels[self.base_id]["definition"]["grain"]["keyIds"]
            and (not relationships.resolve(TARGET, c))
        ]
        candidates, rejected = ([], {})
        for lid, artifact in values.items():
            if lid not in populations or lid not in common_sources:
                continue
            if any((not relationships.resolve(lid, f["coordinate"]) for f in fields)):
                rejected[lid] = "Required dimension attributes are unavailable."
                continue
            connected = relationships.analyze(self.base_id, lid)["relationship"] in (
                "same",
                "equivalent",
                "roll_up",
            )
            keys = relationships.levels[lid]["definition"]["grain"]["keyIds"]
            exact = all(
                (
                    relationships.resolve(TARGET, relationships.outputs(lid)[k])
                    for k in keys
                )
            )
            leaves = self.leaves(request.measures, exact, artifact)
            missing = leaves - artifact.get("measureColumns", {}).keys()
            if missing:
                names = ", ".join(
                    f'"{self.objects[mid]["name"]}"' for mid in sorted(missing)
                )
                rejected[lid] = (
                    f"Required measures or formula inputs are not materialised here: {names}."
                )
                continue
            typed_native = bool(leaves) and all(
                (
                    self.objects[mid]["definition"]["resultType"]["type"]
                    in {"string", "date", "boolean"}
                    and self.objects[mid]["definition"]["nativeGrainId"] == lid
                    for mid in leaves
                )
            )
            if (
                connected
                and lid != self.base_id
                and (not typed_native)
                and (not self.complete_projection(lid))
            ):
                rejected[lid] = (
                    "Population coverage is not proven to be a complete projection of Base."
                )
                continue
            if connected and lid != self.base_id and (not exact) and (not typed_native):
                safe = True
                for mid in leaves:
                    policy = self.objects[mid]["definition"]["aggregation"]
                    methods = {
                        policy["time"] if c.is_time else policy["nonTime"]
                        for c in removed
                    }
                    if len(methods) > 1 or not methods <= {
                        "sum",
                        "min",
                        "max",
                        "single_value",
                        "any_true",
                        "all_true",
                    }:
                        safe = False
                    if (
                        policy["nullInputPolicy"] == "ignore"
                        and policy["allNullResult"] == "zero"
                        and methods & {"min", "max"}
                    ):
                        safe = False
                if not safe:
                    rejected[lid] = (
                        "Re-aggregating stored values could change the configured result; use Base."
                    )
                    continue
            candidates.append(
                ViewPlan(
                    lid,
                    exact,
                    fields,
                    leaves,
                    "",
                    native_only=not connected,
                    count_source_levels=tuple(sorted(count_source_levels)),
                )
            )
        if not candidates:
            names = ", ".join(
                f'"{self.objects[mid]["name"]}"' for mid in request.measures
            )
            raise MetadataError(
                "decision_view_build_required",
                f"{names} have no single compatible materialised table containing all required "
                "measures or formula inputs. Build Semantic Network before querying this selection.",
                status=409,
                details=[
                    {"dimension": self.objects[lid]["name"], "message": reason}
                    for lid, reason in rejected.items()
                ],
            )
        chosen = min(
            candidates,
            key=lambda c: (
                not c.exact,
                populations[c.level_id].get("rowCount")
                if populations[c.level_id].get("rowCount") is not None
                else values[c.level_id]["rowCount"],
                len(relationships.levels[c.level_id]["definition"]["grain"]["keyIds"]),
                c.level_id,
            ),
        )
        chosen.explanation = (
            "Uses the measure's native dimension; no path to Base is defined."
            if chosen.native_only
            else "Uses the smallest compatible materialised population at the requested grain."
            if chosen.exact
            else "Uses a compatible coarser population whose configured aggregates can be combined safely."
            if chosen.level_id != self.base_id
            else "Uses Base to preserve the selected dimension_members, measures and aggregation policies."
        )
        return chosen
