from ..core.errors import MetadataError
from ..engine.contracts import digest
from ..schemas.transactions import ResolutionRule
from .objects import ObjectService


class TransactionService(ObjectService):
    kind = "transaction"

    def __init__(self, store, max_objects=5000, measure_factory=None):
        super().__init__(store, max_objects)
        self.measure_factory = measure_factory

    def create_input(self, payload):
        return payload.model_dump(exclude={"measures"})

    def additional_create_documents(self, payload, document, objects):
        selections = getattr(payload, "measures", [])
        if not selections:
            return []
        return self.measure_factory.create_documents(document, selections, objects)

    def related_update_documents(self, previous, updated, objects):
        if (
            previous["definition"]["dimensionId"]
            == updated["definition"]["dimensionId"]
        ):
            return []
        return self.measure_factory.refresh_documents(previous, updated, objects)

    def prepare_document(self, document, objects):
        """New authoring uses wide columns; unchanged saved selector rules remain readable."""

        def legacy(definition):
            return {
                digest(
                    [
                        axis["grainColumnId"],
                        ResolutionRule.model_validate(rule).model_dump(),
                    ]
                )
                for axis in (definition.get("resolution") or {}).get("axes", [])
                for rule in axis["rules"]
                if rule.get("kind") == "level_value"
            }

        definition = document["definition"]
        if (
            definition.get("dimensionId")
            and definition.get("mappingMode", "direct") == "direct"
        ):
            mapped = [
                mapping["sourceColumnId"] for mapping in definition["keyMappings"]
            ]
            if definition.get("grainColumnIds") and set(
                definition["grainColumnIds"]
            ) != set(mapped):
                raise MetadataError(
                    "transaction_grain_binding",
                    "Direct Dimension binding must map every declared grain key. Change the grain explicitly if needed.",
                )
            definition["grainColumnIds"] = mapped
        requested = legacy(definition)
        if not requested:
            return document
        old = next(
            (
                o
                for o in objects
                if o["objectId"] == document["objectId"] and o["kind"] == "transaction"
            ),
            None,
        )
        allowed = (
            legacy(old["definition"])
            if old
            and all(
                old["definition"][key] == definition[key]
                for key in ("sourceId", "dimensionId")
            )
            else set()
        )
        if not requested <= allowed:
            raise MetadataError(
                "legacy_resolution_rule",
                "New multi-grain matching supports Column equals attribute only. Pivot the long attribute/value columns first, resolve dates if needed, then map the generated columns.",
            )
        return document
