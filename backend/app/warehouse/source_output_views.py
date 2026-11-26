"""Publish one stable source-output view and retire redundant owned views."""

from ..core.errors import Conflict
from ..core.metadata import utc_now
from ..engine.sql.storage import owner
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ArtifactLookup
from .source_view_retirement import SourceViewRetirement


class SourceOutputViews:
    def __init__(self, storage):
        self.storage = storage
        self.warehouse = storage.warehouse
        self.retirement = SourceViewRetirement(storage)

    async def publish(self, artifact):
        previous = artifact.get("previousOutput")
        if not previous:
            return
        candidate, live = artifact["physicalName"], previous["physicalName"]

        def inspect(connect):
            client = connect()
            if not self.storage.owns_view(client, live, owner(previous)):
                raise Conflict("The previous source-output view ownership changed.")
            return self.identities(client, [live, candidate])

        identities = await self.warehouse.run(inspect)
        artifact["outputPublication"] = {
            "liveName": live,
            "candidateName": candidate,
            "oldUuid": identities[live],
            "candidateUuid": identities[candidate],
        }
        await self.storage.save(artifact)
        await self.warehouse.run(
            lambda connect: connect().command(
                f"EXCHANGE TABLES {self.warehouse.qualify(live)} AND {self.warehouse.qualify(candidate)}"
            )
        )
        artifact.update(physicalName=live, viewName=live)
        await self.storage.save(artifact)

    def identities(self, client, names):
        return dict(
            client.query(
                "SELECT name,toString(uuid) FROM system.tables WHERE database={db:String} AND name IN {names:Array(String)}",
                parameters={
                    "db": self.warehouse.settings.clickhouse_database,
                    "names": names,
                },
            ).result_rows
        )

    async def rollback(self, artifact):
        publication = artifact.get("outputPublication")
        if not publication:
            return
        live, candidate = publication["liveName"], publication["candidateName"]

        def restore(connect):
            client = connect()
            actual = self.identities(client, [live, candidate])
            if actual.get(live) == publication["candidateUuid"]:
                if actual.get(candidate) != publication["oldUuid"]:
                    raise Conflict("The source-output rollback view changed.")
                client.command(
                    f"EXCHANGE TABLES {self.warehouse.qualify(live)} AND {self.warehouse.qualify(candidate)}"
                )
            elif actual.get(live) != publication["oldUuid"]:
                raise Conflict("The source-output view changed during rollback.")

        await self.warehouse.run(restore)
        artifact.update(physicalName=candidate, viewName=candidate)

    async def finish(self, artifact):
        previous = artifact.get("previousOutput")
        retired_input = artifact.get("retiredInputView")
        publication = artifact.get("outputPublication")

        def drop(connect):
            client = connect()
            obsolete = []
            if publication:
                obsolete.append((publication["candidateName"], owner(previous)))
            if retired_input:
                obsolete.append((retired_input["physicalName"], owner(retired_input)))
            for name, marker in obsolete:
                if self.warehouse.table_info(client, name):
                    if not self.storage.owns_view(client, name, marker):
                        raise Conflict(
                            "Refusing to retire a source view whose ownership changed."
                        )
                    client.command(f"DROP VIEW {self.warehouse.qualify(name)}")

        await self.warehouse.run(drop)
        if previous:
            repositories = metadata_repositories(self.storage.store.repository)
            old = await repositories.artifacts.require(
                ArtifactLookup(artifact_id=previous["artifactId"]),
                "Previous source output",
            )
            await repositories.artifacts.replace(
                ArtifactLookup(artifact_id=old["artifactId"]),
                {
                    **old,
                    "state": "dropped",
                    "droppedAt": utc_now(),
                    "cleanupPending": False,
                },
            )
        await self.retirement.finish(artifact)
