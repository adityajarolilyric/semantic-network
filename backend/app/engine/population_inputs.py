"""Shared Pivot → Calendar input pipeline for distinct source populations."""

from .calendar_resolution import CalendarResolutionPlanner, CalendarResolutionCompiler
from .contracts import Check
from ..warehouse.repository import quote_identifier as q


def population_owner(population):
    transform = population["inputTransform"]
    return {
        "objectId": transform["objectId"],
        "definition": {"sourceId": population["sourceId"], **transform},
    }


def population_columns(population, objects, columns):
    if not population.get("inputTransform"):
        return columns
    return CalendarResolutionPlanner(
        objects, population_owner(population), columns
    ).schema()


def compile_population_input(database, pipeline):
    # Reuse the same input compilation, calendar checks and limits as Transactions.
    from .measure_compiler import MeasureCompiler

    host = MeasureCompiler(
        database,
        pipeline["objects"],
        {},
        pipeline["relations"],
        None,
        resolution_max_rows=pipeline["maxRows"],
    )
    owner = pipeline["owner"]
    source = host.managed[owner["definition"]["sourceId"]]
    if owner["definition"].get("calendarResolution"):
        compiler = CalendarResolutionCompiler(host, owner)
        node = compiler.compile()
        columns = compiler.p.schema()
        query = (
            "SELECT "
            + ", ".join(q(c["columnId"]) + " AS " + q(c["name"]) for c in columns)
            + " FROM "
            + q(node)
        )
    else:
        table, columns = host.object_input(owner, source)
        query = "SELECT " + ", ".join(q(c["name"]) for c in columns) + " FROM " + table
    return host.with_sql(query), tuple(
        Check(c.code, host.with_sql(c.sql)) for c in host.input_checks
    )
