from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_root: Path = Path(__file__).resolve().parents[2]
    statements_root: Path = Path("Statements")
    sqlite_path: Path = Path("data/trading.sqlite")
    duckdb_path: Path = Path("data/market.duckdb")
    parser_quarantine_csv: Path = Path("data/quarantine/unmapped_transactions.csv")
    default_currency: str = "CAD"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    auth_mode: Literal["none", "oauth"] = "none"
    auth_oauth_issuer: str | None = None
    auth_oauth_audience: str | None = None
    auth_oauth_jwks_url: str | None = None
    auth_oauth_algorithms: str = "RS256"
    auth_read_scope: str = "trade_history.read"
    auth_write_scope: str = "trade_history.write"

    @field_validator("statements_root", "sqlite_path", "duckdb_path", "parser_quarantine_csv", mode="before")
    @classmethod
    def _expand_path(cls, value: str | Path) -> Path:
        return Path(value)


settings = Settings()
