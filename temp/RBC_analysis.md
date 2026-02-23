Here's a breakdown and structured extraction of the financial statement data:

### Date Format

The institution uses a `MON DD` format for individual transaction dates (e.g., `JUNE 01`), and `MON DD, YYYY` for statement dates (e.g., `MAY 31, 2024`). The year for transactions is inferred from the statement header (e.g., `2024`).

### Transaction Format

1.  **Multi-line Transactions:** Yes, descriptions frequently span 2 or more lines. The initial line of a transaction usually contains the date, the start of the description, and all numerical values (quantity, price, debit/credit amounts). Subsequent lines continue the description.
2.  **Column Separators:** The data uses fixed-width column-like spacing, primarily with multiple spaces separating fields.
3.  **Section Headers:** "Account Activity" headers delineate transaction blocks.
4.  **Account Numbers:** Account numbers are formatted as `XXX-XXXXX-X-X` (e.g., `670-27469-2-3`) and appear in the page headers.
5.  **Special Formatting Quirks:**
    *   `\RATE` is used for the per-unit price.
    *   Negative quantities and amounts are indicated with a trailing hyphen (e.g., `20-`, `$32,870.00-`).
    *   `NRT` signifies "Non-Resident Tax Withheld" in USD dividend descriptions.
    *   Currency is determined by "Cdn. Dollar Statement" or "U.S. Dollar Statement" in the page header.
    *   Footnote markers (`#`, `²`) appear in the Asset Review section but are not part of transaction details.
    *   "Opening Balance" and "Closing Balance" lines are present within the "Account Activity" section but are not transactions.

### Regex Suggestions

The parsing strategy relies on a combination of regular expressions and string manipulation due to the fixed-width format and multi-line descriptions.

**Overall Structure Identification:**

*   **Year:** `^(?:Order Execution Only\s+)?(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+\d{1,2}\s+(?P<year>\d{4})$` (from top of page)
*   **Currency:** `^(?P<currency>Cdn\.\s+Dollar|U\.S\.\s+Dollar)\s+Statement$`
*   **Account Number:** `^Your Account Number:\s+(?P<account_id>\d{3}-\d{5}-\d-\d)$`
*   **Transaction Start Line:** `^(?P<month>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(?P<day>\d{1,2})\s+(?P<rest_of_line>.*)$` (to identify the start of a new transaction entry)
*   **Account Activity Header:** `^Account\s+Activity$`

**Parsing `rest_of_line` (after date, for first line of transaction):**

This requires splitting the string by multiple spaces and then attempting to parse from right to left for `debit`, `credit`, `price`, `quantity`, and `description`. A robust approach involves tokenizing and iteratively attempting to cast tokens to numbers.

**Example Tokenization (from `rest_of_line`):**
If `rest_of_line` is `BOUGHT GLOBAL X HIGH INT SVGS ETF 10,100  50.03  505,309.88`
Tokens: `['BOUGHT', 'GLOBAL', 'X', 'HIGH', 'INT', 'SVGS', 'ETF', '10,100', '50.03', '505,309.88']`

1.  **Amounts (Debit/Credit):** Look for the last numerical token(s).
    *   If one number at the end, determine if it's debit or credit based on keywords (`DEPOSIT`, `DIVIDEND` -> credit; `BOUGHT`, `WIRE TFR` -> debit).
    *   If two numbers appear in the typical amount columns (less common for a single transaction), assume the left is debit, right is credit.
    *   Pattern for a number: `^-?\d{1,3}(?:,\d{3})*(?:\.\d{2})?-?$` (to handle `1,234.56` or `123.45-`).

2.  **Quantity and Price:** After amounts are potentially removed from the `rest_of_line`, look for two numerical tokens before the remaining description.
    *   Pattern for price/quantity: `^\d{1,3}(?:,\d{3})*(?:\.\d+)?-?$` (similar to amounts but often without commas for price).

3.  **Symbol and Option Details:**
    *   **Options:** `(CALL|PUT)\s+\.(?P<symbol>[A-Z0-9.-]+)\s+(?P<expiry>\d{2}/\d{2}/\d{2})\s+(?P<strike>\d+(?:\.\d+)?)\s*`
    *   **General Symbols:** Look for common patterns like all-caps words or known symbols from the "Asset Review" section. This is heuristic and prone to error without a symbol dictionary.

### Sample Extractions

Below is the JSON array containing the extracted transaction data:

