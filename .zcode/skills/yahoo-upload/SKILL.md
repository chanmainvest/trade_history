---
name: yahoo-upload
description: Upload the Trade History portfolio export to Yahoo Finance's My
  Portfolio via browser automation. Use whenever the user asks to push/update/import
  the yahoo CSV to Yahoo Finance, sync the portfolio to Yahoo, or upload
  yahoo_portfolio_*.csv to their Yahoo account. Logs in with the YAHOO_USER /
  YAHOO_PASSWORD credentials from the repo-root .env file.
---

# Upload the portfolio CSV to Yahoo Finance

Drives a real browser to sign in to Yahoo Finance and import the CSV produced by
`GET /export/yahoo-csv` (the Monthly tab's "Export Yahoo CSV" button) into a
Yahoo Finance portfolio.

## Security rules (non-negotiable)

1. Credentials come ONLY from the repo-root `.env` (`YAHOO_USER`,
   `YAHOO_PASSWORD`; see `.env.example`). Never write them into source, tests,
   fixtures, logs, or this skill.
2. Load them without printing: run a shell check that outputs only
   yes/no, e.g. `test -f .env && grep -q '^YAHOO_USER=' .env && echo ready`.
   In the browser node REPL, read the file and keep values in variables; never
   `nodeRepl.write` them, never include them in screenshots (type into the
   password field and screenshot only *before* typing or after submit).
3. The only permitted transmission of the credential values is typing them
   into Yahoo's own sign-in form during this run. Never attach, paste, or
   upload the `.env` file itself — to Yahoo or anywhere else.
4. If Yahoo asks for a verification code / 2FA / "unusual activity" challenge,
   STOP and ask the user to complete that step themselves — never guess codes.
5. Uploading to Yahoo is an outward-facing action: confirm the target
   portfolio and the CSV with the user before the import step, and get
   explicit confirmation before any delete-and-recreate of an existing
   portfolio.
6. `.env` is gitignored. If it is missing, tell the user to create it from
   `.env.example` and stop.

## Step 1 — Obtain the CSV

Accept an explicit CSV path from the user. If none was given:

1. If the app API is running (verify "Trade History API" in
   `http://127.0.0.1:<port>/api/health` first — see the dev-server runbook; the
   working pair is usually API 8002 + Vite 5173), fetch it:
   `curl -sS -o temp/yahoo_portfolio.csv "http://127.0.0.1:<port>/api/export/yahoo-csv?account_id=<ids>"`
   (omit `account_id` for all accounts). Keep the file under `temp/`.
2. Otherwise use the newest `yahoo_portfolio_*.csv` the user exported from the
   Monthly tab.

Sanity-check the file: first line must be exactly
`Symbol,Trade Date,Purchase Price,Quantity`; note the data-row count for the
final verification.

## Step 2 — Drive the browser

Load the `browser-use:control-browser` skill and follow it. Known quirks from
this repo's sessions: locator-based clicks can time out — prefer dom_cua
`node_id` clicks; capture screenshots as `temp/*.png` and read them with the
Read tool; crop screenshots to avoid tripping the content filter.

1. Open `https://finance.yahoo.com` and click **Sign in**.
2. Enter `YAHOO_USER` → **Next**, then `YAHOO_PASSWORD` → **Sign in**.
3. Dismiss consent/cookie/ad dialogs as they appear (they are frequent).
4. Go to **My Portfolio**:
   - To create a fresh portfolio: click **Start Creating** in the
     **"Import a CSV"** box, give the portfolio a name, and upload the CSV.
   - To update an existing portfolio: open it and use its **Import** /
     upload-lots action (the icon next to "Holdings"), or delete-and-recreate
     if the user asked for a clean replace — confirm before deleting anything.
5. Upload the CSV file via the file input and confirm the import dialog.
6. **Verify**: the portfolio's holdings row count should match the CSV data-row
   count. Yahoo silently drops rows whose symbol it cannot resolve — compare
   the visible symbol list against the CSV symbols and report any that are
   missing (these correspond to bad Yahoo mappings; fix them via the app's
   Yahoo symbol resolution workflow, then re-export and re-upload).

## Step 3 — Report

Summarize: file uploaded, portfolio name used, number of holdings imported,
symbols Yahoo rejected (if any), and whether a re-upload is needed. Never
include credentials or full page screenshots of the login form in the report.
