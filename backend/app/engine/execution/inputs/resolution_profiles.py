from ....warehouse.sql import quote_identifier as q
from ...sql.compilation_context import CompilationContext
from ...transformations.dates.calendar import (
    CalendarResolutionCompiler,
    effective_calendar_objects,
)
from ...transformations.multigrain.planner import TransactionResolutionPlanner


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
