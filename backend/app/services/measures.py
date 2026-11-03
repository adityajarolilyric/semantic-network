from .measure_types import normalize_measure, referenced_result_type
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

    def prepare_document(self, document, objects):
        definition = document["definition"]
        if definition["measureType"] != "referenced":
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
        if source and transaction["definition"].get("calendarResolution"):
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
                    "definition": {**definition, "resultType": result_type},
                    "business": {
                        **document["business"],
                        "unit": document["business"]["unit"] or column.get("unit"),
                    },
                }
        # Let graph validation report missing/non-numeric references.
        return document
