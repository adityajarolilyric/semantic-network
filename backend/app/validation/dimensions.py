"""Focused semantic rules; diagnostics and graph state are owned by the shared context."""

from ..core.errors import MetadataError
from ..engine.calendar_resolution import CalendarResolutionPlanner
from ..engine.contracts import digest
from ..engine.population_inputs import population_columns


class DimensionRules:
    def __init__(self, context):
        self.context = context

    def population_columns(self, owner, source):
        population = owner["definition"]["population"]
        calendar = (population.get("inputTransform") or {}).get("calendarResolution")
        if calendar:
            self.context.reference(
                owner,
                calendar["memberCatalogId"],
                "dimensionMembers",
                "definition.population.inputTransform.calendarResolution",
            )
        try:
            return {
                c["columnId"]: c
                for c in population_columns(
                    population,
                    list(self.context.objects.values()),
                    list(self.context.columns(source).values()),
                )
            }
        except MetadataError as error:
            self.context.error(
                owner, error.code, error.message, "definition.population.inputTransform"
            )
            return {}

    def grain_type(self, level: dict, column_id: str, seen=None) -> str | None:
        seen = set() if seen is None else seen
        marker = (level["objectId"], column_id)
        if marker in seen or len(seen) > 128:
            return None
        seen.add(marker)
        definition = level["definition"]
        column = next(
            (
                c
                for c in [
                    *definition["grain"]["columns"],
                    *definition.get("populationAttributes", []),
                ]
                if c.get("grainColumnId", c.get("columnId")) == column_id
            ),
            None,
        )
        if not column:
            for binding in definition["memberBindings"]:
                dimension = self.context.objects.get(binding["memberCatalogId"])
                for cid in binding["selectedAttributeColumnIds"]:
                    if (
                        column_id
                        == "da_"
                        + digest([level["objectId"], binding["memberCatalogId"], cid])[
                            :28
                        ]
                        and dimension
                    ):
                        return self.context.dimension_type(dimension, cid)
            return None
        population, ref = definition["population"], column["populationRef"]
        if population["operation"] == "transaction":
            transaction = self.context.objects.get(population["transactionId"])
            source = (
                self.context.objects.get(transaction["definition"]["sourceId"])
                if transaction
                else None
            )
            if not source:
                return None
            columns = CalendarResolutionPlanner(
                list(self.context.objects.values()),
                transaction,
                source["definition"]["columns"],
            ).schema()
            return next(
                (c["physicalType"] for c in columns if c["columnId"] == column_id), None
            )

        if population["operation"] == "distinct_projection":
            parent = self.context.objects.get(population["sourceDimensionId"])
            return (
                self.context.grain_type(
                    parent,
                    ref.get("sourceGrainColumnId") or ref.get("sourceOutputColumnId"),
                    seen,
                )
                if parent and parent["kind"] == "dimension"
                else None
            )
        if population["operation"] == "cartesian_product":
            factor = next(
                f for f in population["factors"] if f["factorId"] == ref["factorId"]
            )
            source_id = factor["sourceId"]
        else:
            source_id = population["sourceId"]
        source = self.context.objects.get(source_id)
        if source and source["kind"] == "source":
            return (
                self.context.population_columns(level, source)
                .get(ref["columnId"], {})
                .get("physicalType")
            )
        return None

    def dimension_type(self, dimension: dict, column_id: str) -> str | None:
        definition = dimension["definition"]
        column = next(
            (c for c in definition["columns"] if c["columnId"] == column_id), None
        )
        source = self.context.objects.get(definition["sourceId"])
        if column and source and source["kind"] == "source":
            return (
                self.context.object_columns(dimension, source)
                .get(column["sourceColumnId"], {})
                .get("physicalType")
            )
        return None

    def validate_dimension(self, obj: dict):
        definition = obj["definition"]
        population = definition["population"]
        grain = definition["grain"]
        columns = {c["grainColumnId"]: c for c in grain["columns"]}
        attributes = definition.get("populationAttributes", [])
        all_columns = {**columns, **{a["columnId"]: a for a in attributes}}
        if population["operation"] == "distinct_projection":
            parent = self.context.reference(
                obj,
                population["sourceDimensionId"],
                "dimension",
                "definition.population",
            )
            if parent:
                parent_grain = parent["definition"]["grain"]
                available = {c["grainColumnId"] for c in parent_grain["columns"]} | {
                    a["columnId"]
                    for a in parent["definition"].get("populationAttributes", [])
                }
                for b in parent["definition"]["memberBindings"]:
                    available |= {
                        "da_"
                        + digest([parent["objectId"], b["memberCatalogId"], cid])[:28]
                        for cid in b["selectedAttributeColumnIds"]
                    }
                self.context.require_columns(
                    obj,
                    [
                        a["populationRef"].get("sourceOutputColumnId")
                        or a["populationRef"].get("sourceGrainColumnId")
                        for a in attributes
                    ],
                    available,
                    "definition.populationAttributes",
                )
                self.context.require_columns(
                    obj,
                    [
                        c["populationRef"].get("sourceGrainColumnId")
                        or c["populationRef"].get("sourceOutputColumnId")
                        for c in grain["columns"]
                    ],
                    available,
                    "definition.grain",
                )
        else:
            factors = population.get("factors") or [
                {"sourceId": population.get("sourceId"), "selectedColumnIds": []}
            ]
            for factor in factors:
                source = self.context.source(obj, factor["sourceId"])
                if source:
                    selected = factor["selectedColumnIds"]
                    if population["operation"] == "source":
                        selected = [
                            c["populationRef"]["columnId"] for c in all_columns.values()
                        ]
                    self.context.require_columns(
                        obj,
                        selected,
                        self.context.population_columns(obj, source),
                        "definition.population",
                    )
        for binding in definition["memberBindings"]:
            dimension = self.context.reference(
                obj,
                binding["memberCatalogId"],
                "dimensionMembers",
                "definition.memberBindings",
            )
            self.context.require_columns(
                obj,
                [
                    m.get("grainColumnId") or m.get("populationColumnId")
                    for m in binding["keyMappings"]
                ],
                all_columns,
                "definition.memberBindings",
            )
            if dimension:
                dim_def = dimension["definition"]
                self.context.require_columns(
                    obj,
                    [m["memberColumnId"] for m in binding["keyMappings"]],
                    {c["columnId"] for c in dim_def["columns"]},
                    "definition.memberBindings.keyMappings",
                )
                self.context.require_columns(
                    obj,
                    binding["selectedAttributeColumnIds"],
                    set(dim_def["attributeColumnIds"]),
                    "definition.memberBindings",
                )
                for mapping in binding["keyMappings"]:
                    self.context.compatible(
                        obj,
                        self.context.grain_type(
                            obj,
                            mapping.get("grainColumnId")
                            or mapping.get("populationColumnId"),
                        ),
                        self.context.dimension_type(
                            dimension, mapping["memberColumnId"]
                        ),
                        "definition.memberBindings",
                    )
        filter_columns = set(all_columns)
        for binding in definition["memberBindings"]:
            filter_columns |= {
                "da_" + digest([obj["objectId"], binding["memberCatalogId"], cid])[:28]
                for cid in binding["selectedAttributeColumnIds"]
            }
        if definition.get("filters"):
            self.context.validate_filters(
                obj,
                definition["filters"],
                {cid: self.context.grain_type(obj, cid) for cid in filter_columns},
            )
