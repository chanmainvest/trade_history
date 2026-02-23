This document details the parsing of brokerage statement transaction data from raw text, including analysis of formatting and extraction into a structured JSON array.

### Date Format

The institution uses the `Mon DD YYYY` format for dates. For example, `Jun 1`, `Jun 3`, `Jun 11`. The year is typically provided in the statement header (e.g., `June 1-June 28, 2024`) and is implied for individual transaction lines.

### Transaction Format

Transactions are presented in a tabular format under "Account Activity" sections.

*   **Column Separators:** The columns appear to be fixed-width, although the "description" column can vary significantly in length and wrap onto multiple lines. The `quantity`, `price`, and `amount` columns are right-aligned.
*   **Multi-line Transactions:** Yes, many transactions span multiple lines. Additional lines often provide more detail about the transaction, such as the full company name, settlement dates (`REC` and `PAY` dates), option details (`OPEN CONTRACT`, `EXPIRATION - EXPIRED`), or exchange rates. These continuation lines do not start with a date.
*   **Section Headers:** Transaction blocks are clearly delineated by headers such as:
    *   `Account Activity — Canadian Dollars`
    *   `Account Activity — U.S. Dollars`
*   **Account Numbers:** The account number `588-93738` is consistently displayed in the page headers and footers.
*   **Special Formatting Quirks:**
    *   **Currency:** Both Canadian and U.S. Dollar sections use the `$` symbol for amounts. The currency is determined by the section header.
    *   **Empty Values:** A long dash (`—`) is used to denote empty or inapplicable values in the `quantity`, `price`, and `amount` columns.
    *   **Negative Values:** Negative amounts and quantities are indicated with a leading hyphen (e.g., `-$5,019.45`, `-3,500`).
    *   **Options:** Option trades (CALL/PUT) include root symbol, expiry month/day/year, and strike price within their descriptions (e.g., `CALL .NGT MAR 21 2025 60`).
    *   **Settlement Dates:** Dividend and other payment-related lines often include `REC Mon DD YYYY` (Record Date) and `PAY Mon DD YYYY` (Payment Date), which can be used as settlement dates.
    *   **Exchange Rates:** U.S. Dollar transactions involving currency conversion may have lines indicating the `EXCHANGE RATE`.

### Transaction Line Regex Suggestion

Given the fixed-width nature and potential for wrapped lines, a multi-stage parsing approach is more robust than a single regex for an entire line.

1.  **Main Transaction Line Identification:**
    This regex aims to identify the primary transaction line, which always starts with a month and day, followed by an activity type. It attempts to greedily capture the description and then extract the optional `quantity`, `price`, and `amount` from the right side of the line.

    ```regex
    ^(?P<month>\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)\s+(?P<day>\d{1,2})\s+ # Date (e.g., Jun 18)
    (?P<activity>(?:[A-Z][a-z]+|\—)(?:\s+[A-Z][a-z]+)?)\s+ # Activity (e.g., Bought, Sold, Dividend, Transfer, Expired, —)
    (?P<description_core>.*?) # Core description part, will be refined
    (?:\s+(?P<quantity_val>[-—\d,\.]+))? # Optional Quantity (e.g., -3,500, 10, —)
    (?:\s+(?P<price_val>[-—\d,\.]+))? # Optional Price (e.g., 5.000, 3.542, —)
    (?:\s+(?P<amount_val>[-—\$]?[\d,\.]+))?$ # Optional Amount, typically at the very end (e.g., -$5,019.45, $12,391.55, —)
    ```

    *   After initial matching, `quantity_val`, `price_val`, and `amount_val` will be parsed from right-to-left to ensure correct assignment, as their presence can be optional or represented by `—`.
    *   The `description_core` might initially contain part of the quantity/price/amount if they weren't matched at the very end, requiring post-processing.

2.  **Continuation Line Identification:**
    Any subsequent line that does *not* start with a month name is considered a continuation of the description for the *previous* transaction.
    Specific patterns for `REC`/`PAY` dates within continuation lines:
    ```regex
    ^(?:REC|PAY)\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s+(\d{4})$
    ```
