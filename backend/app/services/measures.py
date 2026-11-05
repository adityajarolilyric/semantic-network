from ..core.errors import MetadataError
from ..domain.measure_policies import normalize_measure
from ..domain.typed_values import result_type as referenced_result_type, NUMERIC
from ..domain.formula_types import infer, attribute_physical_type
from .objects import ObjectService


class MeasureService(ObjectService):
    kind = "measure"

    async def get(self, model_id, revision_id, object_id):
        return normalize_measure(await super().get(model_id, revision_id, object_id))

    async def list(self, model_id, revision_id, cursor, limit):
        result = await super().list(model_id, revision_id, cursor, limit)
        return {
            **result,
            "items": [normalize_measure(document) for document in result["items"]],
        }

    async def formula_attributes(self, model_id, revision_id, level_id):
        from ..domain.formula_attributes import formula_attributes

        async def read(repo):
            objects = await self.objects(repo, model_id, revision_id)
            return formula_attributes(
                await self.effective(repo, model_id, revision_id, objects), level_id
            )

        return await self.store.transaction(read)

    async def validate_formula(self, model_id, revision_id, payload):
        from ..formula_language.service import FormulaLanguage

        async def read(repo):
            objects = await self.objects(repo, model_id, revision_id)
            effective = await self.effective(repo, model_id, revision_id, objects)
            from ..repositories.metadata import metadata_repositories
            from ..repositories.metadata.criteria import RevisionLookup

            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
            )
            return FormulaLanguage.validate(
                payload, effective, revision["basePlanningLevelId"]
            )

        return await self.store.transaction(read)

    def prepare_document(self, document, objects):
        definition = document["definition"]
        if definition["measureType"] != "referenced":
            from ..validation.graph import SemanticGraphValidator

            formula = definition["formula"]
            if formula.get("languageVersion"):
                from ..formula_language.service import FormulaLanguage

                formula["expression"] = FormulaLanguage.resolve(
                    formula["displayText"],
                    [
                        *[o for o in objects if o["objectId"] != document["objectId"]],
                        document,
                    ],
                    definition["nativePlanningLevelId"],
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
            return normalize_measure(document)
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
            from ..engine.calendar_resolution import CalendarResolutionPlanner

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
                return {
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
        # Let graph validation report missing/non-numeric references.
        return document
