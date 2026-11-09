from ..core.errors import MetadataError
from ..domain.formula_attributes import formula_attributes
from ..domain.formula_types import attribute_physical_type, infer
from ..domain.grains import canonical_measure, resolve_grain
from ..domain.measure_policies import normalize_measure
from ..domain.typed_values import NUMERIC
from ..domain.typed_values import result_type as referenced_result_type
from ..engine.transformations.dates.calendar import CalendarResolutionPlanner
from ..engine.graph.compatibility import MeasureCompatibilityAnalyzer
from ..engine.measures.dependencies import MeasureDependencies
from ..engine.graph.grain_contexts import resolve_grain_contexts
from ..formula_language.service import FormulaLanguage
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import RevisionLookup
from ..validation.graph import SemanticGraphValidator
from .objects import ObjectService


class MeasureService(ObjectService):
    kind = "measure"

    async def get(self, model_id, revision_id, object_id):
        return canonical_measure(
            normalize_measure(await super().get(model_id, revision_id, object_id))
        )

    async def list(self, model_id, revision_id, cursor, limit):
        result = await super().list(model_id, revision_id, cursor, limit)
        return {
            **result,
            "items": [
                canonical_measure(normalize_measure(document))
                for document in result["items"]
            ],
        }

    async def formula_attributes(self, model_id, revision_id, level_id):
        async def read(repo):
            objects = await self.graph_objects(repo, model_id, revision_id)
            return formula_attributes(
                await self.effective(repo, model_id, revision_id, objects), level_id
            )

        return await self.store.transaction(read)

    async def formula_measures(self, model_id, revision_id, grain_id, owner_id=None):
        async def read(repo):
            objects = await self.graph_objects(repo, model_id, revision_id)
            by_id = {obj["objectId"]: obj for obj in objects}
            if grain_id not in by_id or by_id[grain_id]["kind"] not in {
                "dimension",
                "grain",
            }:
                raise MetadataError(
                    "invalid_formula_level", "Choose an existing Output Dimension."
                )
            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
            )
            analyzer = MeasureCompatibilityAnalyzer(
                objects, revision["baseDimensionId"]
            )
            dependencies = MeasureDependencies(objects)
            inputs = []
            for mid, measure in dependencies.measures.items():
                outcome = analyzer.outcome(mid, grain_id)
                cycle = dependencies.path(mid, owner_id) if owner_id else ()
                reason = outcome.reason
                if cycle:
                    names = [
                        by_id.get(owner_id, {}).get("name", "This measure"),
                        *(dependencies.name(node) for node in cycle),
                    ]
                    reason = "Would create a dependency cycle: " + " → ".join(names)
                inputs.append(
                    {
                        "measureId": mid,
                        "available": bool(outcome.available and not cycle),
                        "reason": reason,
                    }
                )
            return inputs

        return await self.store.transaction(read)

    async def validate_formula(self, model_id, revision_id, payload):
        async def read(repo):
            objects = await self.graph_objects(repo, model_id, revision_id)
            effective = await self.effective(repo, model_id, revision_id, objects)

            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
            )
            validation_input = payload
            if payload.outputGrain:
                validation_input = payload.model_copy(
                    update={
                        "nativeGrainId": resolve_grain(
                            payload.outputGrain.model_dump(), effective
                        )
                    }
                )
            return FormulaLanguage.validate(
                validation_input, effective, revision["baseDimensionId"]
            )

        return await self.store.transaction(read)

    def prepare_document(self, document, objects):
        document = canonical_measure(document)
        context_documents = resolve_grain_contexts(
            [
                *[obj for obj in objects if obj["objectId"] != document["objectId"]],
                document,
            ]
        )
        runtime = next(
            obj for obj in context_documents if obj["objectId"] == document["objectId"]
        )
        definition = runtime["definition"]
        if definition["measureType"] != "referenced":
            document = runtime
            formula = definition["formula"]
            if formula.get("languageVersion"):
                formula["expression"] = FormulaLanguage.resolve(
                    formula["displayText"],
                    [
                        *[o for o in objects if o["objectId"] != document["objectId"]],
                        document,
                    ],
                    definition["nativeGrainId"],
                )
                formula["astVersion"] = 2
            context = SemanticGraphValidator(objects)
            previous_type = definition["resultType"]
            try:
                definition["resultType"] = {
                    "type": infer(
                        definition["formula"]["expression"],
                        lambda mid: context.objects[mid]["definition"]["resultType"][
                            "type"
                        ]
                        if mid in context.objects
                        and context.objects[mid]["kind"] == "measure"
                        else "number",
                        lambda node: attribute_physical_type(context, document, node),
                    ),
                    "nullable": definition["resultType"]["nullable"],
                }
            except MetadataError as exc:
                raise MetadataError(
                    "invalid_semantics",
                    "Metadata violates semantic contracts.",
                    details=[
                        {
                            "objectId": document["objectId"],
                            "code": exc.code,
                            "message": exc.message,
                        }
                    ],
                ) from exc

            if (
                definition["resultType"]["type"] == "number"
                and previous_type["type"] == "decimal"
            ):
                definition["resultType"] = previous_type
            if definition["resultType"]["type"] not in NUMERIC:
                definition["allocation"] = None
                document = {
                    **document,
                    "business": {**document["business"], "unit": None},
                }
            if formula.get("languageVersion"):
                document = {
                    **document,
                    "business": {
                        **document["business"],
                        "unit": context.expression_unit(
                            document, formula["expression"]
                        ),
                    },
                }
            return canonical_measure(normalize_measure(document))
        if definition.get("conversion"):
            document = {**document, "business": {**document["business"], "unit": definition["conversion"]["targetUnit"]}}
        transaction = next(
            (
                o
                for o in objects
                if o["objectId"] == definition["transactionId"]
                and o["kind"] == "transaction"
            ),
            None,
        )
        source = next(
            (
                o
                for o in objects
                if transaction
                and o["objectId"] == transaction["definition"]["sourceId"]
                and o["kind"] == "source"
            ),
            None,
        )
        columns = source["definition"]["columns"] if source else []
        if source and (
            transaction["definition"].get("calendarResolution")
            or transaction["definition"].get("pivot")
        ):
            columns = CalendarResolutionPlanner(objects, transaction, columns).schema()
        column = (
            next(
                (c for c in columns if c["columnId"] == definition["valueColumnId"]),
                None,
            )
            if source
            else None
        )
        if column:
            nullable = (
                column["nullable"]
                or definition["missingTransactionValue"] == "null"
                or definition["aggregation"]["allNullResult"] == "null"
            )
            result_type = referenced_result_type(column["physicalType"], nullable)
            if result_type:
                return canonical_measure(
                    {
                        **document,
                        "definition": {
                            **definition,
                            "resultType": result_type,
                            **(
                                {"allocation": None, "editable": False}
                                if result_type["type"] not in NUMERIC
                                else {}
                            ),
                        },
                        "business": {
                            **document["business"],
                            "unit": (document["business"]["unit"] or column.get("unit"))
                            if result_type["type"] in NUMERIC
                            else None,
                        },
                    }
                )
        # Let graph validation report missing/non-numeric references.
        return document