```json
[
  {
    "raw_line": "JUNE 01 DEPOSIT RBCRewards # 1117085895                                9.41",
    "date": "2024-06-01",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "DEPOSIT RBCRewards # 1117085895",
    "quantity": null,
    "price": null,
    "amount": 9.41,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 01 DEPOSIT RBCRewards # 1117085896                                9.41",
    "date": "2024-06-01",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "DEPOSIT RBCRewards # 1117085896",
    "quantity": null,
    "price": null,
    "amount": 9.41,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 01 DEPOSIT RBCRewards # 1117085897                                6.88",
    "date": "2024-06-01",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "DEPOSIT RBCRewards # 1117085897",
    "quantity": null,
    "price": null,
    "amount": 6.88,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 01 DEPOSIT RBCRewards # 1117085898                                9.41",
    "date": "2024-06-01",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "DEPOSIT RBCRewards # 1117085898",
    "quantity": null,
    "price": null,
    "amount": 9.41,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 04 BOUGHT GLOBAL X HIGH INT SVGS ETF 10,100  50.03  505,309.88",
    "date": "2024-06-04",
    "settlement_date": null,
    "action": "buy",
    "symbol": "GLOBAL X HIGH INT SVGS ETF CASH",
    "description": "BOUGHT GLOBAL X HIGH INT SVGS ETF",
    "quantity": 10100.0,
    "price": 50.03,
    "amount": -505309.88,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "CL A UNIT",
    "date": "2024-06-04",
    "settlement_date": null,
    "action": "buy",
    "symbol": "GLOBAL X HIGH INT SVGS ETF CASH",
    "description": "BOUGHT GLOBAL X HIGH INT SVGS ETF CL A UNIT UNSOLICITED DA",
    "quantity": 10100.0,
    "price": 50.03,
    "amount": -505309.88,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 05 DEPOSIT RBCRewards # 1119526596                                6.88",
    "date": "2024-06-05",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "DEPOSIT RBCRewards # 1119526596",
    "quantity": null,
    "price": null,
    "amount": 6.88,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 05 DIVIDEND SPROTT INC                      0.34012              850.31",
    "date": "2024-06-05",
    "settlement_date": null,
    "action": "dividend",
    "symbol": "SII",
    "description": "DIVIDEND SPROTT INC",
    "quantity": 2500.0,
    "price": 0.34012,
    "amount": 850.31,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "COM NEW",
    "date": "2024-06-05",
    "settlement_date": null,
    "action": "dividend",
    "symbol": "SII",
    "description": "DIVIDEND SPROTT INC COM NEW CASH DIV ON 2500 SHS REC 05/21/24 PAY 06/05/24",
    "quantity": 2500.0,
    "price": 0.34012,
    "amount": 850.31,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 10 WIRE TFR TRANSFER FUNDS TO RBC                    2,600.00",
    "date": "2024-06-10",
    "settlement_date": null,
    "action": "withdrawal",
    "symbol": null,
    "description": "WIRE TFR TRANSFER FUNDS TO RBC",
    "quantity": null,
    "price": null,
    "amount": -2600.00,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 19 DEPOSIT RBCRewards # 1124260596                                68.62",
    "date": "2024-06-19",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "DEPOSIT RBCRewards # 1124260596",
    "quantity": null,
    "price": null,
    "amount": 68.62,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 20 TRANSFER CALL .BCE 09/20/24 54      40",
    "date": "2024-06-20",
    "settlement_date": null,
    "action": "transfer",
    "symbol": "BCE",
    "description": "TRANSFER CALL .BCE 09/20/24 54",
    "quantity": 40.0,
    "price": null,
    "amount": null,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": true,
    "option_details": {
      "root": "BCE",
      "expiry": "09/20/24",
      "strike": 54.0,
      "put_call": "call"
    }
  },
  {
    "raw_line": "BCE INC NEW",
    "date": "2024-06-20",
    "settlement_date": null,
    "action": "transfer",
    "symbol": "BCE",
    "description": "TRANSFER CALL .BCE 09/20/24 54 BCE INC NEW MOVE FROM USD SIDE OF ACCOUNT",
    "quantity": 40.0,
    "price": null,
    "amount": null,
    "currency": "CAD",
    "account_id": "670-27469-2-3",
    "is_option": true,
    "option_details": {
      "root": "BCE",
      "expiry": "09/20/24",
      "strike": 54.0,
      "put_call": "call"
    }
  },
  {
    "raw_line": "JUNE 05 DIVIDEND VANGUARD INTERMEDIATE TERM      0.2992 179.52 NRT  1,196.80",
    "date": "2024-06-05",
    "settlement_date": null,
    "action": "dividend",
    "symbol": "VCIT",
    "description": "DIVIDEND VANGUARD INTERMEDIATE TERM 179.52 NRT",
    "quantity": 4000.0,
    "price": 0.2992,
    "amount": 1196.80,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "CORPORATE BOND ETF",
    "date": "2024-06-05",
    "settlement_date": null,
    "action": "dividend",
    "symbol": "VCIT",
    "description": "DIVIDEND VANGUARD INTERMEDIATE TERM CORPORATE BOND ETF CASH DIV ON 4000 SHS REC 06/03/24 PAY 06/05/24 NON-RES TAX WITHHELD",
    "quantity": 4000.0,
    "price": 0.2992,
    "amount": 1196.80,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 05 DIVIDEND VANGUARD SHORT TERM CORPORATE   0.258  143.19 NRT   954.60",
    "date": "2024-06-05",
    "settlement_date": null,
    "action": "dividend",
    "symbol": "VCSH",
    "description": "DIVIDEND VANGUARD SHORT TERM CORPORATE 143.19 NRT",
    "quantity": 3700.0,
    "price": 0.258,
    "amount": 954.60,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "BOND ETF",
    "date": "2024-06-05",
    "settlement_date": null,
    "action": "dividend",
    "symbol": "VCSH",
    "description": "DIVIDEND VANGUARD SHORT TERM CORPORATE BOND ETF CASH DIV ON 3700 SHS REC 06/03/24 PAY 06/05/24 NON-RES TAX WITHHELD",
    "quantity": 3700.0,
    "price": 0.258,
    "amount": 954.60,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 07 NONRES TX ISHARES IBOXX $ INVESTMENT                          258.87",
    "date": "2024-06-07",
    "settlement_date": null,
    "action": "other",
    "symbol": null,
    "description": "NONRES TX ISHARES IBOXX $ INVESTMENT",
    "quantity": null,
    "price": null,
    "amount": 258.87,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "GRADE CORPORATE BOND ETF",
    "date": "2024-06-07",
    "settlement_date": null,
    "action": "other",
    "symbol": null,
    "description": "NONRES TX ISHARES IBOXX $ INVESTMENT GRADE CORPORATE BOND ETF AS OF 06/07/24 REV QI TAX",
    "quantity": null,
    "price": null,
    "amount": 258.87,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 07 DIVIDEND ISHARES IBOXX $ INVESTMENT      0.4045  303.38 NRT  2,022.54",
    "date": "2024-06-07",
    "settlement_date": null,
    "action": "dividend",
    "symbol": null,
    "description": "DIVIDEND ISHARES IBOXX $ INVESTMENT 303.38 NRT",
    "quantity": 5000.0,
    "price": 0.4045,
    "amount": 2022.54,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "GRADE CORPORATE BOND ETF",
    "date": "2024-06-07",
    "settlement_date": null,
    "action": "dividend",
    "symbol": null,
    "description": "DIVIDEND ISHARES IBOXX $ INVESTMENT GRADE CORPORATE BOND ETF CASH DIV ON 5000 SHS REC 06/03/24 PAY 06/07/24 NON-RES TAX WITHHELD",
    "quantity": 5000.0,
    "price": 0.4045,
    "amount": 2022.54,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 17 DIVIDEND ISHARES INC                     0.35088  52.63 NRT   350.89",
    "date": "2024-06-17",
    "settlement_date": null,
    "action": "dividend",
    "symbol": "ISHARES MSCI SINGAPORE ETF",
    "description": "DIVIDEND ISHARES INC 52.63 NRT",
    "quantity": 1000.0,
    "price": 0.35088,
    "amount": 350.89,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "ISHARES MSCI SINGAPORE ETF",
    "date": "2024-06-17",
    "settlement_date": null,
    "action": "dividend",
    "symbol": "ISHARES MSCI SINGAPORE ETF",
    "description": "DIVIDEND ISHARES INC ISHARES MSCI SINGAPORE ETF CASH DIV ON 1000 SHS REC 06/11/24 PAY 06/17/24 NON-RES TAX WITHHELD",
    "quantity": 1000.0,
    "price": 0.35088,
    "amount": 350.89,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "JUNE 18 BOUGHT PUT TSLA 01/17/25 40         40    0.123     544.00",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "TSLA",
    "description": "BOUGHT PUT TSLA 01/17/25 40",
    "quantity": 40.0,
    "price": 0.123,
    "amount": -544.00,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": true,
    "option_details": {
      "root": "TSLA",
      "expiry": "01/17/25",
      "strike": 40.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "TESLA INC",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "TSLA",
    "description": "BOUGHT PUT TSLA 01/17/25 40 TESLA INC UNSOLICITED DA CLOSE CONTRACT",
    "quantity": 40.0,
    "price": 0.123,
    "amount": -544.00,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": true,
    "option_details": {
      "root": "TSLA",
      "expiry": "01/17/25",
      "strike": 40.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "JUNE 18 BOUGHT PUT TSLA 01/17/25 45         40     0.18     770.00",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "TSLA",
    "description": "BOUGHT PUT TSLA 01/17/25 45",
    "quantity": 40.0,
    "price": 0.18,
    "amount": -770.00,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": true,
    "option_details": {
      "root": "TSLA",
      "expiry": "01/17/25",
      "strike": 45.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "TESLA INC",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "TSLA",
    "description": "BOUGHT PUT TSLA 01/17/25 45 TESLA INC UNSOLICITED DA CLOSE CONTRACT",
    "quantity": 40.0,
    "price": 0.18,
    "amount": -770.00,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": true,
    "option_details": {
      "root": "TSLA",
      "expiry": "01/17/25",
      "strike": 45.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "JUNE 18 BOUGHT PUT TSLA 09/20/24 75         20     0.15     325.00",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "TSLA",
    "description": "BOUGHT PUT TSLA 09/20/24 75",
    "quantity": 20.0,
    "price": 0.15,
    "amount": -325.00,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": true,
    "option_details": {
      "root": "TSLA",
      "expiry": "09/20/24",
      "strike": 75.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "TESLA INC",
    "date": "2024-06-18",
    "settlement_date": null,
    "action": "buy",
    "symbol": "TSLA",
    "description": "BOUGHT PUT TSLA 09/20/24 75 TESLA INC UNSOLICITED DA CLOSE CONTRACT",
    "quantity": 20.0,
    "price": 0.15,
    "amount": -325.00,
    "currency": "USD",
    "account_id": "670-27469-2-3",
    "is_option": true,
    "option_details": {
      "root": "TSLA",
      "expiry": "09/20/24",
      "strike": 75.0,
      "put_call": "put"
    }
  }
]
```

