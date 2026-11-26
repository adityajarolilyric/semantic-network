"""Resolve independent Transaction storage without creating a catalog Dimension."""


class SourceStoragePolicy:
    def __init__(self, objects, managed):
        self.objects = {item["objectId"]: item for item in objects}
        self.managed = managed
        self.consumers = {}
        for item in self.objects.values():
            definition = item.get("definition", {})
            population = definition.get("population", {})
            sources = {definition.get("sourceId"), population.get("sourceId")}
            sources.update(
                factor.get("sourceId") for factor in population.get("factors", [])
            )
            for source_id in sources - {None}:
                self.consumers.setdefault(source_id, set()).add(item["objectId"])

    def binding(self, grain_id):
        grain = self.objects.get(grain_id, {})
        if grain.get("kind") != "grain":
            return None
        transaction = self.objects[grain["owner"]["objectId"]]
        definition = transaction["definition"]
        pivot = definition.get("pivot")
        if (
            definition.get("dimensionId")
            or (pivot and pivot.get("enabled", True))
            or any(definition.get(key) for key in ("calendarResolution", "filters"))
        ):
            return None
        source_id = definition["sourceId"]
        source = self.objects[source_id]
        managed = self.managed.get(source_id)
        if not managed or "columns" not in managed:
            return None
        # A pivoted shared input is not the physical source's row/column shape.
        if any(
            column.get("lineage", {}).get("kind", "source") != "source"
            for column in managed["columns"]
        ):
            return None
        source_columns = {
            column["columnId"]: column for column in source["definition"]["columns"]
        }
        if set(source_columns) != {column["columnId"] for column in managed["columns"]}:
            return None
        # Shared source modelling needs coordinated dependency invalidation; only
        # an independently owned Transaction can be its own writable population.
        if self.consumers.get(source_id, set()) - {
            source_id,
            transaction["objectId"],
            grain_id,
        }:
            return None
        return {
            "sourceId": source_id,
            "transactionId": transaction["objectId"],
            "tableName": source["definition"]["tableName"],
            "keyColumns": {
                key: source_columns[key]["name"] for key in definition["grainColumnIds"]
            },
            "columns": source_columns,
            "primaryKeyColumnIds": source["definition"].get("keyColumns", []),
        }
