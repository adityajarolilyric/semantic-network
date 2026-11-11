"""A bounded compatibility graph from saved definitions and coherent build metadata."""

from collections import OrderedDict

from ..core.errors import MetadataError
from ..engine.graph.compatibility import MeasureCompatibilityAnalyzer
from ..engine.sql.contracts import digest
from ..engine.decision.relationships import TARGET, ViewRelationships
from ..engine.decision.planner import DecisionViewPlanner
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import BindingLookup, RevisionLookup
from ..schemas.decision_view import DecisionViewRequest
from .base import Service


class CompatibilityService(Service):
    def __init__(self, store, settings, views):
        super().__init__(store, settings.max_objects_per_revision)
        self.views = views
        self.analyzers = OrderedDict()
        self.snapshots = OrderedDict()

    def analyzer(self, objects, base):
        key = digest([base, objects])
        if key not in self.analyzers:
            self.analyzers[key] = MeasureCompatibilityAnalyzer(objects, base)
            if len(self.analyzers) > 4:
                self.analyzers.popitem(last=False)
        self.analyzers.move_to_end(key)
        return self.analyzers[key]

    async def graph(self, model, revision, request):
        async def capture(repo):
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            bindings = await metadata_repositories(repo).bindings.list(
                BindingLookup(model_id=model, revision_id=revision),
                order_by="resourceId",
                limit=self.max_objects * 3,
            )
            bindings.sort(key=lambda item: (item["resourceKind"], item["resourceId"]))
            snapshot_key = digest([model, revision, rev["version"], bindings])
            if snapshot_key in self.snapshots:
                self.snapshots.move_to_end(snapshot_key)
                return self.snapshots[snapshot_key]
            populations, values = {}, {}
            build_reason = "Build planning populations and the Semantic Network to make these measures ready."
            try:
                (
                    _,
                    raw,
                    populations,
                    _,
                    values,
                    _,
                    _,
                ) = await self.views.current_snapshot(repo, model, revision)
            except MetadataError as error:
                build_reason = error.message
                raw = await self.graph_objects(repo, model, revision)
            objects = await self.effective(repo, model, revision, raw)
            result = rev, objects, populations, values, build_reason
            self.snapshots[snapshot_key] = result
            if len(self.snapshots) > 4:
                self.snapshots.popitem(last=False)
            return result

        rev, objects, populations, values, build_reason = await self.store.transaction(
            capture
        )
        by_id = {obj["objectId"]: obj for obj in objects}
        measures = sorted(
            (obj for obj in objects if obj["kind"] == "measure"),
            key=lambda obj: (obj["name"].casefold(), obj["objectId"]),
        )
        levels = sorted(
            (obj for obj in objects if obj["kind"] in {"dimension", "grain"}),
            key=lambda obj: (obj["name"].casefold(), obj["objectId"]),
        )
        for mid in request.measures:
            if mid not in by_id or by_id[mid]["kind"] != "measure":
                raise MetadataError(
                    "invalid_view_measure", "A selected measure no longer exists."
                )
        if request.dimensionId and (
            request.dimensionId not in by_id
            or by_id[request.dimensionId]["kind"] not in {"dimension", "grain"}
        ):
            raise MetadataError(
                "invalid_formula_level", "The selected dimension no longer exists."
            )
        analyzer = self.analyzer(objects, rev["baseDimensionId"])
        contexts = []
        if request.view:
            fields = DecisionViewPlanner(objects, rev["baseDimensionId"]).fields(
                request.view
            )
            relationships = ViewRelationships(objects, fields)
            analyzer = MeasureCompatibilityAnalyzer(
                objects, rev["baseDimensionId"], relationships
            )
            contexts.append(
                {
                    "id": TARGET,
                    "name": "Selected pivot dimension_members",
                    "rows": request.view.rows,
                    "columns": request.view.columns,
                }
            )
        else:
            scoped = (
                [by_id[request.dimensionId]]
                if request.dimensionId
                else levels[
                    request.levelOffset : request.levelOffset + request.levelLimit
                ]
            )
            for level in scoped:
                if len(level["definition"]["grain"]["keyIds"]) > 8:
                    # The planner has an explicit eight-field pivot limit.
                    fields = None
                else:
                    fields = [
                        (
                            {
                                "transactionId": level["owner"]["objectId"],
                                "columnId": key,
                            }
                            if level["kind"] == "grain"
                            else {"dimensionId": level["objectId"], "columnId": key}
                        )
                        for key in level["definition"]["grain"]["keyIds"]
                    ]
                contexts.append(
                    {
                        "id": level["objectId"],
                        "name": level["name"],
                        "rows": fields,
                        "columns": [],
                    }
                )
        result_cache = {}

        def evaluate(selected, context):
            key = (tuple(sorted(selected)), context["id"])
            if key in result_cache:
                return result_cache[key]
            result = {
                "status": "unavailable",
                "reason": "Select at least one measure.",
                "operation": "",
            }
            if not selected:
                return result
            for mid in selected:
                availability = analyzer.outcome(mid, context["id"])
                if not availability.available:
                    result["reason"] = availability.reason
                    result_cache[key] = result
                    return result
            if context["rows"] is None:
                result["reason"] = (
                    "This level exceeds the eight-field pivot limit; choose fewer dimension_members in Planning View."
                )
            elif not values:
                planner = DecisionViewPlanner(objects, rev["baseDimensionId"])
                payload = DecisionViewRequest(
                    rows=context["rows"],
                    columns=context["columns"],
                    measures=list(selected),
                )
                try:
                    planner.common_sources(payload, planner.fields(payload))
                    result.update(status="needs_materialisation", reason=build_reason)
                except MetadataError as error:
                    result.update(status="unavailable", reason=error.message)
            else:
                payload = DecisionViewRequest(
                    rows=context["rows"],
                    columns=context["columns"],
                    measures=list(selected),
                )
                try:
                    plan = DecisionViewPlanner(objects, rev["baseDimensionId"]).plan(
                        payload, populations, values
                    )
                    result.update(
                        status="ready",
                        reason="The complete selection can be plotted together.",
                        sourceDimensionId=plan.level_id,
                        sourceDimensionName=by_id[plan.level_id]["name"],
                    )
                except MetadataError as error:
                    result.update(
                        status="needs_materialisation"
                        if error.code == "decision_view_build_required"
                        else "unavailable",
                        reason=error.message,
                    )
            if len(selected) == 1:
                result["operation"] = analyzer.outcome(
                    selected[0], context["id"]
                ).operation
            result_cache[key] = result
            return result

        filtered = [
            measure
            for measure in measures
            if request.search.strip().casefold() in measure["name"].casefold()
        ]
        visible = filtered[
            request.measureOffset : request.measureOffset + request.measureLimit
        ]
        items = []
        for measure in visible:
            mid = measure["objectId"]
            candidate = list(dict.fromkeys([*request.measures, mid]))
            connections = []
            for context in contexts:
                verdict = (
                    evaluate(candidate, context)
                    if len(candidate) <= 12
                    else {
                        "status": "unavailable",
                        "reason": "Planning View supports at most twelve selected measures.",
                        "operation": "",
                    }
                )
                connections.append({"dimensionId": context["id"], **verdict})
            items.append(
                {
                    "measureId": mid,
                    "name": measure["name"],
                    "nativeGrainId": measure["definition"]["nativeGrainId"],
                    "nativeDimensionName": by_id.get(
                        measure["definition"]["nativeGrainId"], {}
                    ).get("name", "Unavailable level"),
                    "connections": connections,
                }
            )
        return {
            "revisionVersion": rev["version"],
            "generationId": next(iter(values.values()), {}).get("generationId"),
            "measures": items,
            "totalMeasures": len(filtered),
            "levels": [
                {"dimensionId": level["objectId"], "name": level["name"]}
                for level in levels
            ],
            "contexts": [
                {
                    "dimensionId": context["id"],
                    "name": context["name"],
                    "selection": evaluate(request.measures, context)
                    if request.measures
                    else None,
                }
                for context in contexts
            ],
            "selection": [
                {"measureId": mid, "name": by_id[mid]["name"]}
                for mid in request.measures
            ],
            "measureOffset": request.measureOffset,
            "levelOffset": request.levelOffset,
            "hasMoreMeasures": request.measureOffset + request.measureLimit
            < len(filtered),
            "hasMoreLevels": not request.dimensionId
            and not request.view
            and request.levelOffset + request.levelLimit < len(levels),
        }
