```markdown
## Date Format

The institution uses a `Mon DD` format for transaction dates within the activity section (e.g., `Jun 1`). The year is provided in the statement header (e.g., `June 1-June 30, 2022`).

## Transaction Format

Transactions are presented in a tabular format within the "Account Activity" section.

*   **Multi-line Transactions:** Yes, `Dividend` entries are often followed by a second line (e.g., `REINVESTED DIV @ 11.5845` or `NL REINVESTED DIV @ 9.5423`) which provides the per-unit price for the reinvested dividend.
*   **Column Separators:** Columns are primarily space-separated, with varying amounts of whitespace between the `description` and the numeric columns (`quantity`, `price`, `amount`). This suggests that simple string splitting by whitespace would be unreliable for the description field, and a regex approach is more suitable.
*   **Section Headers:** Transaction blocks are preceded by the header: `Account Activity — Canadian Dollars`.

### Regex Suggestions

1.  **Account Number Extraction (from header):**
    ```regex
    account # (\d{3}-\d{5})
    ```
2.  **Main Transaction Line (initial parse):** This regex captures the date, the raw description (which will be further processed), and the last three columns (`quantity`, `price`, `amount`).
    ```regex
    ^(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<day>\d{1,2})\s+(?P<description_raw>.*?)\s+(?P<quantity_str>[\d.-]+|—)\s+(?P<price_str>[\d.-]+|—)\s+(?P<amount_str>[\d.,$-]+|—)\s*$
    ```
    *   `month`: e.g., `Jun`
    *   `day`: e.g., `1`
    *   `description_raw`: e.g., `—       Opening cash balance` or `Dividend CIBC MONTHLY INCOME FUND`
    *   `quantity_str`: e.g., `—` or `101.711`
    *   `price_str`: e.g., `—`
    *   `amount_str`: e.g., `$0.00` or `—`

3.  **Reinvested Dividend Follow-up Line:**
    ```regex
    ^(NL\s+)?REINVESTED DIV @\s+(?P<reinvest_price>[\d.]+)$
    ```
    *   `reinvest_price`: e.g., `11.5845`

## Sample Extractions

```json
[
  {
    "raw_line": "Jun 1 —       Opening cash balance             —         —          $0.00",
    "date": "2022-06-01",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "Opening cash balance",
    "quantity": null,
    "price": null,
    "amount": 0.0,
    "currency": "CAD",
    "account_id": "586-33338",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 1 Dividend CIBC MONTHLY INCOME FUND    101.711       —             —\nREINVESTED DIV @ 11.5845",
    "date": "2022-06-01",
    "settlement_date": null,
    "action": "dividend",
    "symbol": null,
    "description": "CIBC MONTHLY INCOME FUND",
    "quantity": 101.711,
    "price": 11.5845,
    "amount": 1178.29,
    "currency": "CAD",
    "account_id": "586-33338",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 1 Dividend CIBC DIVIDEND INCOME FUND    61.898       —             —\nNL REINVESTED DIV @ 9.5423",
    "date": "2022-06-01",
    "settlement_date": null,
    "action": "dividend",
    "symbol": null,
    "description": "CIBC DIVIDEND INCOME FUND",
    "quantity": 61.898,
    "price": 9.5423,
    "amount": 590.73,
    "currency": "CAD",
    "account_id": "586-33338",
    "is_option": false,
    "option_details": null
  },
  {
    "raw_line": "Jun 30 —      Closing cash balance             —         —          $0.00",
    "date": "2022-06-30",
    "settlement_date": null,
    "action": "withdrawal",
    "symbol": null,
    "description": "Closing cash balance",
    "quantity": null,
    "price": null,
    "amount": 0.0,
    "currency": "CAD",
    "account_id": "586-33338",
    "is_option": false,
    "option_details": null
  }
]
```

## Parsing Notes

*   **Em Dash (`—`) for Missing Values:** The character `—` is consistently used to denote fields where a value is not applicable or present (e.g., for quantity, price, or amount). These should be parsed as `None` or `null`.
*   **Currency Symbol:** Amounts are prefixed with a dollar sign (`$`). This needs to be stripped before conversion to a float.
*   **Thousands Separator:** Amounts can contain commas (e.g., `$510,022.74`). These need to be removed before conversion to a float.
*   **Action Mapping:** The `action` field is inferred from the `description_raw`. Specific keywords like "Opening cash balance", "Closing cash balance", and "Dividend" are used to map to appropriate actions. Any other activity would default to "other".
*   **Year Context:** The year (`2022`) is derived from the statement header (`June 1-June 30, 2022`) and needs to be applied to all transaction dates within that statement period.
*   **No Settlement Date:** The provided sample does not contain a separate "settlement_date" column, so this field will be `null`.
*   **No Options:** No option-related transactions (buy/sell calls/puts, expiry, assignment) are present in the sample, so `is_option` will be `false` and `option_details` will be `null`.
*   **Account ID Consistency:** The account ID `586-33338` is consistently found in page headers and should be associated with all transactions on that statement.
```