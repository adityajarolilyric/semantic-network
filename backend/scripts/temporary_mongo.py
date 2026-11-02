"""An isolated loopback replica set for browser tests; never uses developer data."""

from contextlib import contextmanager
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

from pymongo import MongoClient

from test_integration import wait_until


@contextmanager
def temporary_mongo():
    executable = shutil.which("mongod")
    if not executable:
        raise RuntimeError("Install mongod to run the metadata browser suite.")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="semantic-ui-mongo-") as directory:
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
                "metadata-ui-rs",
                "--nounixsocket",
                "--logpath",
                str(Path(directory) / "mongo.log"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        try:
            with MongoClient(
                f"mongodb://127.0.0.1:{port}/?directConnection=true",
                serverSelectionTimeoutMS=500,
            ) as client:
                wait_until(lambda: client.admin.command("ping").get("ok"))
                client.admin.command(
                    "replSetInitiate",
                    {
                        "_id": "metadata-ui-rs",
                        "members": [{"_id": 0, "host": f"127.0.0.1:{port}"}],
                    },
                )
                wait_until(
                    lambda: client.admin.command("hello").get("isWritablePrimary")
                )
            yield f"mongodb://127.0.0.1:{port}/?replicaSet=metadata-ui-rs"
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
