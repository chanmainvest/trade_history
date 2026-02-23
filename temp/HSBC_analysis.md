Here's the analysis of the provided financial statement text:

---

## Date Format

The institution uses two primary date formats:

1.  **Statement Period:** `Month Day, Year` (e.g., `June1,2023`).
2.  **Transaction Dates:** `MonDay` (e.g., `Jun2`, `Jun5`). The year for transaction dates must be inferred from the statement period.

Therefore, for full `YYYY-MM-DD` formatting, transaction dates combine `MonDay` with the `YYYY` from the statement period.

## Transaction Format

Transactions are primarily found under sections titled "Account activity since your last statement" or "Account activity since your last statement (continued)".

**General Structure:**

Transaction lines generally follow a fixed-width-like structure, with columns for `Datesettled`, `Activity`, `Description`, `Quantity`, `Price`, and `amount(CAD/USD)`. However, the exact spacing can vary, and some fields might be absent for certain transaction types.

Multi-line entries are present where additional descriptive text or confirmation numbers appear on lines immediately following the primary transaction. These lines should be appended to the `description` of the preceding transaction.

**Regex Suggestions for Parsing:**

1.  **Statement Period (to extract year):**
    ```regex
    Statementperiod:?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\d{1,2},\s*(\d{4})
    ```
    *Group 1 captures the year.*

2.  **Account ID and Currency (to set context):**
    *   **Canadian Margin Account:**
        ```regex
        Account#(\w{2}-\w{4}-\w)\s+Your Canadian Margin Account
        ```
        *Group 1 captures the account ID. Currency is "CAD".*
    *   **USD Margin Account:**
        ```regex
        Account#(\w{2}-\w{4}-\w)\s+Your USD\s+Margin Account
        ```
        *Group 1 captures the account ID. Currency is "USD".*

3.  **Standard Transaction Line (with Quantity, Price, and Amount):**
    ```regex
    ^(?P<month_abbr>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?P<day>\d{1,2})\s+(?P<activity>Bought|Sold)\s+(?P<description>.+?)\s+(?P<quantity>\(?[\d,\.]+\)?)\s+(?P<price>[\d\.]+)\s+(?P<amount>\(?[\d,\.]+\)?)$
    ```

4.  **Transaction Line (e.g., Dividends, Non-ResTax - with Quantity but no Price):**
    ```regex
    ^(?P<month_abbr>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?P<day>\d{1,2})\s+(?P<activity>Non-ResTax|Dividend)\s+(?P<description>.+?)(?P<quantity>[\d,\.]+)\s+(?P<amount>\(?[\d,\.]+\)?)$
    ```

5.  **Transaction Line (e.g., Deposit - with only Amount):**
    ```regex
    ^(?P<month_abbr>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?P<day>\d{1,2})\s+(?P<activity>Deposit)\s+(?P<description>\d{3}-\d{6}-\d{3}\w{6}|[A-Z\s]+)\s+(?P<amount>\(?[\d,\.]+\)?)$
    ```
    *Note: The description for Deposit can sometimes be an internal reference number like `082-365679-1506Y6HF9E`.*

6.  **Transaction Line (e.g., Options Expire/Bought with partial info):**
    ```regex
    ^(?P<month_abbr>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?P<day>\d{1,2})\s+(?P<activity>Bought|Expire)\s+(?P<description>.+?)\s+(?P<value>\(?[\d,\.]+\)?)$
    ```
    *The `value` here could be quantity or amount, requiring context-specific interpretation.*

7.  **Option Symbol Extraction (from description):**
    ```regex
    ^(?P<type>PUT|CALL)-(?P<strike>\d+\.?\d*)(?P<symbol>[A-Z\.\-]+)'(?P<year>\d{2})(?P<month_code>\w{2})$
    ```
    *Captures `type`, `strike`, `symbol`, `year`, and `month_code` for expiry.*

## Sample Extractions

