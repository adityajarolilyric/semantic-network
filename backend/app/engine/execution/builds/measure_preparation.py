from ....core.errors import Conflict
from ....schemas.decision_view import DecisionViewEdit
from ....warehouse.current_storage import STORAGE_POLICY
from ...decision.edits import MeasureEditCompiler
from ...measures.compiler import MeasureCompiler
from ...sql.contracts import Check, ExecutionPlan
from ...transformations.dates.inputs import calendar_source_ids
from ..inputs.captured import CapturedRunInputs
from ..inputs.resolution_profiles import prepare_resolution_profiles
from .contracts import PreparedMeasureBuild
from .incremental import IncrementalMeasureCompiler


class MeasureBuildPreparation:
    def __init__(self, builds):
        self.builds = builds

    async def prepare(self, run, worker):
        builds = self.builds
        await builds.storage.recover(run["modelId"], run["revisionId"])
        if run.get("storagePolicy") != STORAGE_POLICY:
            raise Conflict(
                "This build predates the table-storage upgrade. Submit a new build."
            )
        builds.incremental.validate_runtime_policy(run)
        captured_inputs = await CapturedRunInputs.load(
            builds.store.repository, run["runId"], builds.max_objects * 6 + 3
        )
        docs = captured_inputs.documents
        objects = captured_inputs.graph
        populations = captured_inputs.populations
        managed = captured_inputs.managed_artifacts
        managed_definitions = captured_inputs.managed_definitions
        previous = captured_inputs.single("__previous")
        manifests = captured_inputs.materialisation_manifests or run.get(
            "materialisationManifests", {}
        )
        snapshots = captured_inputs.value_snapshots
        if run.get("reuseOnly"):
            await builds.incremental.reuse(run, worker, snapshots, manifests)
            return
        needed_transactions = None
        if run.get("buildMode") == "incremental":
            needed_transactions = {
                o["definition"]["transactionId"]
                for o in objects
                if o["kind"] == "measure"
                and o["objectId"] in run["dirtyMeasures"]
                and (o["definition"]["measureType"] == "referenced")
            }
        required_sources = set(managed)
        if needed_transactions is not None:
            required_sources = {
                o["definition"]["sourceId"]
                for o in objects
                if o["kind"] == "dimensionMembers"
            }
            for tx in objects:
                if (
                    tx["kind"] == "transaction"
                    and tx["objectId"] in needed_transactions
                ):
                    required_sources.add(tx["definition"]["sourceId"])
                    required_sources.update(calendar_source_ids(objects, tx))
        await builds.managed.input_verifier.verify(
            objects,
            {
                source_id: artifact
                for source_id, artifact in managed.items()
                if source_id in required_sources
            },
            managed_definitions,
        )
        await builds.storage.ensure_inputs([*populations.values(), *snapshots.values()])
        for population in populations.values():
            for check in population.get("checks", []):
                await builds.executor.checks(
                    ExecutionPlan(None, (Check(check["code"], check["sql"]),), None)
                )
        edit_document = next(
            (d["document"] for d in docs if d["inputId"] == "__edit"), None
        )
        if run.get("isEdit"):
            if edit_document is None:
                raise Conflict(
                    "The edit job context is missing. Refresh and submit a new edit."
                )
            await builds.storage.ensure_inputs(list(snapshots.values()))
            compiler = MeasureEditCompiler(
                builds.settings.clickhouse_database,
                objects,
                populations,
                managed,
                run.get("baseDimensionId", run["resourceId"]),
                snapshots,
                DecisionViewEdit.model_validate(edit_document),
            )
        else:
            resolution_profiles = await prepare_resolution_profiles(
                builds.executor.warehouse,
                [
                    o
                    for o in objects
                    if o["kind"] != "transaction"
                    or needed_transactions is None
                    or o["objectId"] in needed_transactions
                ],
                managed,
                builds.settings.resolution_max_profiles,
                builds.executor.execution_settings(),
            )
            compiler_class = (
                IncrementalMeasureCompiler
                if run.get("buildMode") == "incremental"
                else MeasureCompiler
            )
            compiler = compiler_class(
                builds.settings.clickhouse_database,
                objects,
                populations,
                managed,
                run.get("baseDimensionId", run["resourceId"]),
                previous,
                resolution_profiles,
                builds.settings.resolution_max_rows,
                **{
                    "snapshots": snapshots,
                    "manifests": manifests,
                    "forced_measures": run.get("forcedMeasures", []),
                }
                if run.get("buildMode") == "incremental"
                else {},
            )
        compiler.max_nodes = builds.settings.measure_max_nodes
        compiler.native_workset = (
            set(run["dirtyMeasures"])
            if run.get("buildMode") == "incremental" and (not run.get("isEdit"))
            else None
        )
        return PreparedMeasureBuild(
            objects, populations, snapshots, previous, manifests, compiler
        )
