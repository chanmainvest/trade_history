from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from pathlib import Path
from typing import Iterable

import pdfplumber

from trade_history.parsers.base import ParseIssue, ParsedEvent, ParsedInstrument, ParsedSnapshot


DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%b %d %Y",
    "%b %d, %Y",
    "%b %d",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%m/%d/%Y",
    "%m/%d/%y",
)

DATE_RE = re.compile(
    r"(?P<date>\d{4}[-/]\d{2}[-/]\d{2}"
    r"|\d{2}[-/]\d{2}[-/]\d{2,4}"
    r"|\d{2}-[A-Za-z]{3}-\d{2,4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s*\d{1,2}(?:,\s*\d{4})?)",
    re.IGNORECASE,
)
ACTION_RE = re.compile(
    r"\b(?P<action>BUY TO OPEN|SELL TO OPEN|BUY TO CLOSE|SELL TO CLOSE|BUY TO COVER|SELL SHORT|"
    r"TFR IN|TFR OUT|TFR|TRANSFER IN|TRANSFER OUT|TRANSFER|BUY|SELL|BOT|SOLD|BTO|STO|BTC|STC|DIVIDENDS?|INTEREST|FEE|COMMISSION)\b",
    re.IGNORECASE,
)
CURRENCY_RE = re.compile(r"\b(?P<ccy>CAD|USD)\b", re.IGNORECASE)
MONEY_RE = re.compile(r"(?<![A-Za-z])\(?-?\$?\d[\d,]*\.?\d*\)?(?![A-Za-z])")
SYMBOL_RE = re.compile(r"\b[A-Z][A-Z0-9\.]{1,9}\b")
OPTION_RE = re.compile(
    r"(?P<root>[A-Z]{1,6})\s+(?P<expiry>\d{2}[A-Za-z]{3}\d{2}|\d{4}-\d{2}-\d{2})\s+"
    r"(?P<putcall>P|C|PUT|CALL)\s+(?P<strike>\d+(\.\d+)?)",
    re.IGNORECASE,
)
TRADE_ROW_RE = re.compile(
    r"^(?P<date>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s*\d{1,2})\s+"
    r"(?P<action>[A-Za-z ]+?)\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<qty>\(?-?\d[\d,]*(?:\.\d+)?\)?)\s+"
    r"(?P<price>-?\d[\d,]*(?:\.\d+)?)\s+"
    r"(?P<amount>\(?-?\$?\d[\d,]*(?:\.\d+)?\)?)",
    re.IGNORECASE,
)
EXPLICIT_FEE_RE = re.compile(
    r"\b(?:COMM(?:ISSION)?|FEE(?:S)?)\b[^0-9$()\-]{0,8}(?P<value>\(?-?\$?\d[\d,]*(?:\.\d+)?\)?)",
    re.IGNORECASE,
)
SNAPSHOT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cash_opening", re.compile(r"\bopening cash balance\b", re.IGNORECASE)),
    ("cash_closing_total", re.compile(r"\btotal closing cash balance\b", re.IGNORECASE)),
    ("cash_closing", re.compile(r"\b(?:closing cash balance|ending balance)\b", re.IGNORECASE)),
    ("portfolio_total", re.compile(r"\btotal portfolio\b", re.IGNORECASE)),
    ("portfolio_total", re.compile(r"\btotal on\b", re.IGNORECASE)),
    ("account_value_previous", re.compile(r"\bprevious\s*account\s*value\b", re.IGNORECASE)),
    ("account_value_current", re.compile(r"\bbalance\s+of\s+securities\b", re.IGNORECASE)),
)
ACCOUNT_ID_RE = re.compile(
    r"(?:ACCOUNT|ACCT|A/C)\s*(?:NO\.?|NUMBER)?[:#]?\s*([A-Z0-9\-]{4,20})",
    re.IGNORECASE,
)


SIDE_MAP = {
    "TFR": "TRANSFER",
    "TFR IN": "TRANSFER_IN",
    "TFR OUT": "TRANSFER_OUT",
    "BUY": "BUY",
    "BOT": "BUY",
    "BUY TO OPEN": "BUY_TO_OPEN",
    "SELL": "SELL",
    "SOLD": "SELL",
    "SELL SHORT": "SELL_SHORT",
    "SELL TO OPEN": "SELL_TO_OPEN",
    "BUY TO COVER": "BUY_TO_COVER",
    "BUY TO CLOSE": "BUY_TO_CLOSE",
    "STO": "SELL_TO_OPEN",
    "STC": "SELL_TO_CLOSE",
    "BTO": "BUY_TO_OPEN",
    "BTC": "BUY_TO_CLOSE",
    "SELL TO CLOSE": "SELL_TO_CLOSE",
    "TRANSFER": "TRANSFER",
    "TRANSFER IN": "TRANSFER_IN",
    "TRANSFER OUT": "TRANSFER_OUT",
    "DIVIDEND": "DIVIDEND",
    "DIVIDENDS": "DIVIDEND",
    "INTEREST": "INTEREST",
    "FEE": "FEE",
    "COMMISSION": "COMMISSION",
}


