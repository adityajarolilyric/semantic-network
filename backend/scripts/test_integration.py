"""Run integration tests with an isolated, temporary local mongod replica set."""

from pathlib import Path
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from pymongo import MongoClient
from pymongo.errors import PyMongoError


def wait_until(callback, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if callback():
                return
        except PyMongoError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Temporary MongoDB did not become ready")


def main():
    executable = shutil.which("mongod")
    if not executable:
        raise SystemExit(
            "Install mongod or run pytest with METADATA_TEST_MONGO_URI pointing to a test replica set."
        )
    root = Path(__file__).resolve().parents[2]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(
        prefix="semantic-metadata-integration-"
    ) as directory:
        process = subprocess.Popen(
            [
                executable,
                "--dbpath",
                directory,
                "--port",
                str(port),
                "--bind_ip",
                "127.0.0.1",
                "--replSet",
                "metadata-test-rs",
                "--nounixsocket",
                "--logpath",
                str(Path(directory) / "mongo.log"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        try:
            direct_uri = f"mongodb://127.0.0.1:{port}/?directConnection=true"
            with MongoClient(direct_uri, serverSelectionTimeoutMS=500) as client:
                wait_until(lambda: client.admin.command("ping").get("ok"))
                client.admin.command(
                    "replSetInitiate",
                    {
                        "_id": "metadata-test-rs",
                        "members": [{"_id": 0, "host": f"127.0.0.1:{port}"}],
                    },
                )
                wait_until(
                    lambda: client.admin.command("hello").get("isWritablePrimary")
                )
            env = {
                **os.environ,
                "METADATA_TEST_MONGO_URI": f"mongodb://127.0.0.1:{port}/?replicaSet=metadata-test-rs",
            }
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                "backend/pytest.ini",
                "backend/tests/integration",
                "-m",
                "integration",
                "-q",
            ]
            return subprocess.call(command, cwd=root, env=env)
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
