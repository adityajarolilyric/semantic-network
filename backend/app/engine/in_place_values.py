"""Plan column-scoped updates only when population identity proves row stability."""

from graphlib import CycleError, TopologicalSorter

from sqlglot import exp

from ..core.errors import MetadataError

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


def write_groups(artifact, changed):
    """Group independent changed measures; checkpoint dependencies first."""
    mids = {
        c.get("measureId") or c.get("coverageFor") or c.get("mixedFor") for c in changed
    }
    pending = [mid for mid in artifact["measureOrder"] if mid in mids]
    if mids != set(pending):
        raise MetadataError(
            "measure_write_plan", "The write plan omits a changed measure."
        )
    dependencies = artifact.get("measureDependencies")
    if dependencies is None:
        return [[mid] for mid in pending]
    selected = set(pending)
    sorter = TopologicalSorter(
        {mid: selected.intersection(dependencies.get(mid, [])) for mid in pending}
    )
    try:
        sorter.prepare()
    except CycleError as error:
        raise MetadataError(
            "measure_dependency_cycle", "Cyclic measure write dependencies."
        ) from error
    groups = []
    while sorter.is_active():
        ready = set(sorter.get_ready())
        group = [mid for mid in pending if mid in ready]
        groups.append(group)
        sorter.done(*group)
    return groups
