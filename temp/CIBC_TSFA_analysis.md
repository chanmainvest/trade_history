Here's an analysis of the provided financial statement text and the extracted transactions.

### Date Format

The institution uses the following date formats:
*   **Statement Period/Contextual Dates**: `Month DD, YYYY` (e.g., `June 1-June 30, 2022`, `May 31, 2022`, `May 6, 2022`).
*   **Transaction Dates**: `Mon DD` (e.g., `Jun 1`, `Jun 7`, `Jun 15`, `Jun 30`). The year for these dates must be inferred from the statement period.
*   **Record/Payment Dates within description**: `Mon DD YYYY` (e.g., `REC MAY 31 2022`, `PAY JUN 07 2022`).

### Transaction Format

1.  **Multi-line transactions**: Yes, transaction descriptions can span multiple lines. Subsequent lines belonging to the same transaction do not start with a date and are indented or simply continue the text.
2.  **Column Separators**: Fixed-width spacing is used to separate the quantity, price, and amount columns. The earlier parts of the line (date, activity, description) use variable spacing, but the financial figures are consistently right-aligned or positioned.
3.  **Section Headers**: Transaction blocks are preceded by headers such as:
    *   `Account Activity — Canadian Dollars`
    *   `Account Activity — Canadian Dollars (continued)`
4.  **Account Numbers**: Account numbers are formatted as `XXX-XXXXX` (e.g., `605-82155`). They appear in page headers and sometimes in direct relation to the account holder's name.
5.  **Special Formatting Quirks**:
    *   `—` (em dash) is used as a placeholder for empty quantity or price fields.
    *   Negative amounts are indicated by a leading hyphen (e.g., `-$25,731.95`).
    *   Currency is explicitly stated as "Canadian Dollars" in section headers, and amounts are prefixed with `$`. No explicit "CAD" or "USD" appears next to individual transaction amounts, implying the currency of the section.
    *   The "activity" field can sometimes be `—` (e.g., `Opening cash balance`) or implicitly empty (e.g., `GRANITE REAL ESTATE` for the tax withheld line).

#### Regex Suggestions

For identifying a new transaction line:
`^(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<day>\d{1,2})`

For parsing a transaction line (assuming the current year is known):

```regex
# This regex attempts to capture the main fields based on observed fixed-width positions.
# It splits the line into fixed-width chunks for date, activity/description, quantity, price, and amount.
# Further parsing is then applied to each chunk.

# The first part captures the date (e.g., "Jun 1" or "Jun 15")
date_pattern = r"^(?P<date_raw>.{6})"

# The last three parts capture quantity, price, and amount based on character position from the right.
# These indices are approximate based on the sample data provided:
# Quantity: chars 56-62
# Price: chars 63-71
# Amount: chars 72-end
# The middle section (chars 6-55) is the activity and description.

# Example slicing:
# date_raw = line[0:6].strip()
# activity_description_raw = line[6:55].strip()
# quantity_raw = line[56:62].strip()
# price_raw = line[63:71].strip()
# amount_raw = line[72:].strip()

# After initial slicing, further regex or splitting can be used for 'activity_description_raw':
# ^(?P<activity>[^\s—-]+|—)?\s*(?P<description>.*)
```

### Sample Extractions

