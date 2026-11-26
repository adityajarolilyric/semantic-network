import re

from ....core.errors import MetadataError
from ....warehouse.sql import quote_identifier as q
from ....warehouse.sql import quote_literal as lit
from ...graph.relationships import Coordinate, LevelRelationshipAnalyzer
from ..dates.calendar import CalendarResolutionPlanner
from ..dates.planner import DateRangePlanner
from ..inputs import input_columns
from .contracts import key_id


def numeric(typ):
    return bool(re.match(r"^(?:U?Int|Float|Decimal)", typ))


class TransactionResolutionPlanner:
    def __init__(self, objects, transaction, source_columns):
        self.objects = {o["objectId"]: o for o in objects}
        self.tx = transaction
        self.d = transaction["definition"]
        self.config = self.d["resolution"]
        self.level = self.objects[
            (self.d.get("nativeGrainId") or self.d["dimensionId"])
        ]

        self.calendar = CalendarResolutionPlanner(objects, transaction, source_columns)
        self.columns = {c["columnId"]: c for c in self.calendar.schema()}
        self.relationships = LevelRelationshipAnalyzer(objects)
        self.rules = [r for a in self.config["axes"] for r in a["rules"]]

        self.date_range = DateRangePlanner(self)
        self.validate()

    def target(self, target):
        dim_id = target.get("memberCatalogId")
        cid = target["columnId"]
        if not dim_id:
            coordinate = self.relationships.outputs(self.level["objectId"]).get(cid)
            if not coordinate:
                raise MetadataError(
                    "invalid_resolution_target",
                    "Choose a native planning output or a bound dimension attribute.",
                )
            return coordinate
        dim = self.objects.get(dim_id)
        if not dim or dim["kind"] != "dimensionMembers":
            raise MetadataError(
                "invalid_resolution_target", "The matching dimension is missing."
            )
        col = next(
            (c for c in dim["definition"]["columns"] if c["columnId"] == cid), None
        )
        exposed = set(dim["definition"]["keyColumnIds"]) | set(
            dim["definition"]["attributeColumnIds"]
        )
        if not col or cid not in exposed:
            raise MetadataError(
                "invalid_resolution_target", "Choose an exposed dimension attribute."
            )
        coordinate = Coordinate(
            frozenset(
                {
                    ("dimensionMembers", dim_id, cid),
                    ("source", dim["definition"]["sourceId"], col["sourceColumnId"]),
                }
            )
        )
        resolved = self.relationships.resolve(self.level["objectId"], coordinate)
        if not resolved:
            raise MetadataError(
                "resolution_path_unavailable",
                "The native dimension cannot supply this matching attribute. Choose a finer native level or provide the required binding.",
            )
        return resolved

    def validate(self):
        if {a["grainColumnId"] for a in self.config["axes"]} != set(
            self.level["definition"]["grain"]["keyIds"]
        ):
            raise MetadataError(
                "incomplete_resolution_axes",
                "Configure every native grain axis, including Time, or explicitly allow all members.",
            )
        required = [
            *self.config["orderColumnIds"],
            *self.config["partitionColumnIds"],
            *self.config["passthroughColumnIds"],
            *(v["sourceColumnId"] for v in self.config["values"]),
            *self.date_range.required_columns,
        ]
        for rule in self.rules:
            required.extend(
                [rule["sourceColumnId"]]
                if rule["kind"] == "column"
                else [rule["selectorColumnId"], rule["valueColumnId"]]
            )
            for target in (
                [rule["target"]]
                if rule["kind"] == "column"
                else [s["target"] for s in rule["selectors"]]
            ):
                coordinate = self.target(target)
                if self.date_range.config and coordinate.is_time:
                    raise MetadataError(
                        "duplicate_time_resolution",
                        "Use the date-range axis for Time; do not constrain Time again in another rule.",
                    )
        if any(cid not in self.columns for cid in required):
            raise MetadataError(
                "invalid_resolution_column",
                "A configured matching, order or value column is missing from the managed schema.",
            )
        for cid in self.config["orderColumnIds"]:
            typ = self.columns[cid]["physicalType"]
            while typ.startswith(("Nullable(", "LowCardinality(")) and typ.endswith(
                ")"
            ):
                typ = typ[typ.index("(") + 1 : -1]
            if not re.match(
                r"^(?:U?Int\d+|Float\d+|Decimal(?:\d+)?\(|String$|FixedString\(|Date|Bool$|UUID$|Enum\d+\()",
                typ,
            ):
                raise MetadataError(
                    "invalid_resolution_order",
                    "Application order requires a sortable scalar column, such as text, a number or a date. Text uses alphabetical order.",
                )
        self.date_range.validate()
        for value in self.config["values"]:
            if not numeric(self.columns[value["sourceColumnId"]]["physicalType"]):
                raise MetadataError(
                    "invalid_resolution_value",
                    "Split/Copy applies only to explicitly selected numeric value columns.",
                )

    def present(self, cid, blank=True):
        col = self.columns[cid]
        name = q(col["name"])
        return (
            f"(isNotNull({name})"
            + (
                f" AND {name} != ''"
                if blank and "String" in col["physicalType"]
                else ""
            )
            + ")"
        )

    def flags(self):
        expressions = []
        for rule in self.rules:
            if rule["kind"] == "column":
                expressions.append(
                    f"if({self.present(rule['sourceColumnId'], rule['blankIsAbsent'])},1,0)"
                )
            else:
                selector = (
                    "toString("
                    + q(self.columns[rule["selectorColumnId"]]["name"])
                    + ")"
                )
                sp = self.present(rule["selectorColumnId"])
                vp = self.present(rule["valueColumnId"], rule["blankIsAbsent"])
                choices = ", ".join(
                    f"{selector}={lit(m['value'])},{i}"
                    for i, m in enumerate(rule["selectors"], 1)
                )
                expressions.append(
                    f"multiIf(NOT {sp} AND NOT {vp},0, NOT {sp} OR NOT {vp},-2,{choices},-1)"
                )
        return expressions

    def profile_sql(self, table, max_profiles):
        flags = self.flags()
        signature = "tuple(" + ", ".join(flags) + ")" if flags else "tuple(0)"
        return f"SELECT DISTINCT {signature} FROM {table} LIMIT {max_profiles + 1}"

    def validate_profiles(self, profiles, limit):
        if len(profiles) > limit:
            raise MetadataError(
                "resolution_profile_limit",
                "Too many distinct matching profiles. Reduce the configured matching combinations.",
            )
        for profile in profiles:
            if any(flag < 0 for flag in profile):
                raise MetadataError(
                    "invalid_resolution_selector",
                    "A level selector is unknown or its selector/value pair is incomplete.",
                )
            index = 0
            for axis in self.config["axes"]:
                active = profile[index : index + len(axis["rules"])]
                if (
                    not any(active)
                    and not axis["allowAll"]
                    and not axis.get("dateRange")
                ):
                    raise MetadataError(
                        "missing_resolution_constraint",
                        "A source row has no constraint for a required native axis. Supply a value or explicitly allow all members.",
                    )
                index += len(axis["rules"])

    def schema(self, population_columns=(), calendar_columns=()):
        pop = {c["columnId"]: c for c in population_columns}
        columns, used = [], set()
        for col in self.level["definition"]["grain"]["columns"]:
            cid = col["grainColumnId"]
            columns.append(
                {
                    "columnId": key_id(self.tx["objectId"], cid),
                    "name": col["name"],
                    "physicalType": pop.get(cid, {}).get("physicalType"),
                    "nullable": False,
                    "grainColumnId": cid,
                }
            )
            used.add(col["name"])
        if self.calendar.config:
            columns.extend(self.calendar.added_columns()[-2:])
            used.update(c["name"] for c in columns)
        if self.date_range.config:
            name = "Resolved date"
            while name in used:
                name += "_"
            used.add(name)
            columns.append(
                {
                    "columnId": self.date_range.date_id,
                    "name": name,
                    "physicalType": "Date32",
                    "nullable": False,
                    "role": "resolvedDate",
                }
            )
        if self.date_range.config:
            dim = self.date_range.dimension["definition"]
            ordering = next(
                c
                for c in dim["columns"]
                if c["columnId"] == dim["time"]["orderingColumnId"]
            )
            source_columns = (
                calendar_columns
                or self.objects[dim["sourceId"]]["definition"]["columns"]
            )

            source_columns = input_columns(self.date_range.dimension, source_columns)
            physical = next(
                (
                    c
                    for c in source_columns
                    if c["columnId"] == ordering["sourceColumnId"]
                ),
                {},
            )
            name = "Calendar order"
            while name in used:
                name += "_"
            used.add(name)
            columns.append(
                {
                    "columnId": self.date_range.order_id,
                    "name": name,
                    "physicalType": physical.get("physicalType"),
                    "nullable": False,
                    "role": "resolvedDateOrdering",
                }
            )
        value_ids = {v["sourceColumnId"] for v in self.config["values"]}
        cids = list(
            dict.fromkeys(
                [
                    *(v["sourceColumnId"] for v in self.config["values"]),
                    *self.config["orderColumnIds"],
                    *self.config["partitionColumnIds"],
                    *self.config["passthroughColumnIds"],
                ]
            )
        )
        for cid in cids:
            if self.calendar.config and cid in (
                self.calendar.date_id,
                self.calendar.order_id,
            ):
                continue
            col = dict(self.columns[cid])
            base_name, name, suffix = col["name"], col["name"], 2
            while name in used:
                name = base_name + "_" + str(suffix)
                suffix += 1
            used.add(name)
            columns.append(
                {
                    **col,
                    "name": name,
                    **({"physicalType": "Float64"} if cid in value_ids else {}),
                }
            )
        return [{**c, "storageName": c["columnId"]} for c in columns]
