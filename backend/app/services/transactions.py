from ..core.errors import MetadataError
from ..engine.contracts import digest
from ..schemas.transactions import ResolutionRule
from .objects import ObjectService


class TransactionService(ObjectService):
    kind = "transaction"

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
                for key in ("sourceId", "planningLevelId")
            )
            else set()
        )
        if not requested <= allowed:
            raise MetadataError(
                "legacy_resolution_rule",
                "New multi-grain matching supports Column equals attribute only. Pivot the long attribute/value columns first, resolve dates if needed, then map the generated columns.",
            )
        return document
