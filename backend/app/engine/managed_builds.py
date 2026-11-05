"""Managed-view build lifecycle, dispatched by the generic engine worker."""

from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    ManagedTableLookup,
    ObjectLookup,
    RevisionLookup,
    RunInputLookup,
)

from ..core.errors import Conflict
from ..core.metadata import revision_scope as scope
from ..core.metadata import utc_now as now
from ..schemas.managed_tables import ManagedDefinition
from .contracts import digest
from .resolver import EffectiveSchemaResolver


class ManagedTableBuildHandler:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    async def execute_run(self, run, worker):
        service = self.service
        inputs = await metadata_repositories(self.store.repository).inputs.list(
            RunInputLookup(run_id=run["runId"]),
            order_by="inputId",
            limit=service.max_objects,
        )
        objects = [i["document"] for i in inputs]
        source = next(o for o in objects if o["objectId"] == run["sourceId"])
        plan = service.planner.plan(
            source,
            ManagedDefinition.model_validate(
                {k: v for k, v in run["definition"].items() if k != "columns"}
            ),
        )
        compiled = service.compiler.compile(plan)
        await worker.step(
            run,
            "compile",
            "succeeded",
            sql=compiled.sql,
            planHash=digest(compiled.sql),
            compilerVersion=compiled.compiler_version,
        )
        fingerprint = await service.executor.inspect(source)
        await worker.step(run, "checks", "running")
        await service.executor.checks(compiled)
        await worker.step(run, "checks", "succeeded")
        artifact = await metadata_repositories(self.store.repository).artifacts.require(
            ArtifactLookup(artifact_id=run["artifactId"]), "Artifact"
        )
        await worker.step(run, "create_view", "running")
        await service.executor.create(artifact, compiled)
        await worker.step(run, "create_view", "succeeded")
        # Repeat checks after DDL and schema inspection, before entering the activation transaction.
        if fingerprint != await service.executor.inspect(source):
            raise Conflict("The raw schema changed during execution.")
        await service.executor.checks(compiled)
        await self.activate(
            run, artifact, fingerprint, compiled.compiler_version, worker
        )

    async def activate(self, run, artifact, fingerprint, compiler_version, worker):
        service = self.service

        async def operation(repo):
            current_run = await worker.fenced(repo, run)
            query = scope(run["modelId"], run["revisionId"])
            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=run["modelId"], revision_id=run["revisionId"]),
                "Revision",
            )
            service.check_version(revision, run["revisionVersion"])
            if not run["repair"] and revision["approvalStatus"] != "draft":
                raise Conflict("The revision was published while this build ran.")
            source = await service.source(
                repo, run["modelId"], run["revisionId"], run["sourceId"]
            )
            identity = {**query, "sourceId": run["sourceId"]}
            old = await metadata_repositories(repo).managed_tables.find(
                ManagedTableLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    source_id=run["sourceId"],
                )
            )
            if (old["version"] if old else 0) != run["expectedVersion"]:
                raise Conflict()
            document = {
                **identity,
                "definition": run["definition"],
                "definitionHash": run["definitionHash"],
                "schemaVersion": 1,
                "version": run["expectedVersion"] + 1,
                "createdAt": old["createdAt"] if old else now(),
                "updatedAt": now(),
            }
            if run["repair"]:
                if old is None:
                    await metadata_repositories(repo).managed_tables.insert(document)
                else:
                    document = old
            else:
                objects = await service.objects(repo, run["modelId"], run["revisionId"])
                managed = await metadata_repositories(repo).managed_tables.list(
                    ManagedTableLookup(
                        model_id=run["modelId"], revision_id=run["revisionId"]
                    ),
                    order_by="sourceId",
                    limit=service.max_objects,
                )
                effective = EffectiveSchemaResolver.apply(
                    objects,
                    [d for d in managed if d["sourceId"] != run["sourceId"]]
                    + [document],
                )
                for obj in effective:
                    if obj["objectId"] == run["sourceId"]:
                        obj["definition"]["inScope"] = True
                service.validate(effective, revision["basePlanningLevelId"])
                if old:
                    await metadata_repositories(repo).managed_tables.replace(
                        ManagedTableLookup(
                            model_id=run["modelId"],
                            revision_id=run["revisionId"],
                            source_id=run["sourceId"],
                            version=old["version"],
                        ),
                        document,
                    )
                else:
                    await metadata_repositories(repo).managed_tables.insert(document)
                source = {
                    **source,
                    "definition": {**source["definition"], "inScope": True},
                    "version": source["version"] + 1,
                    "updatedAt": now(),
                }
                await metadata_repositories(repo).objects.replace(
                    ObjectLookup(
                        model_id=run["modelId"],
                        revision_id=run["revisionId"],
                        object_id=source["objectId"],
                    ),
                    source,
                )
                await service.touch_revision(repo, run["modelId"], run["revisionId"])
            binding = {
                **identity,
                "resourceKind": "managedTable",
                "resourceId": run["sourceId"],
                "artifactId": artifact["artifactId"],
                "definitionHash": run["definitionHash"],
            }
            await metadata_repositories(repo).activation.bind(identity, binding)
            await metadata_repositories(repo).artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {
                    **artifact,
                    "state": "ready",
                    "sourceFingerprint": fingerprint,
                    "compilerVersion": compiler_version,
                },
            )
            await metadata_repositories(repo).activation.complete(run, current_run)

        await self.store.transaction(operation)
