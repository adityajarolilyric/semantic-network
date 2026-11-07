"""Transaction matching and last-application overlay, without persistent SQL objects."""

import re
from dataclasses import dataclass

from ..core.errors import MetadataError
from ..warehouse.sql import quote_identifier as q
from ..warehouse.sql import quote_literal as lit
from .calendar_resolution import (
    CalendarResolutionCompiler,
    CalendarResolutionPlanner,
    effective_calendar_objects,
)
from .compilation_context import CompilationContext
from .contracts import Check, digest
from .date_range_resolution import DateRangeCompiler, DateRangePlanner
from .object_inputs import input_columns
from .relationships import Coordinate, LevelRelationshipAnalyzer

RESOLUTION_POLICY = "transaction-resolution-v3-calendar-stage"


def key_id(transaction_id, grain_id):
    return "txkey_" + digest([transaction_id, grain_id])[:24]


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


@dataclass
class ResolvedTransaction:
    node: str
    columns: list
    mappings: list
    matches: str
    latest_nodes: list
    input_node: str | None = None
    unfiltered_node: str | None = None


class TransactionResolutionCompiler:
    def __init__(self, host, transaction, profiles, max_rows):
        self.host, self.tx, self.profiles, self.max_rows = (
            host,
            transaction,
            profiles,
            max_rows,
        )
        self.d = transaction["definition"]
        self.source = host.managed[self.d["sourceId"]]

        self.planner = TransactionResolutionPlanner(
            effective_calendar_objects(list(host.objects.values()), host.managed),
            transaction,
            self.source["columns"],
        )
        self.config, self.level = (
            self.planner.config,
            (self.d.get("nativeGrainId") or self.d["dimensionId"]),
        )

    def compile(self):
        h, p, config = self.host, self.planner, self.config
        source_table, _ = h.object_input(self.tx, self.source)
        calendar_enabled = bool(p.calendar.config)
        if calendar_enabled:
            expanded = CalendarResolutionCompiler(h, self.tx).compile()
            source_table = (
                "("
                + "SELECT "
                + ", ".join(
                    q(cid) + " AS " + q(c["name"]) for cid, c in p.columns.items()
                )
                + ", __calendar_days FROM "
                + q(expanded)
                + ")"
            )
        raw = h.cte(
            "SELECT "
            + ", ".join(q(c["name"]) + " AS " + q(cid) for cid, c in p.columns.items())
            + (", __calendar_days" if calendar_enabled else "")
            + " FROM "
            + source_table
        )
        order_ids = config["orderColumnIds"]
        partitions = config["partitionColumnIds"]
        payload_ids = list(
            dict.fromkeys(
                [
                    *(v["sourceColumnId"] for v in config["values"]),
                    *order_ids,
                    *partitions,
                    *config["passthroughColumnIds"],
                ]
            )
        )
        if calendar_enabled:
            payload_ids = list(
                dict.fromkeys(
                    [
                        *payload_ids,
                        p.calendar.date_id,
                        p.calendar.order_id,
                        "__calendar_days",
                    ]
                )
            )
        payload = "tuple(" + ", ".join(q(cid) for cid in payload_ids) + ")"
        order = "tuple(" + ", ".join(q(cid) for cid in order_ids) + ")"
        h.assertions.append(
            Check(
                "null_resolution_order",
                f"SELECT * FROM {q(raw)} WHERE {' OR '.join('isNull(' + q(cid) + ')' for cid in order_ids)} LIMIT 20",
            )
        )
        # Flags use the physical input column names, so add them before aliasing.
        flag_expressions = p.flags()
        flag_sql = (
            "tuple(" + ", ".join(flag_expressions) + ")"
            if flag_expressions
            else "tuple(0)"
        )
        flagged = h.cte(
            f"SELECT {', '.join(q(c['name']) + ' AS ' + q(cid) for cid, c in p.columns.items())}{', __calendar_days' if calendar_enabled else ''}, {flag_sql} AS __profile FROM {source_table}"
        )
        known_profiles = ", ".join(
            "tuple(" + ", ".join(str(v) for v in profile) + ")"
            for profile in self.profiles
        )
        h.assertions.append(
            Check(
                "resolution_profiles_changed",
                f"SELECT * FROM {q(flagged)} WHERE "
                + (f"__profile NOT IN ({known_profiles})" if known_profiles else "1")
                + " LIMIT 20",
            )
        )
        seed = h.cte(
            f"SELECT {', '.join(q(k) for k in h.keys(self.level))}, 0 AS {q(VALUE_NAME)} FROM ({h.population(self.level)})"
        )

        date_range = DateRangeCompiler(self)
        date_range.prepare(raw)
        branches, latest_nodes = [], []
        for profile_index, profile in enumerate(self.profiles):
            selected_rules = []
            for rule, flag in zip(p.rules, profile):
                if flag:
                    target = (
                        rule["target"]
                        if rule["kind"] == "column"
                        else rule["selectors"][flag - 1]["target"]
                    )
                    selected_rules.append(
                        (
                            rule["sourceColumnId"]
                            if rule["kind"] == "column"
                            else rule["valueColumnId"],
                            target,
                        )
                    )
            coords = {
                f"__match_attr_{i}": p.target(target)
                for i, (_, target) in enumerate(selected_rules)
            }
            native_sql = h.enrich(seed, self.level, coords)
            native = h.cte(native_sql)
            original_native = native
            native = date_range.expand_native(native)
            # Derive target SQL types from the captured population/dimension metadata.
            casted = []
            for i, (cid, target) in enumerate(selected_rules):
                coordinate = coords[f"__match_attr_{i}"]
                if coordinate.column_id:
                    col = next(
                        c
                        for c in h.populations[self.level]["columns"]
                        if c["columnId"] == coordinate.column_id
                    )
                else:
                    dim = h.objects[coordinate.dimension_id]["definition"]
                    dim_col = next(
                        c
                        for c in dim["columns"]
                        if c["columnId"] == coordinate.dimension_column_id
                    )
                    col = next(
                        c
                        for c in h.object_input(
                            h.objects[coordinate.dimension_id],
                            h.managed[dim["sourceId"]],
                        )[1]
                        if c["columnId"] == dim_col["sourceColumnId"]
                    )
                typ = col["physicalType"]
                expression = (
                    f"toString({q(cid)})"
                    if "String" in typ
                    else f"accurateCastOrNull(toString({q(cid)}),{lit(typ)})"
                )
                casted.append(expression + " AS " + q(f"__criterion_{i}"))
            casted.extend(date_range.bounds())
            predicate = "__profile = tuple(" + ", ".join(str(v) for v in profile) + ")"
            typed = h.cte(
                f"SELECT *, {', '.join(casted)} FROM {q(flagged)} WHERE {predicate}"
                if casted
                else f"SELECT * FROM {q(flagged)} WHERE {predicate}"
            )
            criteria = [f"__criterion_{i}" for i in range(len(selected_rules))]
            if criteria:
                h.assertions.append(
                    Check(
                        "invalid_resolution_literal",
                        f"SELECT * FROM {q(typed)} WHERE {' OR '.join('isNull(' + q(c) + ')' for c in criteria)} LIMIT 20",
                    )
                )
            group = [*criteria, *date_range.bound_names, *partitions]
            if calendar_enabled:
                group.append(p.calendar.date_id)
            group_sql = ", ".join(q(c) for c in group)
            prefix = group_sql + ", " if group else ""
            h.assertions.append(
                Check(
                    "ambiguous_resolution_order",
                    f"SELECT {prefix}{order} FROM {q(typed)} GROUP BY {prefix}{order} HAVING count()>1 LIMIT 20",
                )
            )
            latest = h.cte(
                f"SELECT {prefix}argMax({payload},{order}) AS __payload, max({order}) AS __order FROM {q(typed)}"
                + (" GROUP BY " + group_sql if group else "")
            )
            latest_nodes.append(latest)
            joins = " AND ".join(
                f"e.{q(c)} = n.{q(f'__match_attr_{i}')}" for i, c in enumerate(criteria)
            )
            native_keys = [
                f"n.{q(k)} AS {q('__native_' + str(i))}"
                for i, k in enumerate(h.keys(self.level))
            ]
            scope = [f"e.{q(c)} AS {q(c)}" for c in partitions]
            partition = ", ".join("e." + q(c) for c in group) or "1"
            if not calendar_enabled:
                branch = date_range.match_branch(
                    latest, native, joins, native_keys, scope, partition
                )
            else:
                join = (
                    f"INNER JOIN {q(native)} n ON {joins}"
                    if joins
                    else f"CROSS JOIN {q(native)} n"
                )
                fields = [
                    *native_keys,
                    *scope,
                    "e.__payload AS __payload",
                    "e.__order AS __order",
                    f"count() OVER (PARTITION BY {partition}) AS __children",
                    f"tupleElement(e.__payload,{payload_ids.index('__calendar_days') + 1}) AS __periods",
                    f"e.{q(p.calendar.date_id)} AS __resolved_day",
                    f"tupleElement(e.__payload,{payload_ids.index(p.calendar.order_id) + 1}) AS __resolved_order",
                ]
                branch = h.cte(f"SELECT {', '.join(fields)} FROM {q(latest)} e {join}")
            date_range.coverage(latest, original_native, criteria, joins)
            # Initial coverage, before overwrites: no source assignment may disappear.
            # Explicit membership is checked by reusing the same selective conditions.
            exists_join = (
                " AND ".join(
                    f"e.{q(c)} = n.{q(f'__match_attr_{i}')}"
                    for i, c in enumerate(criteria)
                )
                or "1"
            )
            probe = f"SELECT toUInt8(1) AS __matched FROM {q(native)} LIMIT 1"
            if criteria:
                probe = f"SELECT DISTINCT {', '.join(q(f'__match_attr_{i}') for i in range(len(criteria)))}, toUInt8(1) AS __matched FROM {q(native)}"
            h.assertions.append(
                Check(
                    "unmatched_resolution_row",
                    f"SELECT e.* FROM {q(latest)} e LEFT JOIN ({probe}) n ON {exists_join} WHERE coalesce(n.__matched,0)=0 LIMIT 20",
                )
            )
            branches.append(branch)
        keys = h.keys(self.level)
        native_ids = ["__native_" + str(i) for i in range(len(keys))]
        if branches:
            matches = h.cte(
                " UNION ALL ".join("SELECT * FROM " + q(b) for b in branches)
            )
        else:
            # Empty sources still expose a stable resolved schema.
            native_selected = [
                "n." + q(k) + " AS " + q(native_ids[i]) for i, k in enumerate(keys)
            ]
            expressions = native_selected + [
                "e." + q(c) + " AS " + q(c) for c in partitions
            ]
            expressions += [
                "tuple("
                + ", ".join("e." + q(c) for c in payload_ids)
                + ") AS __payload",
                "tuple(" + ", ".join("e." + q(c) for c in order_ids) + ") AS __order",
                "toUInt64(1) AS __children",
                "toUInt64(1) AS __periods",
                (
                    f"(SELECT any(__resolved_order) FROM {q(date_range.calendar)}) AS __resolved_order"
                    if date_range.enabled
                    else (
                        f"CAST(NULL AS Nullable({next(c['physicalType'] for c in p.calendar.added_columns() if c['columnId'] == p.calendar.order_id)})) AS __resolved_order"
                        if calendar_enabled
                        else "toUInt8(0) AS __resolved_order"
                    )
                ),
                *(
                    ["toDate32('1970-01-01') AS __resolved_day"]
                    if date_range.enabled or calendar_enabled
                    else []
                ),
            ]
            matches = h.cte(
                f"SELECT {', '.join(expressions)} FROM ({h.population(self.level)}) n CROSS JOIN {q(raw)} e WHERE 0"
            )
        group = [
            *native_ids,
            *(["__resolved_day"] if date_range.enabled or calendar_enabled else []),
            *partitions,
        ]
        grouped = ", ".join(q(c) for c in group)
        h.assertions.append(
            Check(
                "resolution_expansion_limit",
                f"SELECT count() AS expanded FROM {q(matches)} HAVING expanded>{self.max_rows}",
            )
        )
        h.assertions.append(
            Check(
                "ambiguous_resolution_order",
                f"SELECT {grouped}, __order FROM {q(matches)} GROUP BY {grouped}, __order HAVING count()>1 LIMIT 20",
            )
        )
        winner = h.cte(
            f"SELECT {grouped}, argMax(tuple(__payload,__children,__periods,__resolved_order),__order) AS __winner FROM {q(matches)} GROUP BY {grouped}"
        )
        columns = p.schema(
            h.populations[self.level]["columns"],
            h.managed[p.date_range.dimension["definition"]["sourceId"]]["columns"]
            if date_range.enabled
            else (),
        )
        selected = [
            q(native_ids[i]) + " AS " + q(key_id(self.tx["objectId"], k))
            for i, k in enumerate(keys)
        ]
        if calendar_enabled:
            selected += [
                q("__resolved_day") + " AS " + q(p.calendar.date_id),
                "tupleElement(__winner,4) AS " + q(p.calendar.order_id),
            ]
        if date_range.enabled:
            selected.append(q("__resolved_day") + " AS " + q(p.date_range.date_id))
            selected.append("tupleElement(__winner,4) AS " + q(p.date_range.order_id))
        modes = {v["sourceColumnId"]: v["mode"] for v in config["values"]}
        time_modes = {v["sourceColumnId"]: v.get("timeMode") for v in config["values"]}
        for cid in payload_ids:
            if calendar_enabled and cid in (
                p.calendar.date_id,
                p.calendar.order_id,
                "__calendar_days",
            ):
                continue
            value = (
                f"tupleElement(tupleElement(__winner,1),{payload_ids.index(cid) + 1})"
            )
            if cid in modes:
                value = f"toFloat64({value})"
                if modes[cid] == "split":
                    value += "/tupleElement(__winner,2)"
                if (date_range.enabled or calendar_enabled) and time_modes[
                    cid
                ] == "split":
                    value += "/tupleElement(__winner,3)"
            selected.append(value + " AS " + q(cid))
        node = h.cte(f"SELECT {', '.join(selected)} FROM {q(winner)}")
        mappings = [
            {"sourceColumnId": key_id(self.tx["objectId"], k), "grainColumnId": k}
            for k in keys
        ]
        return ResolvedTransaction(node, columns, mappings, matches, latest_nodes, raw)