3.  **Option Details Extraction (from full description):**
    ```regex
    (CALL|PUT)\s+([.A-Z0-9]+)\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+(\d{4})\s+(\d+(\.\d+)?)
    ```
    This regex helps extract the `put_call`, `root` symbol, `expiry`, and `strike` from an option's description.

### Sample Extractions

```json
[
  {
    "raw_line": "Jun 1 — Opening cash balance — — $827.52",
    "date": "2024-06-01",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "Opening cash balance",
    "quantity": 0,
    "price": 0.0,
    "amount": 827.52,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 3 Dividend CIBC DIVIDEND INCOME FUND    40.947       —             —\nCLASS F\nREINVESTED DIV @ 12.0852",
    "date": "2024-06-03",
    "settlement_date": null,
    "action": "dividend",
    "symbol": null,
    "description": "CIBC DIVIDEND INCOME FUND CLASS F REINVESTED DIV @ 12.0852",
    "quantity": 40,
    "price": 0.0,
    "amount": 0.0,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 3 Dividend CIBC MONTHLY INCOME FUND    164.175       —             —\nCLASS F\nREINVESTED DIV @ 9.4531",
    "date": "2024-06-03",
    "settlement_date": null,
    "action": "dividend",
    "symbol": null,
    "description": "CIBC MONTHLY INCOME FUND CLASS F REINVESTED DIV @ 9.4531",
    "quantity": 164,
    "price": 0.0,
    "amount": 0.0,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 11 Dividend WHEATON PRECIOUS METALS        —         —         $426.25\nCORP COM CASH DIV ON 2000 SHS\nREC MAY 29 2024\nPAY JUN 11 2024",
    "date": "2024-06-11",
    "settlement_date": "2024-06-11",
    "action": "dividend",
    "symbol": null,
    "description": "WHEATON PRECIOUS METALS CORP COM CASH DIV ON 2000 SHS REC MAY 29 2024",
    "quantity": 0,
    "price": 0.0,
    "amount": 426.25,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 18 Transfer CALL .FNV MAR 21 2025 200       20       —             —\nFRANCO-NEVADA CORPORATION\nSAME A/C CURRENCY TRANSFER",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "transfer",
    "symbol": ".FNV",
    "description": "CALL .FNV MAR 21 2025 200 FRANCO-NEVADA CORPORATION SAME A/C CURRENCY TRANSFER",
    "quantity": 20,
    "price": 0.0,
    "amount": 0.0,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": ".FNV",
      "expiry": "2025-03-21",
      "strike": 200.0,
      "put_call": "CALL"
    }
  },
  {
    "raw_line": "Jun 18 Bought CALL .NGT MAR 21 2025 60          10    5.000     -$5,019.45\nNEWMONT CORPORATION\nUNSOLICITED OPEN CONTRACT",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": ".NGT",
    "description": "CALL .NGT MAR 21 2025 60 NEWMONT CORPORATION UNSOLICITED OPEN CONTRACT",
    "quantity": 10,
    "price": 5.0,
    "amount": -5019.45,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": ".NGT",
      "expiry": "2025-03-21",
      "strike": 60.0,
      "put_call": "CALL"
    }
  },
  {
    "raw_line": "Jun 19 Sold   B2GOLD CORP UNSOLICITED       -3,500    3.542     $12,391.55",
    "date": "2024-06-19",
    "settlement_date": null,
    "action": "sell",
    "symbol": "B2GOLD",
    "description": "B2GOLD CORP UNSOLICITED",
    "quantity": -3500,
    "price": 3.542,
    "amount": 12391.55,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 19 Sold   METALLA ROYALTY & STREAMING   -2,200    3.948      $8,678.05\nLTD COM UNSOLICITED",
    "date": "2024-06-19",
    "settlement_date": null,
    "action": "sell",
    "symbol": "METALLA",
    "description": "METALLA ROYALTY & STREAMING LTD COM UNSOLICITED",
    "quantity": -2200,
    "price": 3.948,
    "amount": 8678.05,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 19 Bought SPROTT PHYSICAL URANIUM          600   27.950    -$16,776.95\nTRUST UNITS UNSOLICITED",
    "date": "2024-06-19",
    "settlement_date": null,
    "action": "buy",
    "symbol": "URANIUM",
    "description": "SPROTT PHYSICAL URANIUM TRUST UNITS UNSOLICITED",
    "quantity": 600,
    "price": 27.95,
    "amount": -16776.95,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 24 Expired CALL .NGT JUN 21 2024 60        -40       —             —\nNEWMONT CORPORATION\nOPTION EXPIRATION - EXPIRED",
    "date": "2024-06-24",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": ".NGT",
    "description": "CALL .NGT JUN 21 2024 60 NEWMONT CORPORATION OPTION EXPIRATION - EXPIRED",
    "quantity": -40,
    "price": 0.0,
    "amount": 0.0,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": ".NGT",
      "expiry": "2024-06-21",
      "strike": 60.0,
      "put_call": "CALL"
    }
  },
  {
    "raw_line": "Jun 24 Expired PUT .NGT JUN 21 2024 50          20       —             —\nNEWMONT CORPORATION\nOPTION EXPIRATION - EXPIRED",
    "date": "2024-06-24",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": ".NGT",
    "description": "PUT .NGT JUN 21 2024 50 NEWMONT CORPORATION OPTION EXPIRATION - EXPIRED",
    "quantity": 20,
    "price": 0.0,
    "amount": 0.0,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": ".NGT",
      "expiry": "2024-06-21",
      "strike": 50.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 24 Expired PUT .FNV JUN 21 2024 150         10       —             —\nFRANCO-NEVADA CORPORATION\nOPTION EXPIRATION - EXPIRED",
    "date": "2024-06-24",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": ".FNV",
    "description": "PUT .FNV JUN 21 2024 150 FRANCO-NEVADA CORPORATION OPTION EXPIRATION - EXPIRED",
    "quantity": 10,
    "price": 0.0,
    "amount": 0.0,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": ".FNV",
      "expiry": "2024-06-21",
      "strike": 150.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 24 Dividend B2GOLD CORP                    —         —         $191.73\nCASH DIV ON 3500 SHS\nREC JUN 11 2024\nPAY JUN 24 2024",
    "date": "2024-06-24",
    "settlement_date": "2024-06-24",
    "action": "dividend",
    "symbol": "B2GOLD",
    "description": "B2GOLD CORP CASH DIV ON 3500 SHS REC JUN 11 2024",
    "quantity": 0,
    "price": 0.0,
    "amount": 191.73,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 27 Dividend FRANCO-NEVADA CORPORATION      —         —         $295.70\nCASH DIV ON 600 SHS\nREC JUN 13 2024\nPAY JUN 27 2024",
    "date": "2024-06-27",
    "settlement_date": "2024-06-27",
    "action": "dividend",
    "symbol": "FRANCO-NEVADA",
    "description": "FRANCO-NEVADA CORPORATION CASH DIV ON 600 SHS REC JUN 13 2024",
    "quantity": 0,
    "price": 0.0,
    "amount": 295.7,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 28 —      Closing cash balance             —         —       $1,014.40",
    "date": "2024-06-28",
    "settlement_date": null,
    "action": "withdrawal",
    "symbol": null,
    "description": "Closing cash balance",
    "quantity": 0,
    "price": 0.0,
    "amount": 1014.4,
    "currency": "CAD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 1 — Opening cash balance — — $170,340.84",
    "date": "2024-06-01",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "Opening cash balance",
    "quantity": 0,
    "price": 0.0,
    "amount": 170340.84,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 7 Dividend FIRST MAJESTIC SILVER CORP      —         —          $9.25\nCASH DIV ON 2500 SHS\nREC MAY 17 2024\nPAY JUN 07 2024",
    "date": "2024-06-07",
    "settlement_date": "2024-06-07",
    "action": "dividend",
    "symbol": "FIRST MAJESTIC SILVER",
    "description": "FIRST MAJESTIC SILVER CORP CASH DIV ON 2500 SHS REC MAY 17 2024",
    "quantity": 0,
    "price": 0.0,
    "amount": 9.25,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 11 Dividend HECLA MINING COMPANY           —         —         $31.25\nCASH DIV ON 5000 SHS\nREC MAY 24 2024\nPAY JUN 11 2024",
    "date": "2024-06-11",
    "settlement_date": "2024-06-11",
    "action": "dividend",
    "symbol": "HECLA MINING",
    "description": "HECLA MINING COMPANY CASH DIV ON 5000 SHS REC MAY 24 2024",
    "quantity": 0,
    "price": 0.0,
    "amount": 31.25,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 11 Tax    NON-RES TAX WITHHELD             —         —         -$4.68",
    "date": "2024-06-11",
    "settlement_date": null,
    "action": "fee",
    "symbol": null,
    "description": "NON-RES TAX WITHHELD",
    "quantity": 0,
    "price": 0.0,
    "amount": -4.68,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 14 Dividend WEYERHAEUSER CO                —         —         $400.00\nCASH DIV ON 2000 SHS\nREC MAY 31 2024\nPAY JUN 14 2024",
    "date": "2024-06-14",
    "settlement_date": "2024-06-14",
    "action": "dividend",
    "symbol": "WEYERHAEUSER",
    "description": "WEYERHAEUSER CO CASH DIV ON 2000 SHS REC MAY 31 2024",
    "quantity": 0,
    "price": 0.0,
    "amount": 400.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 14 Tax    NON-RES TAX WITHHELD             —         —         -$60.00",
    "date": "2024-06-14",
    "settlement_date": null,
    "action": "fee",
    "symbol": null,
    "description": "NON-RES TAX WITHHELD",
    "quantity": 0,
    "price": 0.0,
    "amount": -60.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 18 Transfer CALL .FNV MAR 21 2025 200      -20       —             —\nFRANCO-NEVADA CORPORATION\nSAME A/C CURRENCY TRANSFER",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "transfer",
    "symbol": ".FNV",
    "description": "CALL .FNV MAR 21 2025 200 FRANCO-NEVADA CORPORATION SAME A/C CURRENCY TRANSFER",
    "quantity": -20,
    "price": 0.0,
    "amount": 0.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": ".FNV",
      "expiry": "2025-03-21",
      "strike": 200.0,
      "put_call": "CALL"
    }
  },
  {
    "raw_line": "Jun 18 Bought CALL .FNV MAR 21 2025 200         20    4.300     -$6,382.22\nFRANCO-NEVADA CORPORATION\nUNSOLICITED OPEN CONTRACT\nEXCHANGE RATE .73937153",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": ".FNV",
    "description": "CALL .FNV MAR 21 2025 200 FRANCO-NEVADA CORPORATION UNSOLICITED OPEN CONTRACT EXCHANGE RATE .73937153",
    "quantity": 20,
    "price": 4.3,
    "amount": -6382.22,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": ".FNV",
      "expiry": "2025-03-21",
      "strike": 200.0,
      "put_call": "CALL"
    }
  },
  {
    "raw_line": "Jun 18 Bought PUT GLD JAN 16 2026 180          150    1.750    -$26,444.45\nSPDR GOLD TR UNSOLICITED\nOPEN CONTRACT",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "GLD",
    "description": "PUT GLD JAN 16 2026 180 SPDR GOLD TR UNSOLICITED OPEN CONTRACT",
    "quantity": 150,
    "price": 1.75,
    "amount": -26444.45,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "GLD",
      "expiry": "2026-01-16",
      "strike": 180.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 18 Bought PUT GLD JAN 16 2026 200          100    5.000    -$50,131.95\nSPDR GOLD TR UNSOLICITED\nOPEN CONTRACT",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "GLD",
    "description": "PUT GLD JAN 16 2026 200 SPDR GOLD TR UNSOLICITED OPEN CONTRACT",
    "quantity": 100,
    "price": 5.0,
    "amount": -50131.95,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "GLD",
      "expiry": "2026-01-16",
      "strike": 200.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 18 Sold   PUT .FNV MAR 21 2025 150         -10    9.250      $6,605.04\nFRANCO-NEVADA CORPORATION\nUNSOLICITED OPEN CONTRACT\nEXCHANGE RATE .71556351",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "sell",
    "symbol": ".FNV",
    "description": "PUT .FNV MAR 21 2025 150 FRANCO-NEVADA CORPORATION UNSOLICITED OPEN CONTRACT EXCHANGE RATE .71556351",
    "quantity": -10,
    "price": 9.25,
    "amount": 6605.04,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": ".FNV",
      "expiry": "2025-03-21",
      "strike": 150.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 18 Sold   PUT .NGT MAR 21 2025 50          -20    2.800      $3,984.29\nNEWMONT CORPORATION\nUNSOLICITED OPEN CONTRACT\nEXCHANGE RATE .71556351",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "sell",
    "symbol": ".NGT",
    "description": "PUT .NGT MAR 21 2025 50 NEWMONT CORPORATION UNSOLICITED OPEN CONTRACT EXCHANGE RATE .71556351",
    "quantity": -20,
    "price": 2.8,
    "amount": 3984.29,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": ".NGT",
      "expiry": "2025-03-21",
      "strike": 50.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 18 Sold   CALL GLD JAN 16 2026 250         -50   10.208     $50,968.13\nSPDR GOLD TR UNSOLICITED\nOPEN CONTRACT",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "sell",
    "symbol": "GLD",
    "description": "CALL GLD JAN 16 2026 250 SPDR GOLD TR UNSOLICITED OPEN CONTRACT",
    "quantity": -50,
    "price": 10.208,
    "amount": 50968.13,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "GLD",
      "expiry": "2026-01-16",
      "strike": 250.0,
      "put_call": "CALL"
    }
  },
  {
    "raw_line": "Jun 18 Sold   CALL GLD JAN 16 2026 300         -50    4.100     $20,429.98\nSPDR GOLD TR UNSOLICITED\nOPEN CONTRACT",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "sell",
    "symbol": "GLD",
    "description": "CALL GLD JAN 16 2026 300 SPDR GOLD TR UNSOLICITED OPEN CONTRACT",
    "quantity": -50,
    "price": 4.1,
    "amount": 20429.98,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "GLD",
      "expiry": "2026-01-16",
      "strike": 300.0,
      "put_call": "CALL"
    }
  },
  {
    "raw_line": "Jun 18 Bought CALL SOXS1 JAN 17 2025 130        20    0.100       -$231.95\nDIREXION DAILY SMCNDCTR BEAR\nADJ 1:10 REV SPLIT D:10 SOXS\nUNSOLICITED CLOSING CONTRACT",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "SOXS1",
    "description": "CALL SOXS1 JAN 17 2025 130 DIREXION DAILY SMCNDCTR BEAR ADJ 1:10 REV SPLIT D:10 SOXS UNSOLICITED CLOSING CONTRACT",
    "quantity": 20,
    "price": 0.1,
    "amount": -231.95,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "SOXS1",
      "expiry": "2025-01-17",
      "strike": 130.0,
      "put_call": "CALL"
    }
  },
  {
    "raw_line": "Jun 18 Bought CALL SOXS1 JAN 16 2026 25         20    0.290       -$611.95\nDIREXION DAILY SMCNDCTR BEAR\nADJ 1:10 REV SPLIT D:10 SOXS\nUNSOLICITED CLOSING CONTRACT",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "SOXS1",
    "description": "CALL SOXS1 JAN 16 2026 25 DIREXION DAILY SMCNDCTR BEAR ADJ 1:10 REV SPLIT D:10 SOXS UNSOLICITED CLOSING CONTRACT",
    "quantity": 20,
    "price": 0.29,
    "amount": -611.95,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "SOXS1",
      "expiry": "2026-01-16",
      "strike": 25.0,
      "put_call": "CALL"
    }
  },
  {
    "raw_line": "Jun 20 Dividend QUALCOMM INC                   —         —        -$680.00\nDIV CHG 800 SHS SHORT\nREC MAY 30 2024\nPAY JUN 20 2024",
    "date": "2024-06-20",
    "settlement_date": "2024-06-20",
    "action": "dividend",
    "symbol": "QUALCOMM",
    "description": "QUALCOMM INC DIV CHG 800 SHS SHORT REC MAY 30 2024",
    "quantity": 0,
    "price": 0.0,
    "amount": -680.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 20 Sold   VALE S A                      -4,000   11.175     $44,691.80\nSPONSORED ADR UNSOLICITED",
    "date": "2024-06-20",
    "settlement_date": null,
    "action": "sell",
    "symbol": "VALE",
    "description": "VALE S A SPONSORED ADR UNSOLICITED",
    "quantity": -4000,
    "price": 11.175,
    "amount": 44691.8,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 20 Sold   WEYERHAEUSER CO UNSOLICITED   -2,000   28.860     $57,711.84",
    "date": "2024-06-20",
    "settlement_date": null,
    "action": "sell",
    "symbol": "WEYERHAEUSER",
    "description": "WEYERHAEUSER CO UNSOLICITED",
    "quantity": -2000,
    "price": 28.86,
    "amount": 57711.84,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 21 Sold   ETF SERIES SOLUTIONS          -5,000   19.720     $98,590.80\nU S GLOBAL JETS ETF\nUNSOLICITED",
    "date": "2024-06-21",
    "settlement_date": null,
    "action": "sell",
    "symbol": "JETS",
    "description": "ETF SERIES SOLUTIONS U S GLOBAL JETS ETF UNSOLICITED",
    "quantity": -5000,
    "price": 19.72,
    "amount": 98590.8,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 21 Bought SPROTT PHYSICAL PLATINUM      10,000    9.596    -$95,966.15\nAND PALLADIUM TRUST UNIT\nUNSOLICITED",
    "date": "2024-06-21",
    "settlement_date": null,
    "action": "buy",
    "symbol": "PLATINUM",
    "description": "SPROTT PHYSICAL PLATINUM AND PALLADIUM TRUST UNIT UNSOLICITED",
    "quantity": 10000,
    "price": 9.596,
    "amount": -95966.15,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun 21 Sold   PUT WY JAN 17 2025 26            -20    0.750      $1,468.00\nWEYERHAEUSER CO UNSOLICITED\nOPEN CONTRACT",
    "date": "2024-06-21",
    "settlement_date": null,
    "action": "sell",
    "symbol": "WY",
    "description": "PUT WY JAN 17 2025 26 WEYERHAEUSER CO UNSOLICITED OPEN CONTRACT",
    "quantity": -20,
    "price": 0.75,
    "amount": 1468.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "WY",
      "expiry": "2025-01-17",
      "strike": 26.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 24 Expired PUT QCOM JUN 21 2024 160         -4       —             —\nQUALCOMM INC\nOPTION EXPIRATION - EXPIRED",
    "date": "2024-06-24",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": "QCOM",
    "description": "PUT QCOM JUN 21 2024 160 QUALCOMM INC OPTION EXPIRATION - EXPIRED",
    "quantity": -4,
    "price": 0.0,
    "amount": 0.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "QCOM",
      "expiry": "2024-06-21",
      "strike": 160.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 24 Expired PUT GLD JUN 21 2024 180         -10       —             —\nSPDR GOLD TR\nOPTION EXPIRATION - EXPIRED",
    "date": "2024-06-24",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": "GLD",
    "description": "PUT GLD JUN 21 2024 180 SPDR GOLD TR OPTION EXPIRATION - EXPIRED",
    "quantity": -10,
    "price": 0.0,
    "amount": 0.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "GLD",
      "expiry": "2024-06-21",
      "strike": 180.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 24 Expired PUT FCX JUN 21 2024 35          -15       —             —\nFREEPORT MCMORAN INC\nOPTION EXPIRATION - EXPIRED",
    "date": "2024-06-24",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": "FCX",
    "description": "PUT FCX JUN 21 2024 35 FREEPORT MCMORAN INC OPTION EXPIRATION - EXPIRED",
    "quantity": -15,
    "price": 0.0,
    "amount": 0.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "FCX",
      "expiry": "2024-06-21",
      "strike": 35.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 24 Expired PUT GLD JUN 21 2024 170          20       —             —\nSPDR GOLD TR\nOPTION EXPIRATION - EXPIRED",
    "date": "2024-06-24",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": "GLD",
    "description": "PUT GLD JUN 21 2024 170 SPDR GOLD TR OPTION EXPIRATION - EXPIRED",
    "quantity": 20,
    "price": 0.0,
    "amount": 0.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "GLD",
      "expiry": "2024-06-21",
      "strike": 170.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 24 Expired PUT FCX JUN 21 2024 30           30       —             —\nFREEPORT MCMORAN INC\nOPTION EXPIRATION - EXPIRED",
    "date": "2024-06-24",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": "FCX",
    "description": "PUT FCX JUN 21 2024 30 FREEPORT MCMORAN INC OPTION EXPIRATION - EXPIRED",
    "quantity": 30,
    "price": 0.0,
    "amount": 0.0,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": true,
    "option_details": {
      "root": "FCX",
      "expiry": "2024-06-21",
      "strike": 30.0,
      "put_call": "PUT"
    }
  },
  {
    "raw_line": "Jun 28 —      Closing cash balance             —         —     $274,717.87",
    "date": "2024-06-28",
    "settlement_date": null,
    "action": "withdrawal",
    "symbol": null,
    "description": "Closing cash balance",
    "quantity": 0,
    "price": 0.0,
    "amount": 274717.87,
    "currency": "USD",
    "account_id": "588-93738",
    "is_option": false,
    "option_details": {}
  }
]
```