```json
[
  {
    "raw_line": "Jun2   Deposit   082-365679-1506Y6HF9E                          370,000.00",
    "date": "2023-06-02",
    "settlement_date": null,
    "action": "deposit",
    "symbol": null,
    "description": "082-365679-1506Y6HF9E",
    "quantity": null,
    "price": null,
    "amount": 370000.00,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun5   Bought    HORZNHIGHINTSVGSETF-A      10,000 50.030      (500,306.88)",
    "date": "2023-06-05",
    "settlement_date": null,
    "action": "buy",
    "symbol": "HORZNHIGHINTSVGSETF-A",
    "description": "HORZNHIGHINTSVGSETF-A",
    "quantity": 10000.0,
    "price": 50.03,
    "amount": -500306.88,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun6   Bought    HORZNHIGHINTSVGSETF-A       8,100 50.040      (405,330.88)",
    "date": "2023-06-06",
    "settlement_date": null,
    "action": "buy",
    "symbol": "HORZNHIGHINTSVGSETF-A",
    "description": "HORZNHIGHINTSVGSETF-A",
    "quantity": 8100.0,
    "price": 50.04,
    "amount": -405330.88,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun15  Bought    PUT-100T'23JN              11",
    "date": "2023-06-15",
    "settlement_date": null,
    "action": "buy",
    "symbol": "T",
    "description": "PUT-100T'23JN",
    "quantity": 11.0,
    "price": null,
    "amount": null,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "T",
      "expiry": "2023-06-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun16  EPS       082-365679-1506Y6HF9E                         (10,000.00)",
    "date": "2023-06-16",
    "settlement_date": null,
    "action": "other",
    "symbol": null,
    "description": "082-365679-1506Y6HF9E",
    "quantity": null,
    "price": null,
    "amount": -10000.00,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun16  Bought    TELUSCORP                   1,100 26.000      (28,690.90)",
    "date": "2023-06-16",
    "settlement_date": null,
    "action": "buy",
    "symbol": "TELUSCORP",
    "description": "TELUSCORP ASOFJUN14,2023 ASSIGNED",
    "quantity": 1100.0,
    "price": 26.0,
    "amount": -28690.90,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun19  Sold      HORZNHIGHINTSVGSETF-A      (4,600) 50.120      230,547.60",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "sell",
    "symbol": "HORZNHIGHINTSVGSETF-A",
    "description": "HORZNHIGHINTSVGSETF-A",
    "quantity": -4600.0,
    "price": 50.12,
    "amount": 230547.60,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun19  Sold      PUT-100MFC'23SP            (25)  0.230         536.87",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "sell",
    "symbol": "MFC",
    "description": "PUT-100MFC'23SP OPENINGTRANS-UNCOVERED EXPIRESONSEP15,2023",
    "quantity": -25.0,
    "price": 0.23,
    "amount": 536.87,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "MFC",
      "expiry": "2023-09-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Sold      PUT-100SHOP'23SP           (20)  0.550        1,068.12",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "sell",
    "symbol": "SHOP",
    "description": "PUT-100SHOP'23SP OPENINGTRANS-UNCOVERED EXPIRESONSEP15,2023",
    "quantity": -20.0,
    "price": 0.55,
    "amount": 1068.12,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "SHOP",
      "expiry": "2023-09-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Expire    PUT-100OTEX'23JN           20",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": "OTEX",
    "description": "PUT-100OTEX'23JN",
    "quantity": 20.0,
    "price": null,
    "amount": null,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "OTEX",
      "expiry": "2023-06-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Expire    PUT-100SHOP'23JN           20",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": "SHOP",
    "description": "PUT-100SHOP'23JN",
    "quantity": 20.0,
    "price": null,
    "amount": null,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "SHOP",
      "expiry": "2023-06-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Expire    PUT-100BCE'23JN            10",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": "BCE",
    "description": "PUT-100BCE'23JN",
    "quantity": 10.0,
    "price": null,
    "amount": null,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "BCE",
      "expiry": "2023-06-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Sold      PUT-100BCE'23SP            (20)  0.540        1,048.12",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "sell",
    "symbol": "BCE",
    "description": "PUT-100BCE'23SP OPENINGTRANS-UNCOVERED EXPIRESONSEP15,2023",
    "quantity": -20.0,
    "price": 0.54,
    "amount": 1048.12,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "BCE",
      "expiry": "2023-09-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Sold      PUT-100CVE'23SP @19.5         (50)  0.480        2,330.62",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "sell",
    "symbol": "CVE",
    "description": "PUT-100CVE'23SP OPENINGTRANS-UNCOVERED EXPIRESONSEP15,2023",
    "quantity": -50.0,
    "price": 0.48,
    "amount": 2330.62,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "CVE",
      "expiry": "2023-09-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Sold      PUT-100FTS'23SP            (20)  0.600        1,168.12",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "sell",
    "symbol": "FTS",
    "description": "PUT-100FTS'23SP OPENINGTRANS-UNCOVERED EXPIRESONSEP15,2023",
    "quantity": -20.0,
    "price": 0.6,
    "amount": 1168.12,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "FTS",
      "expiry": "2023-09-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Sold      PUT-100H'23SP              (25)  0.360         861.87",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "sell",
    "symbol": "H",
    "description": "PUT-100H'23SP OPENINGTRANS-UNCOVERED EXPIRESONSEP15,2023",
    "quantity": -25.0,
    "price": 0.36,
    "amount": 861.87,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "H",
      "expiry": "2023-09-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Bought    PUT-100RCI.B'23JN          20",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "buy",
    "symbol": "RCI.B",
    "description": "PUT-100RCI.B'23JN",
    "quantity": 20.0,
    "price": null,
    "amount": null,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "RCI.B",
      "expiry": "2023-06-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Expire    PUT-100TECK.B'23JN         20",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "option_expiry",
    "symbol": "TECK.B",
    "description": "PUT-100TECK.B'23JN",
    "quantity": 20.0,
    "price": null,
    "amount": null,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "TECK.B",
      "expiry": "2023-06-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun19  Bought    PUT-100T'23JN              9",
    "date": "2023-06-19",
    "settlement_date": null,
    "action": "buy",
    "symbol": "T",
    "description": "PUT-100T'23JN",
    "quantity": 9.0,
    "price": null,
    "amount": null,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": true,
    "option_details": {
      "root": "T",
      "expiry": "2023-06-01",
      "strike": 100.0,
      "put_call": "put"
    }
  },
  {
    "raw_line": "Jun20  Bought    ROGERSCOMMUNICATION-BNV     2,000 60.000      (120,139.50)",
    "date": "2023-06-20",
    "settlement_date": null,
    "action": "buy",
    "symbol": "ROGERSCOMMUNICATION-BNV",
    "description": "ROGERSCOMMUNICATION-BNV ASOFJUN16,2023 ASSIGNED",
    "quantity": 2000.0,
    "price": 60.0,
    "amount": -120139.50,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun20  Bought    TELUSCORP                    900  26.000      (23,480.10)",
    "date": "2023-06-20",
    "settlement_date": null,
    "action": "buy",
    "symbol": "TELUSCORP",
    "description": "TELUSCORP ASOFJUN16,2023 ASSIGNED",
    "quantity": 900.0,
    "price": 26.0,
    "amount": -23480.10,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun23  Bought    SPROTTINC-NEW               1,300 42.990      (55,893.88)",
    "date": "2023-06-23",
    "settlement_date": null,
    "action": "buy",
    "symbol": "SPROTTINC-NEW",
    "description": "SPROTTINC-NEW",
    "quantity": 1300.0,
    "price": 42.99,
    "amount": -55893.88,
    "currency": "CAD",
    "account_id": "6Y-6HF9-E",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun1   Sold      RBBFDUSTREAS2YNTETF        (5,000) 48.540      242,691.17",
    "date": "2023-06-01",
    "settlement_date": null,
    "action": "sell",
    "symbol": "RBBFDUSTREAS2YNTETF",
    "description": "RBBFDUSTREAS2YNTETF",
    "quantity": -5000.0,
    "price": 48.54,
    "amount": 242691.17,
    "currency": "USD",
    "account_id": "6Y-6HF9-F",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun1   Sold      RBBFDUSTR12MBILLETF        (5,600) 49.830      279,038.88",
    "date": "2023-06-01",
    "settlement_date": null,
    "action": "sell",
    "symbol": "RBBFDUSTR12MBILLETF",
    "description": "RBBFDUSTR12MBILLETF",
    "quantity": -5600.0,
    "price": 49.83,
    "amount": 279038.88,
    "currency": "USD",
    "account_id": "6Y-6HF9-F",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun1   Sold      RBBUSTREAS6MOBILLETF       (4,800) 50.020      240,087.19",
    "date": "2023-06-01",
    "settlement_date": null,
    "action": "sell",
    "symbol": "RBBUSTREAS6MOBILLETF",
    "description": "RBBUSTREAS6MOBILLETF",
    "quantity": -4800.0,
    "price": 50.02,
    "amount": 240087.19,
    "currency": "USD",
    "account_id": "6Y-6HF9-F",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun1   Sold      SPDRBLM3-12MT-BILLETF      (1,650) 99.420      164,034.80",
    "date": "2023-06-01",
    "settlement_date": null,
    "action": "sell",
    "symbol": "SPDRBLM3-12MT-BILLETF",
    "description": "SPDRBLM3-12MT-BILLETF",
    "quantity": -1650.0,
    "price": 99.42,
    "amount": 164034.80,
    "currency": "USD",
    "account_id": "6Y-6HF9-F",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun5   Non-ResTax RBBUSTREAS3MOBILLETF       4,230               (132.75)",
    "date": "2023-06-05",
    "settlement_date": null,
    "action": "fee",
    "symbol": "RBBUSTREAS3MOBILLETF",
    "description": "Non-ResTax RBBUSTREAS3MOBILLETF",
    "quantity": 4230.0,
    "price": null,
    "amount": -132.75,
    "currency": "USD",
    "account_id": "6Y-6HF9-F",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun5   Dividend  RBBUSTREAS3MOBILLETF        4,230                885.03",
    "date": "2023-06-05",
    "settlement_date": null,
    "action": "dividend",
    "symbol": "RBBUSTREAS3MOBILLETF",
    "description": "Dividend RBBUSTREAS3MOBILLETF",
    "quantity": 4230.0,
    "price": null,
    "amount": 885.03,
    "currency": "USD",
    "account_id": "6Y-6HF9-F",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun6   Bought    RBBUSTREAS6MOBILLETF       18,000 50.100      (901,806.88)",
    "date": "2023-06-06",
    "settlement_date": null,
    "action": "buy",
    "symbol": "RBBUSTREAS6MOBILLETF",
    "description": "RBBUSTREAS6MOBILLETF",
    "quantity": 18000.0,
    "price": 50.1,
    "amount": -901806.88,
    "currency": "USD",
    "account_id": "6Y-6HF9-F",
    "is_option": false,
    "option_details": {}
  },
  {
    "raw_line": "Jun12  Bought    RBBUSTREAS6MOBILLETF         500  50.008      (25,010.93)",
    "date": "2023-06-12",
    "settlement_date": null,
    "action": "buy",
    "symbol": "RBBUSTREAS6MOBILLETF",
    "description": "RBBUSTREAS6MOBILLETF",
    "quantity": 500.0,
    "price": 50.008,
    "amount": -25010.93,
    "currency": "USD",
    "account_id": "6Y-6HF9-F",
    "is_option": false,
    "option_details": {}
  }
]
```