VALUE_NAME = "__value"


async def prepare_resolution_profiles(warehouse, objects, managed, limit, settings):
    result = {}
    for tx in objects:
        if (
            tx["kind"] != "transaction"
            or tx["definition"].get("mappingMode") != "resolve_multi_grain"
        ):
            continue
        source = managed[tx["definition"]["sourceId"]]

        planner = TransactionResolutionPlanner(
            effective_calendar_objects(objects, managed), tx, source["columns"]
        )
        table = warehouse.qualify(source["physicalName"])
        compiler = None
        if planner.calendar.config or tx["definition"].get("pivot"):
            compiler = CompilationContext(
                warehouse.settings.clickhouse_database,
                objects,
                managed,
                resolution_max_rows=warehouse.settings.resolution_max_rows,
            )
            if planner.calendar.config:
                node = CalendarResolutionCompiler(compiler, tx).compile()
            else:
                input_table, cols = compiler.object_input(tx, source)
                node = compiler.cte(
                    "SELECT "
                    + ", ".join(q(c["name"]) + " AS " + q(c["columnId"]) for c in cols)
                    + " FROM "
                    + input_table
                )
            table = (
                "(SELECT "
                + ", ".join(
                    q(cid) + " AS " + q(c["name"]) for cid, c in planner.columns.items()
                )
                + " FROM "
                + q(node)
                + ")"
            )
        sql = planner.profile_sql(table, limit)
        if compiler:
            sql = compiler.with_sql(sql)

        profiles = await warehouse.queries.resolution_profiles(sql, settings)
        planner.validate_profiles(profiles, limit)
        result[tx["objectId"]] = profiles
    return result
