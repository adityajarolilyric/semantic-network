import asyncio

from backend.app.core.config import Settings
from backend.app.engine.worker import EngineWorker
from backend.app.warehouse.dimension_execution import DimensionTableExecutor
from backend.tests.memory_store import MemoryStore


async def test_job_query_budgets_are_isolated_and_do_not_change_defaults():
    settings = Settings(_env_file=None)
    executor = DimensionTableExecutor(None, settings)
    observed = {}

    class Handler:
        async def execute_run(self, run, worker):
            before = executor.execution_settings()["max_memory_usage"]
            await asyncio.sleep(0.001)
            observed[run["queryMemoryBytes"]] = (
                before,
                executor.execution_settings()["max_memory_usage"],
            )

    worker = EngineWorker(MemoryStore(), handlers={"budget": Handler()}, collector=None)
    await asyncio.gather(
        *(
            worker.execute({"resourceKind": "budget", "queryMemoryBytes": budget})
            for budget in [1024**3, 2 * 1024**3]
        )
    )
    assert observed == {budget: (budget, budget) for budget in [1024**3, 2 * 1024**3]}
    assert (
        executor.execution_settings()["max_memory_usage"]
        == settings.planning_max_bytes
        == 512 * 1024**2
    )
