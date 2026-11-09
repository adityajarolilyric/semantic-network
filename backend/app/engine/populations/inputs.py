"""Shared Pivot → Calendar input pipeline for distinct source populations."""

from ...warehouse.sql import quote_identifier as q
from ..sql.compilation_context import CompilationContext
from ..sql.contracts import Check
from ..transformations.dates.calendar import (
    CalendarResolutionCompiler,
    CalendarResolutionPlanner,
)
from ..transformations.filters import FilterCompiler


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

    host = CompilationContext(
        database,
        pipeline["objects"],
        pipeline["relations"],
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
    if owner["definition"].get("filters"):
        # Filters address stable input IDs, independently of physical names.
        aliases = ", ".join(q(c["name"]) + " AS " + q(c["columnId"]) for c in columns)
        filtered = "SELECT " + aliases + " FROM (" + query + ")"
        predicate = FilterCompiler.compile(owner["definition"]["filters"], columns)
        projection = ", ".join(
            q(c["columnId"]) + " AS " + q(c["name"]) for c in columns
        )
        query = "SELECT " + projection + " FROM (" + filtered + ") WHERE " + predicate
    return host.with_sql(query), tuple(
        Check(c.code, host.with_sql(c.sql)) for c in host.input_checks
    )
