from hashlib import sha256

from pydantic import ValidationError

from ..core.errors import Conflict, MetadataError
from ..schemas.sources import SourceInput, SourceRegistration


class SourceRegistrationService:
    """Capture warehouse schema without accepting client-supplied physical types."""

    def __init__(self, sources, warehouse):
        self.sources = sources
        self.warehouse = warehouse

    async def register(self, model_id, revision_id, payload: SourceRegistration):
        preview = await self.warehouse.preview(payload.tableName, 0, 1)
        names = {column.name for column in preview.columns}
        if not set(payload.keyColumnNames) <= names:
            raise MetadataError(
                "invalid_keys", "Choose keys from the current warehouse columns."
            )
        # Stable IDs are independent of physical names, including quoted CSV headers.
        columns = []
        keys = []
        for index, column in enumerate(preview.columns, 1):
            if column.name != column.name.strip():
                raise MetadataError(
                    "unsupported_schema",
                    "Column names with outer whitespace cannot be registered.",
                )
            nullable = column.type.startswith(("Nullable(", "LowCardinality(Nullable("))
            physical_type = column.type
            if column.type.startswith("Nullable("):
                physical_type = column.type[len("Nullable(") : -1]
            elif column.type.startswith("LowCardinality(Nullable("):
                physical_type = (
                    "LowCardinality("
                    + column.type[len("LowCardinality(Nullable(") : -2]
                    + ")"
                )
            column_id = f"col_{index}"
            if column.name in payload.keyColumnNames:
                if nullable:
                    raise Conflict(
                        "Key columns cannot be nullable. Choose a non-null key column."
                    )
                keys.append(column_id)
            columns.append(
                {
                    "columnId": column_id,
                    "name": column.name,
                    "physicalType": physical_type,
                    "nullable": nullable,
                }
            )
        try:
            source = SourceInput(
                objectId=payload.objectId
                or "source_" + sha256(payload.tableName.encode()).hexdigest()[:24],
                name=payload.tableName,
                description=payload.description,
                definition={
                    "tableName": payload.tableName,
                    "inScope": payload.inScope,
                    "keyColumns": keys,
                    "columns": columns,
                },
            )
        except ValidationError as error:
            raise MetadataError(
                "unsupported_schema",
                "The warehouse schema exceeds the supported semantic column or name limits.",
            ) from error
        return await self.sources.create(model_id, revision_id, source)
