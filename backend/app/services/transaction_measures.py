"""Derive and refresh referenced measures for Transaction commands."""

from copy import deepcopy

from ..core.errors import MetadataError
from ..domain.transaction_measures import rebind_transaction_measures
from ..domain.typed_values import result_type as referenced_result_type
from ..engine.calendar_resolution import CalendarResolutionPlanner
from ..engine.contracts import digest
from ..schemas.measures import MeasureInput


class TransactionMeasureFactory:
    """App-scoped, metadata-only factory; no clients, writes or request state."""

    def __init__(self, measures):
        self.measures = measures

    def refresh_documents(self, previous, transaction, objects):
        """Prepare relocated references and their closed scalar calculation chains."""
        rebound = rebind_transaction_measures(previous, transaction, objects)
        replacements = {measure["objectId"]: measure for measure in rebound}
        context = [replacements.get(obj["objectId"], obj) for obj in objects]
        return [
            self.measures.prepare_document(
                {
                    **deepcopy(measure),
                    "version": measure["version"] + 1,
                    "updatedAt": transaction["updatedAt"],
                },
                context,
            )
            for measure in rebound
        ]

    def create_documents(self, transaction, selections, objects):
        if not selections:
            return []
        definition = transaction["definition"]
        source = next(
            (
                item
                for item in objects
                if item["objectId"] == definition["sourceId"]
                and item["kind"] == "source"
            ),
            None,
        )
        if source is None:
            raise MetadataError(
                "invalid_measure_source", "Choose an available transaction source."
            )
        columns = CalendarResolutionPlanner(
            objects, transaction, source["definition"]["columns"]
        ).schema()
        if definition.get("mappingMode") == "resolve_multi_grain":
            resolved_values = {
                value["sourceColumnId"] for value in definition["resolution"]["values"]
            }
            columns = [
                {
                    **column,
                    "physicalType": "Float64"
                    if column["columnId"] in resolved_values
                    else column["physicalType"],
                }
                for column in columns
                if column["columnId"] in resolved_values
                or column["columnId"]
                in definition["resolution"].get("passthroughColumnIds", [])
            ]
        eligible_columns = {
            column["columnId"]: column
            for column in columns
            if referenced_result_type(column["physicalType"], True)
        }
        documents = []
        for selection in selections:
            column = eligible_columns.get(selection.valueColumnId)
            if column is None:
                raise MetadataError(
                    "invalid_measure_column",
                    "Select a supported value column from the transformed transaction output.",
                )
            measure = MeasureInput.model_validate(
                {
                    "objectId": "measure_"
                    + digest(
                        [
                            "transaction-column",
                            transaction["objectId"],
                            selection.valueColumnId,
                        ]
                    )[:32],
                    "kind": "measure",
                    "name": column["name"],
                    "displayName": column["name"],
                    "business": {},
                    "definition": {
                        "measureType": "referenced",
                        "transactionId": transaction["objectId"],
                        "valueColumnId": selection.valueColumnId,
                        "editable": selection.editable,
                        "aggregation": selection.aggregation.model_dump(),
                        "allocation": selection.allocation.model_dump()
                        if selection.allocation
                        else None,
                        "missingTransactionValue": "null",
                        "sourceNullPolicy": "preserve",
                        "resultType": {"type": "number", "nullable": True},
                    },
                }
            )
            document = {
                **measure.model_dump(),
                **{
                    key: transaction[key]
                    for key in ["modelId", "revisionId", "createdAt", "updatedAt"]
                },
                "version": 1,
            }
            documents.append(self.measures.prepare_document(document, objects))
        return documents
