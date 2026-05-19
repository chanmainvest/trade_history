# Example dataset

This is a **synthetic** ledger meant to demo what the app can do without
exposing any real personal data.

## What's inside

* `data/ledger.sqlite` — 2 accounts (TD Direct Investing + Interactive
  Brokers), ~37 fictional transactions stretching from 2021 to 2026, plus a
  current snapshot dated 2026-05-11 that mirrors the
  `portfolio_dashboard/sample_portfolio.xlsx` holdings.
* `data/market.duckdb` — empty. Run `ledger market scrape` to populate it
  from yfinance.
* `Statements/` — placeholder folders (`TD Direct Investing/`,
  `Interactive Brokers/`). No PDFs are shipped; the data is loaded directly
  via `scripts/build_example_data.py`.

## Reproducing

```powershell
$env:LEDGER_PROFILE = "example"
uv run python scripts/build_example_data.py
```

## Switching profiles

The app reads `LEDGER_PROFILE` at startup:

```powershell
$env:LEDGER_PROFILE = "example"   # uses example_data/
uv run ledger serve
# vs
$env:LEDGER_PROFILE = "real"      # default — uses Statements/ + data/
uv run ledger serve
```

You can also point `LEDGER_DATA_DIR` and `LEDGER_STATEMENTS_DIR` at any
folders you want.
