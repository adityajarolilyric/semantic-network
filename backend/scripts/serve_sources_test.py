"""Playwright API server with isolated real MongoDB and ClickHouse databases."""

from contextlib import asynccontextmanager
from pathlib import Path
import os
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import clickhouse_connect
import uvicorn

from backend.app.core.config import Settings
from backend.tests.fixture_app import create_fixture_app as create_app
from temporary_mongo import temporary_mongo


def serve(mongo_uri):
    port = os.getenv("METADATA_TEST_CH_PORT")
    if not port:
        raise SystemExit("Use the isolated CSV integration runner with --with-ui.")
    host = os.getenv("METADATA_TEST_CH_HOST", "127.0.0.1")
    password = os.getenv("METADATA_TEST_CH_PASSWORD", "")
    database = "csv_ui_test_" + uuid4().hex
    settings = Settings(
        _env_file=None,
        environment="test",
        api_key=None,
        mongo_uri=mongo_uri,
        mongo_database="semantic_ui_" + uuid4().hex,
        clickhouse_host=host,
        clickhouse_port=int(port),
        clickhouse_password=password,
        clickhouse_database=database,
    )
    app = create_app(settings)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        admin = clickhouse_connect.get_client(
            host=host, port=int(port), username="default", password=password
        )
        admin.command(f"CREATE DATABASE `{database}`")
        try:
            async with original(application):
                yield
        finally:
            assert database.startswith("csv_ui_test_")
            admin.command(f"DROP DATABASE IF EXISTS `{database}` SYNC")
            admin.close()

    app.router.lifespan_context = lifespan
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.getenv("METADATA_UI_TEST_API_PORT", "8798")),
        log_level="warning",
    )


if __name__ == "__main__":
    with temporary_mongo() as mongo_uri:
        serve(mongo_uri)