@dataclass(slots=True)
class TextLine:
    page_number: int
    line_number: int
    text: str


def parse_date(value: str | None, default_year: int | None = None) -> date | None:
    if not value:
        return None
    v = value.strip().replace(".", " ").replace("  ", " ")
    v = re.sub(r"([A-Za-z]{3,})(\d{1,2})", r"\1 \2", v)
    v = re.sub(r"\s+", " ", v)
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(v, fmt)
            if fmt == "%b %d" and default_year is not None:
                dt = dt.replace(year=default_year)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            return dt.date()
        except ValueError:
            continue
    return None


def parse_money(value: str | None) -> float | None:
    if not value:
        return None
    s = value.strip().replace("$", "").replace(",", "")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s.startswith("-"):
        negative = True
        s = s[1:]
    if not s:
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if negative else val


def normalize_symbol(symbol: str | None) -> str:
    if not symbol:
        return "UNKNOWN"
    return symbol.strip().upper().replace("/", ".").replace("-", ".")


def _clean_symbol(token: str) -> str:
    return normalize_symbol(token.replace("'", "").replace("@", ""))


def extract_text_lines(file_path: Path) -> list[TextLine]:
    lines: list[TextLine] = []
    with pdfplumber.open(file_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            text = page.extract_text(layout=True) or page.extract_text() or ""
            for line_idx, raw_line in enumerate(text.splitlines()):
                line = raw_line.strip()
                if not line:
                    continue
                lines.append(TextLine(page_number=page_index + 1, line_number=line_idx + 1, text=line))
    return lines


def guess_instrument(line: str) -> ParsedInstrument | None:
    cibc_option_match = re.search(
        r"\b(?P<putcall>PUT|CALL)\s+\.?(?P<root>[A-Z]{1,6})\s+"
        r"(?:[A-Za-z]{3}\s+\d{1,2}\s+\d{4}|\d{2}[A-Za-z]{3}\d{2})\s+"
        r"(?P<strike>\d+(\.\d+)?)",
        line,
        re.IGNORECASE,
    )
    if cibc_option_match:
        put_call = "P" if cibc_option_match.group("putcall").upper() == "PUT" else "C"
        return ParsedInstrument(
            symbol_raw=cibc_option_match.group("root"),
            symbol_norm=_clean_symbol(cibc_option_match.group("root")),
            asset_type="option",
            option_root=_clean_symbol(cibc_option_match.group("root")),
            strike=float(cibc_option_match.group("strike")),
            put_call=put_call,
            multiplier=100,
        )

    td_option_match = re.search(
        r"\b(?P<putcall>PUT|CALL)\s*[-\s]?100\s+(?P<root>[A-Z]{1,6})(?:[^@]*@(?P<strike>\d+(\.\d+)?))?",
        line,
        re.IGNORECASE,
    )
    if td_option_match:
        put_call = "P" if td_option_match.group("putcall").upper() == "PUT" else "C"
        strike = td_option_match.group("strike")
        return ParsedInstrument(
            symbol_raw=td_option_match.group("root"),
            symbol_norm=_clean_symbol(td_option_match.group("root")),
            asset_type="option",
            option_root=_clean_symbol(td_option_match.group("root")),
            strike=float(strike) if strike else None,
            put_call=put_call,
            multiplier=100,
        )

    hsbc_option_match = re.search(
        r"\b(?P<putcall>PUT|CALL)-?100(?P<root>[A-Z]{1,6}).*?@(?P<strike>\d+(\.\d+)?)",
        line,
        re.IGNORECASE,
    )
    if hsbc_option_match:
        put_call = "P" if hsbc_option_match.group("putcall").upper() == "PUT" else "C"
        return ParsedInstrument(
            symbol_raw=hsbc_option_match.group("root"),
            symbol_norm=_clean_symbol(hsbc_option_match.group("root")),
            asset_type="option",
            option_root=_clean_symbol(hsbc_option_match.group("root")),
            strike=float(hsbc_option_match.group("strike")),
            put_call=put_call,
            multiplier=100,
        )

    option_match = OPTION_RE.search(line)
    if option_match:
        expiry = parse_date(option_match.group("expiry"))
        put_call = option_match.group("putcall").upper()
        if put_call == "PUT":
            put_call = "P"
        if put_call == "CALL":
            put_call = "C"
        return ParsedInstrument(
            symbol_raw=option_match.group("root"),
            symbol_norm=normalize_symbol(option_match.group("root")),
            asset_type="option",
            option_root=normalize_symbol(option_match.group("root")),
            strike=float(option_match.group("strike")),
            expiry=expiry,
            put_call=put_call,
            multiplier=100,
        )

    candidates = SYMBOL_RE.findall(line)
    if not candidates:
        return None
    # Ignore noisy upper-case tokens that are common text.
    blocked = {
        "CAD",
        "USD",
        "RRSP",
        "TFSA",
        "RESP",
        "ETF",
        "DRIP",
        "WITH",
        "TAX",
        "NET",
        "LTD",
        "INC",
        "CORP",
        "CORPORATION",
        "COMPANY",
        "PLC",
        "HOLDINGS",
        "FUND",
        "DIVIDEND",
        "DIVIDENDS",
        "INTEREST",
        "PREMIUM",
        "MARKET",
        "MONEY",
        "ACCOUNT",
        "TRANSFER",
        "FROM",
        "TO",
        "CONTRIB",
        "CONTRIBUTION",
        "WITHDRAWAL",
        "WITHDRAW",
        "DEPOSIT",
        "ACTIVITY",
        "STATEMENT",
        "DIRECT",
        "WEBBROKER",
        "INVEST",
        "THIS",
        "THAT",
        "PUT",
        "CALL",
        "MAR",
        "APR",
        "MAY",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "OCT",
        "NOV",
        "DEC",
        "JAN",
        "FEB",
        "NRT",
    }
    for token in candidates:
        if token in blocked:
            continue
        if len(token) == 1:
            continue
        return ParsedInstrument(
            symbol_raw=token,
            symbol_norm=normalize_symbol(token),
            asset_type="equity",
        )
    return None


def parse_trade_like_line(
    account_id: str,
    text_line: TextLine,
    institution: str,
    default_year: int | None = None,
) -> tuple[ParsedEvent | None, ParseIssue | None]:
    text = text_line.text
    action_match = ACTION_RE.search(text)
    date_match = DATE_RE.search(text)
    if not action_match or not date_match:
        return None, None

    action = action_match.group("action").upper()
    side = SIDE_MAP.get(action, action)
    trade_date = parse_date(date_match.group("date"), default_year=default_year)
    if not trade_date:
        return None, ParseIssue(
            page_number=text_line.page_number,
            raw_line=text,
            reason="date_parse_failed",
        )

    settlement_date = None
    date_matches = [m.group("date") for m in DATE_RE.finditer(text)]
    if len(date_matches) > 1:
        settlement_date = parse_date(date_matches[1], default_year=default_year)

    currency_match = CURRENCY_RE.search(text)
    currency = currency_match.group("ccy").upper() if currency_match else None

    # Ignore date-related numbers by parsing only text after the action token.
    numeric_tail = text[action_match.end() :]
    instrument = guess_instrument(numeric_tail)
    parsed_values = [parse_money(v) for v in MONEY_RE.findall(numeric_tail)]
    values = [v for v in parsed_values if v is not None]
    quantity = None
    price = None
    gross_amount = None
    commission = 0.0
    fees = 0.0

    # Start with lightweight parsing for qty/price only; remaining fields are side-specific.
    if values:
        if len(values) >= 1:
            quantity = values[0]
        if len(values) >= 2:
            price = abs(values[1])

    event_type = "trade"
    if side == "TRANSFER":
        # Infer direction when statements only say "Transfer".
        if re.search(r"\b(OUT|TFR OUT|TRANSFER\s+TO)\b", text, re.IGNORECASE):
            side = "TRANSFER_OUT"
        elif re.search(r"\b(IN|TFR IN|TRANSFER\s+FROM)\b", text, re.IGNORECASE):
            side = "TRANSFER_IN"
        else:
            side = "TRANSFER_OUT" if (quantity or 0) < 0 else "TRANSFER_IN"
    if side in {"TRANSFER_IN", "TRANSFER_OUT"}:
        event_type = "transfer"
    elif side == "DIVIDEND":
        event_type = "dividend"
    elif side == "INTEREST":
        event_type = "interest"
    elif side in {"FEE", "COMMISSION"}:
        event_type = "fee"

    trade_sides = {
        "BUY",
        "SELL",
        "BUY_TO_OPEN",
        "SELL_TO_OPEN",
        "BUY_TO_CLOSE",
        "SELL_TO_CLOSE",
        "SELL_SHORT",
        "BUY_TO_COVER",
    }
    if side in trade_sides:
        trade_row = TRADE_ROW_RE.search(text)
        if trade_row:
            qty_val = parse_money(trade_row.group("qty"))
            px_val = parse_money(trade_row.group("price"))
            amt_val = parse_money(trade_row.group("amount"))
            quantity = abs(qty_val) if qty_val is not None else quantity
            price = abs(px_val) if px_val is not None else price
            gross_amount = amt_val if amt_val is not None else gross_amount

        if quantity is not None:
            quantity = abs(quantity)
        expected_notional = abs(quantity * price) if (quantity is not None and price is not None) else 0.0
        trailing = list(enumerate(values[2:], start=2))
        gross_index: int | None = None
        if trailing and expected_notional > 0:
            gross_index, gross_candidate = min(
                trailing,
                key=lambda item: abs(abs(item[1]) - expected_notional),
            )
            tolerance = max(5.0, expected_notional * 0.2)
            if abs(abs(gross_candidate) - expected_notional) <= tolerance:
                gross_amount = gross_candidate
            else:
                gross_index = None
        elif len(trailing) == 1:
            gross_index, gross_amount = trailing[0]

        fee_match = EXPLICIT_FEE_RE.search(numeric_tail)
        if fee_match:
            fee_value = parse_money(fee_match.group("value"))
            if fee_value is not None:
                commission = abs(fee_value)

        if commission == 0.0 and trailing:
            fee_cap = max(500.0, expected_notional * 0.02) if expected_notional > 0 else 500.0
            fee_candidates = [
                abs(value)
                for idx, value in trailing
                if idx != gross_index and 0 < abs(value) <= fee_cap
            ]
            if fee_candidates:
                fee_candidates.sort()
                commission = fee_candidates[0]
                if len(fee_candidates) > 1:
                    fees = fee_candidates[1]

        if gross_amount is not None:
            if side in {"BUY", "BUY_TO_OPEN", "BUY_TO_CLOSE", "BUY_TO_COVER"} and gross_amount > 0:
                gross_amount = -gross_amount
            elif side in {"SELL", "SELL_SHORT", "SELL_TO_OPEN", "SELL_TO_CLOSE"} and gross_amount < 0:
                gross_amount = abs(gross_amount)

    if event_type == "transfer":
        if instrument and instrument.symbol_norm in {"TRANSFER", "FROM", "TO"}:
            instrument = None

        if quantity is not None:
            quantity = abs(quantity)
            # Account transfer rows often contain account numbers in the numeric column.
            if quantity >= 1_000_000:
                quantity = None

        if values:
            last_value = values[-1]
            gross_amount = last_value
            if quantity is not None and abs(last_value - quantity) < 1e-6:
                gross_amount = None
            if len(values) >= 2 and quantity is not None and abs(values[1] - quantity) < 1e-6:
                gross_amount = None
        price = None

    if instrument and instrument.asset_type == "option":
        option_numbers = [parse_money(v) for v in MONEY_RE.findall(numeric_tail)]
        option_numbers = [v for v in option_numbers if v is not None]
        candidate_pattern = re.compile(
            r"(?P<qty>\(?-?\d[\d,]*(?:\.\d+)?\)?)\s+"
            r"(?P<price>-?\d[\d,]*(?:\.\d+)?)\s+"
            r"(?P<amount>\(?-?\$?\d[\d,]*(?:\.\d+)?\)?)",
            re.IGNORECASE,
        )
        option_match = None
        for match in candidate_pattern.finditer(numeric_tail):
            qty_val = parse_money(match.group("qty"))
            px_val = parse_money(match.group("price"))
            if qty_val is None or px_val is None:
                continue
            # Quantity is usually integer contracts; avoid matching trailing balances.
            if abs(qty_val - round(qty_val)) > 1e-6:
                continue
            if abs(px_val) > 1000:
                continue
            option_match = match
            break
        if option_match:
            qty_val = parse_money(option_match.group("qty"))
            px_val = parse_money(option_match.group("price"))
            amt_val = parse_money(option_match.group("amount"))
            quantity = abs(qty_val) if qty_val is not None else quantity
            price = abs(px_val) if px_val is not None else price
            gross_amount = amt_val if amt_val is not None else gross_amount
        elif len(option_numbers) >= 3:
            # Fallback heuristic when regex grouping cannot isolate contract fields.
            candidate_slice = option_numbers[-4:-1] if len(option_numbers) >= 4 else option_numbers[-3:]
            if len(candidate_slice) == 3:
                quantity = abs(candidate_slice[0])
                price = abs(candidate_slice[1])
                gross_amount = candidate_slice[2]

    event = ParsedEvent(
        account_id=account_id,
        trade_date=trade_date,
        settle_date=settlement_date,
        event_type=event_type,
        side=side,
        quantity=quantity,
        price=price,
        gross_amount=gross_amount,
        commission=commission,
        fees=fees,
        currency=currency,
        instrument=instrument,
        source_line_ref=f"p{text_line.page_number}:l{text_line.line_number}",
        notes=f"{institution}: {text[:200]}",
    )
    return event, None


def _infer_snapshot_currency(line: str) -> str | None:
    ccy_match = CURRENCY_RE.search(line)
    if ccy_match:
        return ccy_match.group("ccy").upper()
    if re.search(r"\b(CANADIAN|CDN|CAD)\b", line, re.IGNORECASE):
        return "CAD"
    if re.search(r"\b(US|USD|U\.S\.)\b", line, re.IGNORECASE):
        return "USD"
    return None


def _extract_snapshot_amounts(line: str) -> list[float]:
    values: list[float] = []
    for match in MONEY_RE.finditer(line):
        token = match.group(0)
        next_char = line[match.end() : match.end() + 1]
        if next_char == "%":
            continue
        has_amount_hint = "$" in token or "," in token or "." in token
        if not has_amount_hint:
            continue
        parsed = parse_money(token)
        if parsed is None:
            continue
        values.append(parsed)
    return values


def parse_snapshot_line(
    account_id: str,
    text_line: TextLine,
    default_year: int | None = None,
) -> ParsedSnapshot | None:
    text = text_line.text
    if MONEY_RE.search(text) is None:
        return None

    metric_code = None
    for code, pattern in SNAPSHOT_RULES:
        if pattern.search(text):
            metric_code = code
            break
    if metric_code is None:
        return None

    amounts = _extract_snapshot_amounts(text)
    if not amounts:
        return None

    line_date = None
    date_match = DATE_RE.search(text)
    if date_match:
        line_date = parse_date(date_match.group("date"), default_year=default_year)

    return ParsedSnapshot(
        account_id=account_id,
        metric_code=metric_code,
        value_native=float(amounts[-1]),
        currency=_infer_snapshot_currency(text),
        snapshot_date=line_date,
        source_line_ref=f"p{text_line.page_number}:l{text_line.line_number}",
        raw_line=text[:240],
    )


def extract_statement_snapshots(
    lines: Iterable[TextLine],
    default_account_id: str,
    default_year: int | None = None,
) -> list[ParsedSnapshot]:
    snapshots: list[ParsedSnapshot] = []
    seen: set[tuple[str, str, str | None, float, str | None]] = set()
    current_account = default_account_id

    for line in lines:
        found_account = extract_account_id_from_text(line.text)
        if found_account:
            current_account = found_account

        snap = parse_snapshot_line(current_account, line, default_year=default_year)
        if snap is None:
            continue

        dedupe_key = (
            snap.account_id,
            snap.metric_code,
            snap.snapshot_date.isoformat() if snap.snapshot_date else None,
            round(snap.value_native, 4),
            snap.currency,
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        snapshots.append(snap)

    return snapshots


def _is_valid_account_id(candidate: str) -> bool:
    account_id = candidate.strip().upper()
    if not any(ch.isdigit() for ch in account_id):
        return False
    normalized = account_id.replace("-", "")
    # Avoid matching street numbers (for example "Account 1234 Main St").
    if len(normalized) < 6:
        return False
    return True


def parse_account_ids(lines: Iterable[TextLine], fallback: str) -> list[str]:
    ids: list[str] = []
    for item in lines:
        match = ACCOUNT_ID_RE.search(item.text)
        if match:
            account_id = match.group(1).strip().upper()
            if not _is_valid_account_id(account_id):
                continue
            if account_id not in ids:
                ids.append(account_id)
    if not ids:
        ids.append(fallback)
    return ids


def extract_account_id_from_text(line: str) -> str | None:
    match = ACCOUNT_ID_RE.search(line)
    if not match:
        return None
    account_id = match.group(1).strip().upper()
    if not _is_valid_account_id(account_id):
        return None
    return account_id
