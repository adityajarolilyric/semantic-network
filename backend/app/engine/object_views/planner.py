"""Compile proposed object definitions using the existing engine operators."""

from copy import deepcopy
from dataclasses import replace
from sqlglot import parse_one
from ...domain.typed_values import NUMERIC, result_type
from ...warehouse.sql import quote_identifier as q
from ...schemas.transactions import TransactionInput
from ..populations.virtual import virtual_population
from ..sql.contracts import Check, ExecutionPlan
from .contracts import ObjectViewPlan
from .compilation import direct_identity_inputs


class ObjectViewPlanner:
    def __init__(self, populations, resolution):
        self.populations, self.resolution = populations, resolution

    async def compile(self, model, revision, document):
        if document["kind"] == "transaction":
            payload = TransactionInput.model_validate(
                {
                    k: v
                    for k, v in document.items()
                    if k
                    not in {
                        "version",
                        "createdAt",
                        "updatedAt",
                        "modelId",
                        "revisionId",
                        "_id",
                    }
                }
            )
            compiled, columns, mappings = await self.resolution.compile_view(
                model, revision, payload
            )
            definition = document["definition"]
            pivot = definition.get("pivot")
            if not definition.get("dimensionId") and not (
                (pivot and pivot.get("enabled", True))
                or definition.get("calendarResolution")
                or definition.get("filters")
            ):
                replacements = [
                    f"accurateCastOrNull({q(c['columnId'])}, 'Float64') AS {q(c['columnId'])}"
                    for c in columns
                    if c["columnId"] not in definition["grainColumnIds"]
                    and (result_type(c["physicalType"]) or {}).get("type") in NUMERIC
                ]
                if replacements:
                    compiled = replace(
                        compiled,
                        query=parse_one(
                            "SELECT * REPLACE ("
                            + ", ".join(replacements)
                            + ") FROM ("
                            + compiled.sql
                            + ")",
                            read="clickhouse",
                        ),
                    )
            if not document["definition"].get("dimensionId"):
                mappings = [
                    {"sourceColumnId": key, "grainColumnId": key}
                    for key in document["definition"]["grainColumnIds"]
                ]
            return ObjectViewPlan(
                compiled,
                [{**c, "storageName": c["columnId"]} for c in columns],
                mappings,
                [],
            )
        builds = self.populations

        async def capture(repo):
            objects = await builds.graph_objects(repo, model, revision)
            objects = [o for o in objects if o["objectId"] != document["objectId"]] + [
                document
            ]
            objects = await builds.effective(repo, model, revision, objects)
            # Include catalog columns physically while respecting exposure in previews.
            level = deepcopy(document)
            catalog = level.get("memberCatalog")
            if catalog:
                for binding in level["definition"]["memberBindings"]:
                    if binding["memberCatalogId"] == level["memberCatalogId"]:
                        binding["selectedAttributeColumnIds"] = [
                            c["columnId"]
                            for c in catalog["columns"]
                            if c["columnId"] not in catalog["keyColumnIds"]
                        ]
            inputs = await builds.capture_population_inputs(
                repo, model, revision, level, objects, virtual=True
            )
            inputs["relations"].pop(document["objectId"], None)
            inputs["relations"].pop(document.get("memberCatalogId"), None)
            public = builds.compiler.compile(
                builds.planner.plan(document, objects, inputs["relations"])
            )
            compiled = builds.compiler.compile(
                builds.planner.plan(level, objects, inputs["relations"])
            )
            compiled = direct_identity_inputs(
                compiled, objects, inputs["relations"], inputs["managed"]
            )
            checks = virtual_population(level, compiled, inputs["inputHash"])["checks"]
            compiled = ExecutionPlan(
                compiled.query,
                tuple(Check(c["code"], c["sql"]) for c in checks),
                compiled.schema,
            )
            members = []
            if catalog:
                for c in catalog["columns"]:
                    column = next(
                        (
                            s
                            for s in compiled.schema.columns
                            if s["columnId"] == c["columnId"]
                            or s.get("lineage", {}).get("memberCatalogId")
                            == level["memberCatalogId"]
                            and s.get("lineage", {}).get("columnId") == c["columnId"]
                        ),
                        None,
                    )
                    if column:
                        members.append(
                            {
                                **column,
                                "columnId": c["columnId"],
                                "sourceColumnId": c["sourceColumnId"],
                                "storageName": column["name"],
                            }
                        )
            labels = {
                c["columnId"]: c["displayName"]
                for c in (catalog or {}).get("columns", [])
            }
            visible = []
            for column in public.schema.columns:
                member_id = (
                    column["columnId"]
                    if column["columnId"] in labels
                    else (
                        column.get("lineage", {}).get("columnId")
                        if column.get("lineage", {}).get("memberCatalogId")
                        == document.get("memberCatalogId")
                        else None
                    )
                )
                visible.append(
                    {
                        **column,
                        "storageName": column["name"],
                        "name": labels.get(
                            member_id, column.get("displayName") or column["name"]
                        ),
                    }
                )
            return ObjectViewPlan(compiled, visible, [], members)

        return await builds.store.transaction(capture)
