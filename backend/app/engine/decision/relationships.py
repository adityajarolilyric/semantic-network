from dataclasses import replace

from ..graph.relationships import LevelRelationshipAnalyzer

TARGET = "__decision_view"


def virtual_level(fields):
    return {
        "objectId": TARGET,
        "kind": "dimension",
        "name": "Planning View",
        "definition": {
            "grain": {"keyIds": [f["id"] for f in fields], "columns": []},
            "population": {"operation": "source", "sourceId": "__view"},
            "memberBindings": [],
        },
    }


class ViewRelationships(LevelRelationshipAnalyzer):
    def __init__(self, objects, fields):
        super().__init__([*objects, virtual_level(fields)])
        self._cache[TARGET] = {
            f["id"]: replace(f["coordinate"], column_id=f["id"]) for f in fields
        }

        # A displayed dimension key can determine its attributes without making
        # those attributes part of the pivot grain.
        bindings = []
        for dimension in self.dimension_members.values():
            mappings = []
            for key in dimension["definition"]["keyColumnIds"]:
                matched = [
                    field
                    for field in fields
                    if ("dimensionMembers", dimension["objectId"], key)
                    in field["coordinate"].tokens
                ]
                if len(matched) != 1:
                    break
                mappings.append(
                    {"grainColumnId": matched[0]["id"], "memberColumnId": key}
                )
            else:
                if mappings:
                    bindings.append(
                        {
                            "memberCatalogId": dimension["objectId"],
                            "keyMappings": mappings,
                            "selectedAttributeColumnIds": [],
                        }
                    )
        self.levels[TARGET]["definition"]["memberBindings"] = bindings

    def resolve(self, source, target_coordinate):
        if source == TARGET:
            # Two displayed attributes can be proven aliases of the same source
            # field. Either grouping column proves that the source key is retained.
            matches = [
                c
                for c in self.outputs(TARGET).values()
                if c.tokens & target_coordinate.tokens
            ]
            if matches:
                return matches[0]
            return super().resolve(source, target_coordinate)
        return super().resolve(source, target_coordinate)
