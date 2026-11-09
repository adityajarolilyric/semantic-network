from ...core.errors import MetadataError
from ...domain.dimensions import dimension_columns
from ...domain.grains import transaction_column_reference
from ..graph.relationships import Coordinate


class DecisionFieldResolver:
    """Resolve requested pivot fields to owner-qualified semantic coordinates."""

    def __init__(self, planner):
        self.planner = planner

    def fields(self, request):
        result = []
        for index, ref in enumerate([*request.rows, *request.columns]):
            if ref.dimensionId or ref.transactionId:
                grain_id, column_id = (ref.dimensionId, ref.columnId)
                owner_name = None
                if ref.transactionId:
                    grain_id, column_id = transaction_column_reference(
                        ref.transactionId,
                        ref.columnId,
                        list(self.planner.objects.values()),
                    )
                    owner_name = self.planner.objects.get(ref.transactionId, {}).get(
                        "name"
                    )
                level = self.planner.objects.get(grain_id)
                if not level or level["kind"] not in {"dimension", "grain"}:
                    raise MetadataError(
                        "invalid_view_field",
                        "The selected grain context no longer exists.",
                    )
                columns = {
                    c["columnId"]: c["name"]
                    for c in dimension_columns(
                        level, list(self.planner.objects.values())
                    )
                }
                if column_id not in columns:
                    raise MetadataError(
                        "invalid_view_field",
                        "Choose a declared grain key or Dimension attribute.",
                    )
                coordinate = self.planner.relationships.outputs(grain_id)[column_id]
                result.append(
                    {
                        "id": f"axis_{index}",
                        **ref.model_dump(exclude_none=True),
                        "label": (owner_name or level["name"])
                        + " · "
                        + columns[column_id],
                        "axis": "row" if index < len(request.rows) else "column",
                        "coordinate": coordinate,
                    }
                )
                continue
            dim = self.planner.objects.get(ref.memberCatalogId)
            if not dim or dim["kind"] != "dimensionMembers":
                raise MetadataError(
                    "invalid_view_field", "A selected dimension no longer exists."
                )
            definition = dim["definition"]
            exposed = set(definition["keyColumnIds"]) | set(
                definition["attributeColumnIds"]
            )
            column = next(
                (
                    c
                    for c in definition["columns"]
                    if c["columnId"] == ref.columnId and c["columnId"] in exposed
                ),
                None,
            )
            if not column:
                raise MetadataError(
                    "invalid_view_field",
                    "Choose an exposed attribute from the dimension.",
                )
            result.append(
                {
                    "id": f"axis_{index}",
                    "memberCatalogId": dim["objectId"],
                    "columnId": column["columnId"],
                    "label": dim["name"] + " · " + column["displayName"],
                    "axis": "row" if index < len(request.rows) else "column",
                    "coordinate": Coordinate(
                        frozenset(
                            {
                                (
                                    "dimensionMembers",
                                    dim["objectId"],
                                    column["columnId"],
                                ),
                                (
                                    "source",
                                    definition["sourceId"],
                                    column["sourceColumnId"],
                                ),
                            }
                        ),
                        is_time=dim["isTimeDim"],
                    ),
                }
            )
        return result
