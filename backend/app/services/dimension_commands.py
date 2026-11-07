"""Dimension authoring commands that also request durable population builds."""

from ..core.errors import Conflict, MetadataError
from ..engine.contracts import digest
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ObjectLookup, RevisionLookup, RunLookup
from .base import Service


class DimensionCommandService(Service):
    def __init__(self, store, max_objects, dimensions, network):
        super().__init__(store, max_objects)
        self.dimensions, self.network = dimensions, network

    async def save_and_build(self, model, revision, command, key):
        if not key or len(key) > 128:
            raise MetadataError(
                "invalid_idempotency_key",
                "Supply an Idempotency-Key of 1 to 128 characters.",
            )
        payload = command.definition
        if payload.definition.population.operation == "source":
            raise MetadataError(
                "source_dimension_save",
                "Save source-backed Dimension metadata without a population build.",
            )
        command_hash = digest(["saveDerivedDimension", command.model_dump()])
        repositories = metadata_repositories(self.store.repository)
        previous = await repositories.runs.find(
            RunLookup(model_id=model, revision_id=revision, idempotency_key=key)
        )
        if previous:
            if previous["requestHash"] != command_hash:
                raise Conflict("This idempotency key belongs to another request.")
            return self.network.managed.public_run(previous)
        current = await repositories.objects.find(
            ObjectLookup(
                model_id=model, revision_id=revision, object_id=payload.objectId
            )
        )
        if current and current["kind"] != "dimension":
            raise Conflict("This identity is already used by another semantic object.")
        unchanged = current and all(
            current.get(k) == v for k, v in payload.model_dump().items()
        )
        if unchanged and command.expectedVersion in {
            None,
            current["version"],
            current["version"] - 1,
        }:
            # A previous request can have saved metadata before submission failed.
            # Reuse that exact definition; never overwrite a concurrent change.
            pass
        elif command.expectedVersion is None:
            await self.dimensions.create(model, revision, payload)
        else:
            await self.dimensions.update(
                model, revision, payload.objectId, payload, command.expectedVersion
            )
        rev = await repositories.revisions.require(
            RevisionLookup(model_id=model, revision_id=revision), "Revision"
        )
        return await self.network.submit(
            model,
            revision,
            rev["version"],
            key,
            population_dimension_id=payload.objectId,
            command_hash=command_hash,
        )