```json
[
  {
    "raw_line": "Jun 1 —       Opening cash balance             —         —      $26,730.35",
    "date": "2022-06-01",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "Opening cash balance",
    "quantity": null,
    "price": null,
    "amount": 26730.35,
    "currency": "CAD",
    "account_id": "605-82155",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 7 Dividend RIOCAN REAL ESTATE              —         —         $42.50",
    "date": "2022-06-07",
    "settlement_date": "2022-06-07",
    "action": "dividend",
    "symbol": "REI.UN",
    "description": "RIOCAN REAL ESTATE INVESTMENT TRUST UNI DIST ON 500 SHS REC MAY 31 2022 PAY JUN 07 2022",
    "quantity": null,
    "price": null,
    "amount": 42.5,
    "currency": "CAD",
    "account_id": "605-82155",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 15 Dividend BOARDWALK REAL ESTATE INVT     —         —         $18.00",
    "date": "2022-06-15",
    "settlement_date": "2022-06-15",
    "action": "dividend",
    "symbol": "BEI.UN",
    "description": "BOARDWALK REAL ESTATE INVT TRUST UNITS DIST ON 200 SHS REC MAY 31 2022 PAY JUN 15 2022",
    "quantity": null,
    "price": null,
    "amount": 18.0,
    "currency": "CAD",
    "account_id": "605-82155",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 15 Dividend CANADIAN APARTMENT PPTYS       —         —         $48.33",
    "date": "2022-06-15",
    "settlement_date": "2022-06-15",
    "action": "dividend",
    "symbol": "CAR.UN",
    "description": "CANADIAN APARTMENT PPTYS REAL ESTATE INVT TRUST UTS DIST ON 400 SHS REC MAY 31 2022 PAY JUN 15 2022",
    "quantity": null,
    "price": null,
    "amount": 48.33,
    "currency": "CAD",
    "account_id": "605-82155",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 15        GRANITE REAL ESTATE              —         —         -$0.18",
    "date": "2022-06-15",
    "settlement_date": "2022-06-15",
    "action": "fee",
    "symbol": "GRT.UN",
    "description": "GRANITE REAL ESTATE INVESTMENT TRUST STAPLED UNIT NON-RES TAX WITHHELD WHTAX ON US SOURCE DIV REC MAY 31 2022 PAY JUN 15 2022",
    "quantity": null,
    "price": null,
    "amount": -0.18,
    "currency": "CAD",
    "account_id": "605-82155",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 15 Dividend GRANITE REAL ESTATE            —         —         $38.75",
    "date": "2022-06-15",
    "settlement_date": "2022-06-15",
    "action": "dividend",
    "symbol": "GRT.UN",
    "description": "GRANITE REAL ESTATE INVESTMENT TRUST STAPLED UNIT DIST ON 150 SHS REC MAY 31 2022 PAY JUN 15 2022",
    "quantity": null,
    "price": null,
    "amount": 38.75,
    "currency": "CAD",
    "account_id": "605-82155",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 15 Bought SUNCOR ENERGY INC UNSOLICITED    500   51.450    -$25,731.95",
    "date": "2022-06-15",
    "settlement_date": null,
    "action": "buy",
    "symbol": "SU",
    "description": "SUNCOR ENERGY INC UNSOLICITED",
    "quantity": 500.0,
    "price": 51.45,
    "amount": -25731.95,
    "currency": "CAD",
    "account_id": "605-82155",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 30 —      Closing cash balance             —         —       $1,145.80",
    "date": "2022-06-30",
    "settlement_date": null,
    "action": "other",
    "symbol": null,
    "description": "Closing cash balance",
    "quantity": null,
    "price": null,
    "amount": 1145.8,
    "currency": "CAD",
    "account_id": "605-82155",
    "is_option": false,
    "option_details": null
  }
]
```

### Parsing Notes

*   **Year Inference**: Transaction dates (`Mon DD`) require the year to be inferred from the statement period (e.g., `June 1-June 30, 2022` implies the year is 2022).
*   **Symbol Extraction**: Ticker symbols like `REI.UN/TSX` or `SU/TSX` are often found within parentheses or in the following lines of multi-line descriptions in the "Portfolio Assets" section. For the "Account Activity" section, these are generally inferred from the description. I have inferred some common symbols for the dividend and bought transactions where the full name was provided and the symbol was found later in the "Portfolio Assets" section (e.g., RIOCAN REAL ESTATE -> REI.UN). A more robust parser would need to map full company names to symbols or use an external data source.
*   **Action Mapping**: The `action` field is derived from keywords in the `activity` or `description` (e.g., "Dividend", "Bought", "Opening cash balance" -> "deposit", "NON-RES TAX WITHHELD" -> "fee").
*   **Settlement Date**: The `settlement_date` is extracted when phrases like "PAY JUN 15 2022" are present in the multi-line description. If not explicitly found, it's left as `null`.
*   **Option Transactions**: While the "Portfolio Assets" section contains an option (`CALL .BCE JAN 19 2024 50`), there are no explicit option *transactions* within the "Account Activity" sections provided. Therefore, `is_option` is `false` and `option_details` is `null` for all extracted transactions. If option transactions were present, specific parsing for `root`, `expiry`, `strike`, and `put_call` would be needed.
*   **Book Value/Market Value Adjustments**: Several securities in the "Portfolio Assets" section mention adjustments to book value due to "Return of Capital" or "Phantom distributions". These are descriptive notes rather than transactions and are not included in the "Sample Extractions" of account activity.
*   **Currency**: The `currency` is set to "CAD" based on the section header "Account Activity — Canadian Dollars". This assumes all transactions within this section are in Canadian Dollars. If a statement contained mixed currencies, more granular extraction would be needed.