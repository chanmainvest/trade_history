# Financial Statement Analysis

## Date Format
The institution uses the `Mon DD, YYYY` format for period summaries (e.g., `Jun 30, 2024`) and `Mon DD` for transaction dates (e.g., `May 30`). The year for transactions is inferred from the statement period, which is 2024 for the provided data.

## Transaction Format
Transaction lines are largely fixed-width columns, but the "Description" field can vary significantly, sometimes leading to multi-line transactions. Column headers are: `Date Activity Description Quantity Price ($) Amount ($) balance ($)`. For dividend/distribution activities, the `Price ($)` column is often empty. The "Quantity" field for dividends refers to the number of shares held, not the dividend amount itself, while for options it represents the number of contracts.

### Regex Suggestions
-   **General Transaction Pattern (for options/buy/sell):**
    ```regex
    ^(?P<date>\w{3} \d{1,2})\s+(?P<action>Buy|Sell)\s+(?P<description>.*?)(?P<quantity>-?[\d,\.]+)\s+?(?P<price>[\d,\.]+)\s+(?P<amount>-?[\d,\.]+)\s+[\d,\.]+
    ```
-   **Dividend/Distribution Transaction Pattern (missing price):**
    ```regex
    ^(?P<date>\w{3} \d{1,2})\s+(?P<action>Dividend|Distribution|Dividends)\s+(?P<description>.*?)(?P<quantity>[\d,\.]+)\s+(?P<amount>-?[\d,\.]+)\s+[\d,\.]+
    ```
    *Note:* The final `[\d,\.]+` captures the balance but is not included in the named groups as it's not requested in the output JSON for each transaction. This pattern assumes the price column is simply skipped, causing the `Amount ($)` to shift left.

## Sample Extractions

```json
[
  {
    "raw_line": "May 30 Dividend TD DIV INCM-D /NL'FRAC 4.309 0.00 11,893.01",
    "date": "2024-05-30",
    "settlement_date": null,
    "action": "dividend",
    "symbol": null,
    "description": "TD DIV INCM-D /NL'FRAC Reinvestment Plan VALUE = 62.31",
    "quantity": 4.309,
    "price": null,
    "amount": 0.0,
    "currency": "CAD",
    "account_id": "58MRB0",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 4 Distribution BMO MID CORP BND INDX 3,200 160.00 12,053.01",
    "date": "2024-06-04",
    "settlement_date": null,
    "action": "distribution",
    "symbol": null,
    "description": "BMO MID CORP BND INDX ETF",
    "quantity": 3200.0,
    "price": null,
    "amount": 160.0,
    "currency": "CAD",
    "account_id": "58MRB0",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 18 Buy PUT -100 ENB'24 SP @data\\test-symbol-overrides-fab29abd3c7444b690e1fb5561c0a401.sqlite 10 0.340 -362.49 11,690.52",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "ENB",
    "description": "PUT -100 ENB'24 SP CLOSING TRANSACTION EXPIRES ON SEP 20,2024 MG-692051",
    "quantity": 10.0,
    "price": 0.34,
    "amount": -362.49,
    "currency": "CAD",
    "account_id": "58MRB0",
    "is_option": true,
    "option_details": {
      "put_call": "PUT",
      "strike": 100.0,
      "root": "ENB",
      "expiry": "2024-09-20"
    }
  },
  {
    "raw_line": "Jun 18 Buy CALL-100 CNQ'25 JA @data\\test-symbol-overrides-23e80b0a8d9d4f43908506b4f4efb36f.sqlite 20 2.100 -4,234.99 7,455.53",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "CNQ",
    "description": "CALL-100 CNQ'25 JA OPENING TRANSACTION EXPIRES ON JAN 17,2025 IZ-689169",
    "quantity": 20.0,
    "price": 2.1,
    "amount": -4234.99,
    "currency": "CAD",
    "account_id": "58MRB0",
    "is_option": true,
    "option_details": {
      "put_call": "CALL",
      "strike": 100.0,
      "root": "CNQ",
      "expiry": "2025-01-17"
    }
  },
  {
    "raw_line": "Jun 18 Sell PUT -100 CNQ'25 JA @.ruff_cache\\0.15.2\\400134734948792590 -40 1.000 3,940.01 11,395.54",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "sell",
    "symbol": "CNQ",
    "description": "PUT -100 CNQ'25 JA OPENING TRANS - UNCOVERED EXPIRES ON JAN 17,2025 YP-690956",
    "quantity": -40.0,
    "price": 1.0,
    "amount": 3940.01,
    "currency": "CAD",
    "account_id": "58MRB0",
    "is_option": true,
    "option_details": {
      "put_call": "PUT",
      "strike": 100.0,
      "root": "CNQ",
      "expiry": "2025-01-17"
    }
  },
  {
    "raw_line": "Jun 25 Dividends SUNCOR ENERGY INC NEW 1,600 872.00 12,267.54",
    "date": "2024-06-25",
    "settlement_date": null,
    "action": "dividend",
    "symbol": null,
    "description": "SUNCOR ENERGY INC NEW",
    "quantity": 1600.0,
    "price": null,
    "amount": 872.0,
    "currency": "CAD",
    "account_id": "58MRB0",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 28 Dividends CANADIAN NATIONAL 305 257.73 12,525.27",
    "date": "2024-06-28",
    "settlement_date": null,
    "action": "dividend",
    "symbol": null,
    "description": "CANADIAN NATIONAL RAILWAY",
    "quantity": 305.0,
    "price": null,
    "amount": 257.73,
    "currency": "CAD",
    "account_id": "58MRB0",
    "is_option": false,
    "option_details": {}
  }
]
```

## Parsing Notes
-   **Multi-line Transactions:** Transactions can span multiple lines, with additional descriptive text or details (like "CLOSING TRANSACTION" or "EXPIRES ON...") appearing on subsequent lines. These lines are associated with the preceding transaction and merged into the `description` field.
-   **Column Separators:** Fixed-width columns are used, with fields delimited by multiple spaces. This sometimes causes issues where a column might be omitted (e.g., `Price ($)` for dividends), shifting subsequent columns.
-   **Section Headers:** Key transaction-related headers include "Activity in your account this period" and "Holdings in your account".
-   **Account Number Formatting:** Account numbers are 6-character alphanumeric (e.g., `58MRB0`). They appear in page headers.
-   **Special Formatting Quirks:**
    -   Placeholder file paths (e.g., `@data\test-symbol-overrides-...`, `@.ruff_cache\...`) are present in some descriptions. These are likely artifacts from the PDF extraction process and have been stripped.
    -   `SEG` appears after quantities in holdings, possibly indicating "segregated," but is not explicitly parsed as a separate field in this output.
    -   Ticker symbols for equities are often enclosed in parentheses, e.g., `(BMO )`. For options, the root symbol is extracted from the option description.
    -   Option symbols like `CALL-100 CNQ'25 JA` indicate `put_call`, `strike` (implied from the `-100` pattern), `root`, `expiry_year`, and a non-standard `expiry_month` code. The expiry date is preferentially extracted from multi-line `EXPIRES ON ...` statements. The `expiry_month_code` (e.g., `JA`, `SP`) is used as a fallback if an explicit expiry date is not found. Based on the provided examples, `JA` maps to January and `SP` to September.
    -   The "Quantity" column in the activity section is context-dependent: for options, it's the number of contracts; for dividends, it seems to refer to the number of shares that *received* the dividend, not the dividend quantity itself. For dividends, the actual dividend amount is in the `amount` field.
    -   `settlement_date` is not explicitly present in the provided activity lines.
```