"""Test CSV ingestion with a disposable ClickHouse container and isolated databases."""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-ui",
        action="store_true",
        help="Also run Playwright against the real CSV service and ClickHouse.",
    )
    parser.add_argument(
        "--measures-only",
        action="store_true",
        help="Run only measure execution integration tests.",
    )
    parser.add_argument(
        "--ui-only",
        action="store_true",
        help="Skip Python tests and run only the browser suite (requires --with-ui).",
    )
    args = parser.parse_args()
    if args.ui_only and not args.with_ui:
        parser.error("--ui-only requires --with-ui")
    root = Path(__file__).resolve().parents[2]
    name = "semantic-csv-test-" + uuid4().hex[:12]
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--publish",
            "127.0.0.1::8123",
            "--env",
            "CLICKHOUSE_SKIP_USER_SETUP=1",
            "--ulimit",
            "nofile=65536:65536",
            "clickhouse/clickhouse-server:25.3",
        ],
        check=True,
    )
    try:
        address = (
            subprocess.check_output(["docker", "port", name, "8123/tcp"], text=True)
            .strip()
            .splitlines()[0]
        )
        port = address.rsplit(":", 1)[1]
        deadline = time.monotonic() + 45
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/ping", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, TimeoutError):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Temporary ClickHouse did not become ready")
            time.sleep(0.2)
        env = {
            **os.environ,
            "METADATA_TEST_CH_HOST": "127.0.0.1",
            "METADATA_TEST_CH_PORT": port,
            "METADATA_TEST_CH_PASSWORD": "",
        }
        result = 0
        if not args.ui_only:
            result = subprocess.call(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-c",
                    "backend/pytest.ini",
                    *(
                        ["backend/tests/integration/test_measures_clickhouse.py"]
                        if args.measures_only
                        else [
                            str(path.relative_to(root))
                            for path in sorted(
                                (root / "backend/tests/integration").glob(
                                    "*_clickhouse.py"
                                )
                            )
                        ]
                    ),
                    "-m",
                    "integration",
                    "-q",
                ],
                cwd=root,
                env=env,
            )
        if result == 0 and args.with_ui:
            result = subprocess.call(
                ["npm", "run", "test:e2e"], cwd=root / "application", env=env
            )
        return result
    finally:
        subprocess.run(["docker", "stop", "--time", "10", name], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
