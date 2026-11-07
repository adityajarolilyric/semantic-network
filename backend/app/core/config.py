from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="METADATA_",
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    app_name: str = "semantic-metadata-v1"
    mongo_uri: SecretStr = SecretStr(
        "mongodb://127.0.0.1:27018/?replicaSet=metadata-rs"
    )
    mongo_database: str = Field(
        default="semantic_metadata_v1", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,62}$"
    )
    mongo_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    mongo_pool_size: int = Field(default=50, ge=1, le=500)
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=8_388_608)
    max_objects_per_revision: int = Field(default=5000, ge=1, le=10000)
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ]
    )
    api_key: SecretStr | None = None
    environment: Literal["development", "test", "production"] = "development"
    clickhouse_host: str = "127.0.0.1"
    clickhouse_port: int = Field(default=8123, ge=1, le=65535)
    clickhouse_username: str = "default"
    clickhouse_password: SecretStr = SecretStr("semantic")
    clickhouse_database: str = Field(
        default="semantic", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
    )
    clickhouse_secure: bool = False
    clickhouse_timeout_sec: int = Field(default=30, ge=1, le=120)
    csv_max_rows: int = Field(default=5000, ge=1, le=5000)
    csv_max_file_bytes: int = Field(default=10_485_760, ge=1024, le=20_971_520)

    planning_max_rows: int = Field(default=1_000_000, ge=1, le=100_000_000)
    planning_max_bytes: int = Field(default=536_870_912, ge=1048576)

    measure_max_nodes: int = Field(default=1024, ge=10, le=10000)
    measure_max_sql_bytes: int = Field(default=4_194_304, ge=65536, le=16777216)

    decision_view_max_groups: int = Field(default=5000, ge=1, le=50000)
    decision_view_max_cells: int = Field(default=50000, ge=1, le=200000)

    clickhouse_max_threads: int = Field(default=4, ge=1, le=32)

    resolution_max_profiles: int = Field(default=64, ge=1, le=256)
    resolution_max_rows: int = Field(default=1000000, ge=1, le=10000000)
