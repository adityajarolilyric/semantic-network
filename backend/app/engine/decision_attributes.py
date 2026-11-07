"""Expose reporting attributes from Transaction-native and Base contexts only."""

from ..domain.dimensions import dimension_columns
from .relationships import LevelRelationshipAnalyzer
from .representations import RepresentationPolicy


class DecisionAttributeCatalog:
    def __init__(self, objects, base_id):
        self.objects = {obj["objectId"]: obj for obj in objects}
        self.object_list = list(self.objects.values())
        self.transactions_by_grain = {}
        for obj in objects:
            if obj["kind"] == "transaction":
                self.transactions_by_grain.setdefault(
                    obj["definition"].get("nativeGrainId"), []
                ).append(obj)
        self.base_id = base_id
        self.policy = RepresentationPolicy(objects, base_id)
        self.relationships = LevelRelationshipAnalyzer(objects)

    def options(self):
        targets = self.policy.native_targets | (
            {self.base_id} if self.base_id else set()
        )
        # Base first makes the chosen reference stable when additional native
        # projections expose the same member. Names alone never merge fields.
        ordered = sorted(targets, key=lambda key: (key != self.base_id, key))
        groups, by_token = [], {}
        for grain_id in ordered:
            grain = self.objects.get(grain_id)
            if not grain:
                continue
            transactions = self.transactions_by_grain.get(grain_id, [])
            outputs = self.relationships.outputs(grain_id)
            for column in dimension_columns(grain, self.object_list):
                ref = {"dimensionId": grain_id, "columnId": column["columnId"]}
                if grain["kind"] == "grain":
                    ref = {
                        "transactionId": transactions[0]["objectId"],
                        "columnId": column["columnId"],
                    }
                aliases = [ref]
                if column.get("memberCatalogId"):
                    aliases.append(
                        {
                            "memberCatalogId": column["memberCatalogId"],
                            "columnId": column["memberColumnId"],
                        }
                    )
                for tx in transactions:
                    aliases.extend(
                        {
                            "transactionId": tx["objectId"],
                            "columnId": mapping["sourceColumnId"],
                        }
                        for mapping in tx["definition"].get("keyMappings", [])
                        if mapping["grainColumnId"] == column["columnId"]
                    )
                coordinate = outputs[column["columnId"]]
                matches = {
                    by_token[token] for token in coordinate.tokens if token in by_token
                }
                index = min(matches) if matches else len(groups)
                if not matches:
                    groups.append(
                        {
                            "ref": ref,
                            "grain": grain,
                            "column": {**column, "aliases": []},
                        }
                    )
                for previous in matches - {index}:
                    groups[index]["column"]["aliases"].extend(
                        groups[previous]["column"]["aliases"]
                    )
                    groups[previous]["removed"] = True
                    for token, owner in list(by_token.items()):
                        if owner == previous:
                            by_token[token] = index
                groups[index]["column"]["aliases"].extend(aliases)
                for token in coordinate.tokens:
                    by_token[token] = index
        # Previously saved layouts may reference a lookup Dimension directly.
        # Preserve those selections only when they alias an already exposed field;
        # this must never introduce additional attributes into the selector.
        for grain in self.object_list:
            if grain["kind"] != "dimension" or grain["objectId"] in targets:
                continue
            outputs = self.relationships.outputs(grain["objectId"])
            for column in dimension_columns(grain, self.object_list):
                coordinate = outputs.get(column["columnId"])
                matches = (
                    {
                        by_token[token]
                        for token in coordinate.tokens
                        if token in by_token
                    }
                    if coordinate
                    else set()
                )
                if matches:
                    groups[min(matches)]["column"]["aliases"].append(
                        {
                            "dimensionId": grain["objectId"],
                            "columnId": column["columnId"],
                        }
                    )
        dimensions, transactions = {}, {}
        for group in groups:
            if group.get("removed"):
                continue
            ref, grain, column = group["ref"], group["grain"], group["column"]
            if "transactionId" in ref:
                tx = self.objects[ref["transactionId"]]
                entry = transactions.setdefault(
                    tx["objectId"],
                    {
                        "transactionId": tx["objectId"],
                        "name": tx["name"],
                        "columns": [],
                    },
                )
            else:
                entry = dimensions.setdefault(
                    grain["objectId"],
                    {
                        "dimensionId": grain["objectId"],
                        "name": grain["name"],
                        "memberCatalogId": grain.get("memberCatalogId"),
                        "columns": [],
                    },
                )
            entry["columns"].append(column)
        return {
            "dimensionMembers": [],
            "dimensions": list(dimensions.values()),
            "transactions": list(transactions.values()),
        }