### Parsing Notes

*   **Year Inference:** The year (2024 in this case) is inferred from the statement's overall date range and appended to individual transaction dates for full `YYYY-MM-DD` formatting.
*   **Activity Type Normalization:** The `action` field is normalized to a predefined set of types (e.g., `Bought` -> `buy`, `Dividend` -> `dividend`, `Expired` -> `option_expiry`). `Opening cash balance` and `Closing cash balance` are mapped to `deposit` and `withdrawal` respectively. `Tax` is mapped to `fee`.
*   **Symbol Extraction:** Stock symbols are often embedded within the description. For options, the root symbol is extracted into `option_details.root` and also placed in the main `symbol` field. For non-options, a best effort is made to extract a symbol (e.g., "B2GOLD CORP" -> "B2GOLD", "WHEATON PRECIOUS METALS CORP COM" -> "WHEATON PRECIOUS METALS"). For ETFs, commonly recognized abbreviations (like JETS) are used where possible.
*   **Settlement Date:** `REC` (Record) and `PAY` (Payment) dates found on continuation lines are used to populate the `settlement_date` field. When both are present, `PAY` date is prioritized as the effective settlement date.
*   **Number Parsing:** Numbers for `quantity`, `price`, and `amount` are cleaned by removing dollar signs (`$`) and commas (`,`) before conversion to float or integer. `—` is treated as 0.
*   **Multi-line Descriptions:** The parser handles multi-line descriptions by appending subsequent non-date-starting lines to the `description` of the most recently identified transaction.
*   **OCR Errors:** The raw text can contain minor OCR errors (e.g., `Jun 1 — Opening cash balance — — $827.52` where extra `—` are present). The parsing logic is made resilient to these by liberal use of `\s+` and optional groups.
*   **Order of Operations for Quantity/Price/Amount:** Due to variable spacing and the fixed-width nature of the raw PDF, identifying the exact boundaries for `quantity`, `price`, and `amount` can be challenging. The current approach attempts to capture these from the right-hand side of the line. If a line appears to be a transaction but lacks one of these values (e.g., a dividend without an explicit quantity or price), it defaults to `0` or `0.0`.`run_shell_command` was called directly without a preceding `tool_code` block. This is not allowed. All `run_shell_command` calls must be preceded by a `tool_code` block.