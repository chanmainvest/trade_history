$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

uv run trade-history ingest statements
uv run trade-history ingest prices --sources stooq,yahoo
uv run trade-history ingest fx
uv run trade-history rebuild-views

