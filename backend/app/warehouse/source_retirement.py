"""Retire an obsolete engine-owned copy after an independent source is published."""

from ..core.errors import Conflict
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import TableResourceLookup


class SourceCopyRetirement:
    def __init__(self, storage):
        self.storage = storage
        self.warehouse = storage.warehouse

    async def finish(self, artifact):
        previous = artifact.get("previousMaterialisation")
        if not previous:
            return
        name, marker = previous["physicalName"], previous["storageOwner"]
        if name == artifact["sourceTableName"] or not marker.startswith(
            "semanticCurrent:"
        ):
            raise Conflict(
                "An imported source cannot be retired as an engine-owned copy."
            )

        def drop(connect):
            client = connect()
            if previous.get("viewName"):
                view = self.warehouse.table_info(client, previous["viewName"])
                if view:
                    if view[0] != "View" or view[1] != marker + ":view":
                        raise Conflict(
                            "The obsolete materialised view ownership changed."
                        )
                    client.command(
                        f"DROP VIEW {self.warehouse.qualify(previous['viewName'])}"
                    )
            table = self.warehouse.table_info(client, name)
            if table:
                if table[0] != "MergeTree" or table[1] != marker:
                    raise Conflict("The obsolete materialised table ownership changed.")
                client.command(f"DROP TABLE {self.warehouse.qualify(name)} SYNC")

        await self.warehouse.run(drop)
        await metadata_repositories(
            self.storage.store.repository
        ).table_resources.delete(
            TableResourceLookup(
                model_id=artifact["modelId"],
                revision_id=artifact["revisionId"],
                resource_kind="dimension",
                resource_id=artifact["resourceId"],
                physical_name=name,
            )
        )
