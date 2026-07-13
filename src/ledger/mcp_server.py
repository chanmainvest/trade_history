"""MCP server for AI-agent control of ledger operations."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import httpx

from . import config
from .api.routes.config import get_config, put_config

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - exercised when optional dependency is absent
    FastMCP = None  # type: ignore[assignment]

_ALLOWED_GET_PATHS = {
    "/health",
    "/transactions",
    "/transactions/accounts",
    "/transactions/symbols",
    "/transactions/txn-types",
    "/transactions/latest-date",
    "/monthly/snapshot",
    "/monthly/diff",
    "/performance/total",
    "/performance/cash",
    "/research/prices",
    "/research/trades",
    "/research/financials",
    "/viz/holdings_by_sector",
    "/viz/correlation",
    "/viz/rrg",
    "/statements",
    "/config",
}

_ALLOWED_CLI_COMMANDS = {
    "db_init",
    "pdf_dump_samples",
    "pdf_dump_all",
    "ingest_run",
    "ingest_infer_initials",
    "ingest_repair_symbols",
    "ingest_reconcile",
    "market_refresh",
    "market_refresh_profiles",
    "market_refresh_dividends",
    "market_refresh_splits",
    "market_refresh_financials",
    "market_refresh_earnings",
    "market_refresh_fx",
    "market_refresh_benchmarks",
    "market_refresh_all",
}


def _normalize_api_path(path: str) -> str:
    cleaned = "/" + (path or "").strip().lstrip("/")
    if cleaned.startswith("/api/"):
        cleaned = cleaned[4:]
    return cleaned


def _api_path_allowed(path: str) -> bool:
    return path in _ALLOWED_GET_PATHS


def _truncate(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _run_ledger(args: list[str], timeout_seconds: int) -> dict[str, Any]:
    timeout = min(max(int(timeout_seconds or 600), 1), 7200)
    command = [sys.executable, "-m", "ledger.cli", *args]
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            command,
            cwd=str(config.ROOT),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "command": "ledger " + " ".join(args),
            "timeout_seconds": timeout,
            "stdout": _truncate(exc.stdout or ""),
            "stderr": _truncate(exc.stderr or ""),
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": "ledger " + " ".join(args),
        "stdout": _truncate(proc.stdout),
        "stderr": _truncate(proc.stderr),
    }


def _build_cli_args(
    command: str,
    *,
    institution: str | None,
    limit: int | None,
    symbols: list[str] | None,
    lookback_years: int,
    per_folder: int,
) -> list[str] | None:
    command = command.strip()
    if command == "db_init":
        return ["db", "init"]
    if command == "pdf_dump_samples":
        return ["pdf", "dump-samples", "--per-folder", str(per_folder)]
    if command == "pdf_dump_all":
        args = ["pdf", "dump-all"]
        if institution:
            args.extend(["--institution", institution])
        return args
    if command == "ingest_run":
        args = ["ingest", "run"]
        if institution:
            args.extend(["--institution", institution])
        if limit is not None:
            args.extend(["--limit", str(limit)])
        return args
    if command == "ingest_infer_initials":
        return ["ingest", "infer-initials"]
    if command == "ingest_repair_symbols":
        return ["ingest", "repair-symbols"]
    if command == "ingest_reconcile":
        return ["ingest", "reconcile"]
    if command == "market_refresh":
        args = ["market", "refresh", "--lookback-years", str(lookback_years)]
        for symbol in symbols or []:
            args.extend(["--symbol", symbol.upper()])
        return args
    if command == "market_refresh_profiles":
        return ["market", "refresh-profiles"]
    if command == "market_refresh_dividends":
        return ["market", "refresh-dividends"]
    if command == "market_refresh_splits":
        return ["market", "refresh-splits"]
    if command == "market_refresh_financials":
        return ["market", "refresh-financials"]
    if command == "market_refresh_earnings":
        return ["market", "refresh-earnings"]
    if command == "market_refresh_fx":
        return ["market", "refresh-fx", "--lookback-years", str(lookback_years)]
    if command == "market_refresh_benchmarks":
        args = ["market", "refresh-benchmarks", "--lookback-years", str(lookback_years)]
        for symbol in symbols or []:
            args.extend(["--symbol", symbol.upper()])
        return args
    if command == "market_refresh_all":
        return ["market", "refresh-all", "--lookback-years", str(lookback_years)]
    return None


def create_server() -> Any:
    if FastMCP is None:
        raise RuntimeError("The MCP SDK is not installed. Run `uv sync` after pulling this change.")

    server = FastMCP("ledger")

    @server.tool()
    def frontend_routes(base_url: str = "http://localhost:5173") -> dict[str, Any]:
        """Return the frontend routes an agent can open for browser work."""
        routes = [
            "/transactions",
            "/monthly",
            "/performance",
            "/research",
            "/viz",
            "/config",
        ]
        return {"base_url": base_url.rstrip("/"), "routes": [base_url.rstrip("/") + r for r in routes]}

    @server.tool()
    def frontend_config_get() -> dict[str, Any]:
        """Read the same user preferences shown in the Settings tab."""
        return get_config()

    @server.tool()
    def frontend_config_update(payload: dict[str, Any]) -> dict[str, Any]:
        """Update Settings-tab preferences such as portfolio, theme, and language."""
        return put_config(payload)

    @server.tool()
    def api_get(
        path: str,
        params: dict[str, Any] | None = None,
        base_url: str = "http://127.0.0.1:8000",
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Call an allowlisted Ledger API GET endpoint."""
        api_path = _normalize_api_path(path)
        if not _api_path_allowed(api_path):
            return {"ok": False, "error": "API path is not allowlisted", "path": api_path}
        url = base_url.rstrip("/") + api_path
        try:
            response = httpx.get(url, params=params or {}, timeout=timeout_seconds)
        except httpx.HTTPError as exc:
            return {"ok": False, "url": url, "error": str(exc)}
        out: dict[str, Any] = {"ok": response.is_success, "status_code": response.status_code, "url": str(response.url)}
        try:
            out["body"] = response.json()
        except ValueError:
            out["body"] = response.text
        return out

    @server.tool()
    def ledger_cli(
        command: str,
        institution: str | None = None,
        limit: int | None = None,
        symbols: list[str] | None = None,
        lookback_years: int = 15,
        per_folder: int = 2,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        """Run an allowlisted backend CLI operation without shell access."""
        if command not in _ALLOWED_CLI_COMMANDS:
            return {
                "ok": False,
                "error": "Unknown or disallowed command",
                "allowed_commands": sorted(_ALLOWED_CLI_COMMANDS),
            }
        args = _build_cli_args(
            command,
            institution=institution,
            limit=limit,
            symbols=symbols,
            lookback_years=lookback_years,
            per_folder=per_folder,
        )
        if args is None:
            return {"ok": False, "error": "Command could not be built", "command": command}
        return _run_ledger(args, timeout_seconds)

    return server


def serve() -> None:
    create_server().run()