## Parsing Notes

1.  **Multi-line Transactions:** Confirmation numbers (e.g., `2023060220000103`) and additional descriptive phrases (e.g., `OPENINGTRANS-UNCOVERED`, `EXPIRESONSEP15,2023`, `ASOFJUN14,2023`, `ASSIGNED`) often appear on lines immediately following the primary transaction line. These should be consolidated into the `description` field of the preceding transaction.

2.  **Date Inference:** Transaction lines use a `MonDay` format (e.g., `Jun19`). The full year (`2023`) must be extracted from the "Statement period" header and applied to these transaction dates.

3.  **Column Alignment:** While the text appears to have columns, they are not strictly fixed-width or tab-separated. Parsing requires flexible matching and careful handling of whitespace.

4.  **Noise in Description:** The extracted text contains artifacts like `.ruff_cache\...` and `data\test-symbol-overrides-...sqlite` embedded within descriptions. These should be filtered out during parsing.

5.  **Option Details:**
    *   Option symbols follow a consistent pattern: `(PUT|CALL)-<StrikePrice><Symbol>'<ExpiryYearTwoDigits><ExpiryMonthCode>`.
    *   Examples of Expiry Month Codes: `JN` (June), `SP` (September), `MR` (March).
    *   The `strike` price is typically embedded directly after `PUT-` or `CALL-` (e.g., `PUT-100`).
    *   Some option transaction lines (e.g., `Expire` or `Bought` options) might not explicitly list `price` and `amount` in separate columns, or the `quantity` might be in the position where `amount` typically is.

6.  **"Quantity" for Dividends/Fees:** For `Non-ResTax` and `Dividend` entries, the "Quantity" column seems to represent the number of shares held rather than a transaction quantity related to the fee or dividend itself. It should still be recorded but understood in this context.

7.  **Account Context:** The currency and `account_id` for transactions are determined by the preceding "Your Canadian Margin Account" or "Your USD Margin Account" headers.

8.  **Ignored Lines:** "OpeningBalance" and "ClosingBalance" lines are not considered transactions for this extraction. "Asset Mix" and "Details of holdings in your account" sections describe positions, not activities, and are also ignored for transaction extraction.

9.  **Action Mapping:** Activities like `EPS` (Earnings Per Share) are mapped to a generic `other` action, as their specific financial implication isn't fully detailed here to categorize as a standard buy/sell/dividend. `ASSIGNED` can be a multi-line detail, or sometimes on its own line if it's the core activity. For these samples, `ASSIGNED` was a multi-line detail, so it's included in the description. If it was a primary activity, it would map to `option_assignment`.

---