### Parsing Notes

*   **Year Inference:** The year for transactions is assumed to be the year stated in the page header. This is typically consistent within a single statement.
*   **Currency Determination:** The currency (CAD/USD) is established at the page level by identifying "Cdn. Dollar Statement" or "U.S. Dollar Statement". All transactions following this header on the same page are assigned that currency.
*   **Account ID:** The account number is extracted once per page (or set of pages with the same header) and applied to all transactions within that scope.
*   **Multi-line Descriptions:** Lines that do not begin with a month (indicating a new transaction) are appended to the `description` of the preceding transaction. This assumes that all non-date-prefixed lines within an "Account Activity" block are continuations of a description. This heuristic may fail if there are other structured non-transaction lines.
*   **Amount and Quantity/Price Parsing:** This is the most complex part. The solution relies on:
    *   Identifying the rightmost numerical tokens as potential `debit` or `credit` amounts.
    *   Identifying numerical tokens preceding the amounts as potential `quantity` and `price`.
    *   Keywords (`DEPOSIT`, `DIVIDEND`, `BOUGHT`, `WIRE TFR`) are used to infer `action` and the sign of the `amount` (debit is negative, credit is positive).
    *   The `quantity` for dividends is derived from the "CASH DIV ON XXX SHS" part of the multi-line description.
*   **Options Parsing:** A specific regex is used to identify options contracts (`CALL`/`PUT`, symbol, expiry, strike) within the description.
*   **Symbol Extraction:** For non-option transactions, symbol extraction is heuristic, looking for common patterns or matching against a predefined list of symbols observed in the "Asset Review" sections. This is not exhaustive and can miss symbols or incorrectly identify parts of the description as symbols.
*   **`settlement_date`:** Not explicitly present in the provided transaction activity lines, so it's `null`.
*   **Opening/Closing Balances:** These lines are explicitly excluded from transaction parsing as they represent account summaries, not individual activities.