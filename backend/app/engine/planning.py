from ..core.errors import MetadataError
from .contracts import (
    CrossJoin,
    DistinctProject,
    Filter,
    LookupJoin,
    PlanningPlan,
    RelationScan,
    RelationSchema,
    digest,
)


def invalid(message):
    raise MetadataError("invalid_planning_definition", message)


class PlanningLevelPlanner:
    """Analyze stable logical references; SQL generation belongs to the compiler."""

    def __init__(self, resolution_max_rows=1000000):
        self.resolution_max_rows = resolution_max_rows

    def plan(self, level, objects, relations):
        objects = {o["objectId"]: o for o in objects}
        definition = level["definition"]
        population = definition["population"]
        projection = population["operation"] == "distinct_projection"
        selections = [
            (c["grainColumnId"], c["populationRef"])
            for c in definition["grain"]["columns"]
        ]
        selections += [
            (a["columnId"], a["populationRef"])
            for a in definition.get("populationAttributes", [])
        ]
        schema, scans, joins = [], [], []
        if projection:
            factors = [
                {"sourceId": population["sourcePlanningLevelId"], "factorId": None}
            ]
        elif population["operation"] == "source":
            factors = [{"sourceId": population["sourceId"], "factorId": None}]
        else:
            factors = population["factors"]
        if not factors:
            invalid("Choose at least one population factor.")
        for factor in factors:
            relation = relations[factor["sourceId"]]
            pipeline = None
            if population.get("inputTransform"):
                from .population_inputs import population_columns, population_owner
                from .calendar_resolution import effective_calendar_objects

                effective = effective_calendar_objects(
                    list(objects.values()), relations
                )
                relation = {
                    **relation,
                    "columns": population_columns(
                        population, effective, relation["columns"]
                    ),
                }
                pipeline = {
                    "owner": population_owner(population),
                    "objects": effective,
                    "relations": relations,
                    "maxRows": self.resolution_max_rows,
                }
            available = {c["columnId"]: c for c in relation["columns"]}
            selected = [
                (cid, ref)
                for cid, ref in selections
                if ref.get("factorId") == factor.get("factorId")
            ]
            if not selected:
                invalid(
                    "Every Cartesian factor must contribute a key or output attribute."
                )
            columns = []
            for cid, ref in selected:
                input_id = (
                    (ref.get("sourceGrainColumnId") or ref.get("sourceOutputColumnId"))
                    if projection
                    else ref["columnId"]
                )
                if input_id not in available:
                    invalid(
                        "A population column no longer exists in its effective input schema."
                    )
                source_column = available[input_id]
                columns.append(
                    (
                        cid,
                        source_column["name"]
                        if pipeline
                        else source_column.get("storageName", source_column["name"]),
                    )
                )
                schema.append(
                    {
                        **source_column,
                        "columnId": cid,
                        "name": self.semantic_name(source_column, objects),
                        "role": "key"
                        if cid in definition["grain"]["keyIds"]
                        else "attribute",
                        "ownerName": objects[factor["sourceId"]]["name"],
                        "lineage": {
                            "kind": "population",
                            "resourceId": factor["sourceId"],
                            "factorId": factor.get("factorId"),
                            "columnId": input_id,
                        },
                    }
                )
            scans.append(
                RelationScan(
                    relation["physicalName"],
                    tuple(columns),
                    input_pipeline=pipeline,
                    generation=relation.get("generationToken", relation["artifactId"])
                    if projection
                    else None,
                    nonnullable_ids=tuple(
                        cid
                        for cid, _ in columns
                        if not next(c for c in schema if c["columnId"] == cid)[
                            "nullable"
                        ]
                    ),
                )
            )
        by_output = {c["columnId"]: c for c in schema}
        schema = [by_output[cid] for cid, _ in selections]
        root = scans[0] if len(scans) == 1 else CrossJoin(tuple(scans))
        if projection:
            root = DistinctProject(scans[0])
        lookups = []
        for index, binding in enumerate(definition["dimensionBindings"]):
            dimension = objects[binding["dimensionId"]]
            dim_def = dimension["definition"]
            relation = relations[dim_def["sourceId"]]
            from .object_inputs import input_plan

            transform = input_plan(
                dimension, relation["columns"], relation["physicalName"]
            )
            source_columns = {
                c["columnId"]: c
                for c in (
                    transform.schema.columns if transform else relation["columns"]
                )
            }
            dim_columns = {c["columnId"]: c for c in dim_def["columns"]}
            # Names within a dimension relation are semantic IDs, never ambiguous physical names.
            scan = RelationScan(
                relation["physicalName"],
                tuple(
                    (c["columnId"], source_columns[c["sourceColumnId"]]["name"])
                    for c in dim_def["columns"]
                ),
                dim_def.get("filters"),
                transform=transform,
                filter_columns=tuple(
                    {**source_columns[c["sourceColumnId"]], "columnId": c["columnId"]}
                    for c in dim_def["columns"]
                ),
            )
            mappings = tuple(
                (
                    m.get("grainColumnId") or m.get("populationColumnId"),
                    m["dimensionColumnId"],
                )
                for m in binding["keyMappings"]
            )
            outputs = []
            for dim_id in binding["selectedAttributeColumnIds"]:
                dim_column = dim_columns[dim_id]
                source_column = source_columns[dim_column["sourceColumnId"]]
                cid = (
                    "da_"
                    + digest([level["objectId"], binding["dimensionId"], dim_id])[:28]
                )
                outputs.append((cid, dim_id))
                schema.append(
                    {
                        **source_column,
                        "columnId": cid,
                        "name": dim_column["name"],
                        "displayName": dim_column["displayName"],
                        "role": "attribute",
                        "ownerName": dimension["name"],
                        "lineage": {
                            "kind": "dimension",
                            "dimensionId": dimension["objectId"],
                            "columnId": dim_id,
                            "sourceId": dim_def["sourceId"],
                            "sourceColumnId": dim_column["sourceColumnId"],
                        },
                    }
                )
            join = LookupJoin(
                root, scan, mappings, tuple(outputs), "__match_" + str(index)
            )
            joins.append(join)
            lookups.append(scan)
            root = join
        if definition.get("filters"):
            root = Filter(root, definition["filters"], tuple(schema))
        self.assign_output_names(schema)
        if len(schema) > 2000:
            invalid("Planning output exceeds 2000 columns.")
        return PlanningPlan(
            root,
            RelationSchema(tuple(schema)),
            tuple(definition["grain"]["keyIds"]),
            tuple(scans),
            tuple(lookups),
            tuple(joins),
            projection,
        )

    @staticmethod
    def semantic_name(column, objects):
        """Recover a parent's business name without stripping genuine source names."""
        if column.get("semanticName"):
            return column["semanticName"]
        origin = column.get("lineage", {})
        if origin.get("kind") == "dimension":
            dimension = objects.get(origin.get("dimensionId"), {})
            original = next(
                (
                    c
                    for c in dimension.get("definition", {}).get("columns", [])
                    if c["columnId"] == origin.get("columnId")
                ),
                None,
            )
            if original:
                return original["name"]
        return column["name"]

    @staticmethod
    def assign_output_names(schema):
        # Grain/population columns precede enrichment. The first occurrence keeps
        # its plain name; reserve all plain names before qualifying later outputs
        # so generated aliases cannot steal a real selected column's name.
        reserved = {c["name"] for c in schema}
        used = set()
        for column in schema:
            base = column["name"]
            column["semanticName"] = base
            name = base
            if name in used:
                candidate = column["ownerName"] + "__" + base
                name = candidate
                if name in used or name in reserved:
                    name = candidate + "__" + digest(column["columnId"])[:8]
                suffix = 2
                while name in used or name in reserved:
                    name = (
                        candidate
                        + "__"
                        + digest(column["columnId"])[:8]
                        + "_"
                        + str(suffix)
                    )
                    suffix += 1
            used.add(name)
            column["name"] = name
