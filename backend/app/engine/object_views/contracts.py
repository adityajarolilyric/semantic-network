from dataclasses import dataclass
from copy import deepcopy
from ..sql.contracts import ExecutionPlan, digest


def definition_hash(document):
    authored = deepcopy(
        {
            k: v
            for k, v in document.items()
            if k
            not in {
                "version",
                "createdAt",
                "updatedAt",
                "modelId",
                "revisionId",
                "_id",
            }
        }
    )
    if authored["kind"] == "transaction":
        authored["definition"].pop("nativeGrainId", None)
        if not authored["definition"].get("dimensionId"):
            authored["definition"]["keyMappings"] = []
    return digest(authored)


@dataclass(frozen=True)
class ObjectViewPlan:
    execution: ExecutionPlan
    columns: list
    mappings: list
    member_columns: list
