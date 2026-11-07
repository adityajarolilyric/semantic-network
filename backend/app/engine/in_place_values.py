"""Plan column-scoped updates only when population identity proves row stability."""

from sqlglot import exp

from .incremental import reusable
from .shared_reads import prune_unused_ctes


def update_columns(previous, population, manifest, compiled, forced_measures=()):
    if (
        not previous
        or previous.get("storageMode") != "current"
        or previous.get("populationArtifactId") != population["artifactId"]
    ):
        return None
    changed = {
        c["measureId"]
        for c in compiled.schema.columns
        if c.get("measureId")
        and (
            c["measureId"] in forced_measures
            or not reusable(previous, manifest, c["measureId"])
        )
    }
    return [
        c["columnId"]
        for c in compiled.schema.columns
        if c.get("measureId") in changed
        or c.get("coverageFor") in changed
        or c.get("mixedFor") in changed
    ]


def update_query(compiled, key_ids, column_ids):
    """Prune preserved outputs and their joins before evaluating changed values."""
    query = compiled.query.copy()
    selected = set(key_ids) | set(column_ids)
    names = {c["name"] for c in compiled.schema.columns if c["columnId"] in selected}
    query.set("expressions", [e for e in query.expressions if e.alias_or_name in names])
    needed = {
        c.table for e in query.expressions for c in e.find_all(exp.Column) if c.table
    }
    joins = query.args.get("joins", [])
    # Compiler assembly guarantees a single row per native key for every joined
    # measure. Removing an unused output therefore cannot change cardinality.
    query.set("joins", [j for j in joins if j.this.alias_or_name in needed])
    return prune_unused_ctes(query).sql(dialect="clickhouse")
