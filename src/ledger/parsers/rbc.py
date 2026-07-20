"""RBC Direct Investing parser.

A single PDF often contains BOTH a "Cdn. Dollar Statement" and a
"U.S. Dollar Statement" block for the same root account number. Each is
emitted as a separate ParsedStatement.

Layout per block:

    Order Execution Only <MMM. DD>
    {Cdn. Dollar Statement | U.S. Dollar Statement} <YYYY>
    Your Account Number: NNN-NNNNN-D-D
    Date of Last Statement: <MMM. DD, YYYY>
    Asset Summary ...
    Asset Review ...
        SECURITY  SYMBOL  QUANTITY/SEGREGATED  MKT.PRICE  BOOK COST  MARKET VALUE
        Common Shares / Preferred Shares / Foreign Securities / Mutual Funds / Other
    Account Activity ...
        DATE  ACTIVITY  DESCRIPTION  QUANTITY/RATE  DEBIT  CREDIT
        Opening Balance (... ) $X
        ...
        Closing Balance (...) $X
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from ..pdf_text import PdfText
from .helpers import parse_money, parse_option_expiry
from .layout import (
    PageTextIndex,
    attach_source_spans,
    declare_snapshot_scopes,
    quarantine_unsupported_rows,
)
from .registry import register
from .types import (
    ParsedAccount,
    ParsedAnnualPerformance,
    ParsedCashBalance,
    ParsedInstrument,
    ParsedPosition,
    ParsedQuarantine,
    ParsedStatement,
    ParsedTxn,
    ParseResult,
)

_MONTH_ABBR = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    "JUNE": 6, "JULY": 7,
}


RE_BLOCK_HDR = re.compile(
    r"\b(Cdn|U\.S\.)\.?\s+Dollar\s+Statement\s+(\d{4})", re.IGNORECASE,
)
RE_PERIOD_END = re.compile(
    r"^Order Execution Only\s+([A-Z]+)\.?\s+(\d{1,2})", re.MULTILINE,
)
RE_LAST_STMT = re.compile(
    r"Date of Last Statement:\s+([A-Z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE,
)
RE_ANNUAL_PERIOD = re.compile(
    r"For the period from\s+([A-Z][a-z]+)\s*(\d{1,2}),\s*(\d{4})\s+"
    r"to\s+([A-Z][a-z]+)\s*(\d{1,2}),\s*(\d{4})",
)
RE_ACCT_NUM = re.compile(
    r"Your Account Number:\s+(\d{3}[- ]?\d{5}(?:-\d-\d)?)"
)
RE_ACCT_TYPE = re.compile(r"^\s*(Margin|Cash|RRSP|TFSA|RRIF|RESP|LIRA)\s*-\s*(Long|Short)?",
                          re.IGNORECASE | re.MULTILINE)

# Activity row: "JAN. 06 BOUGHT NUTRIEN LTD ..." — we capture date prefix.
RE_ACT_DATE = re.compile(r"^([A-Z]+)\.?\s*(\d{1,2})\s+([A-Z][A-Z0-9 .'/&-]+?)\s+(.*)$")
RE_OPENING_BAL = re.compile(r"Opening\s*Balance\s*\([^)]+\)\s+\$?\s*\(?(-?[\d,]+(?:\.\d+)?)\)?")
RE_CLOSING_BAL = re.compile(r"Closing\s*Balance\s*\([^)]+\)\s+\$?\s*\(?(-?[\d,]+(?:\.\d+)?)\)?")

# RBC option position: "CALL .BCE 09/20/24 54   40   0.020   80.00 ²   $80.00"
RE_RBC_OPT_POS = re.compile(
    r"^(CALL|PUT)\s+\.?([A-Z]{1,6})\s+(\d{2}/\d{2}/\d{2})\s+(\d+(?:\.\d+)?)\s+"
    r"(-?\d[\d,]*-?)\s+(\d+(?:\.\d+)?)\s+(-?\$?[\d,]+(?:\.\d+)?-?)\s*[#²³¤*]?\s+"
    r"(-?\$?[\d,]+(?:\.\d+)?-?)"
)

# Activity option line e.g. "PUT .NTR 09/20/24 75 8" (assignment)
RE_RBC_OPT_TXN = re.compile(
    r"\b(CALL|PUT)\s+\.?([A-Z]{1,6})\s+(\d{2}/\d{2}/\d{2})\s+(\d+(?:\.\d+)?)\s+(-?\d[\d,]*-?)"
)

ACT_VERBS = {
    "BOUGHT": "buy", "SOLD": "sell",
    "DIVIDEND": "dividend", "DIST.": "distribution", "DIST": "distribution",
    "INTEREST": "interest_income", "INT FR": "interest_income",
    "INTEREST EXPENSE": "interest_expense",
    "ASSIGN.": "option_assignment", "ASSIGN": "option_assignment",
    "EXERCISE": "option_exercise",
    "EXPIRED": "option_expiration",
    "WIRE TFR": "transfer_out",  # direction inferred from text
    "TFR OUT": "transfer_out",
    "TFR IN": "transfer_in",
    "TFRIN": "transfer_in",
    "TRFIN": "transfer_in",
    "TRANSFER": "transfer_in",
    "JOURNAL": "journal", "JNL": "journal",
    "REINVEST": "dividend",
    "FEE": "fee",
    "NONRES TX": "tax_withholding",
    "NON-RES TAX": "tax_withholding",
    "ADJUSTMENT": "adjustment",
    "WITHDRAWAL": "withdrawal",
    "DEPOSIT": "deposit",
    "CONTRIBUTION": "deposit",
    "RETURN OF CAPITAL": "return_of_capital",
    "NAME CHANGE": "name_change",
    "SYMBOL CHANGE": "name_change",
    "TICKER CHANGE": "name_change",
}


@dataclass
class _Block:
    currency: str
    text: str
    page_numbers: tuple[int, ...] = ()


def _layout_cash_effects(pdf: PdfText) -> dict[str, list[float]]:
    """Map RBC visual rows to their net printed debit/credit cash effect.

    RBC text extraction preserves numbers but loses the empty debit/credit
    cells. Some rows also print *both* a debit (for example, non-resident tax)
    and a credit (the gross dividend). Derive the cash columns from each page's
    header and return ``credits - debits`` instead of trusting the last number.
    Text-only fixtures and pypdf fallback return no effects and retain the
    parser's semantic signs.
    """
    effects: dict[str, list[float]] = defaultdict(list)
    money = re.compile(r"^\(?-?\$?[\d,]+(?:\.\d+)?\)?-?$")
    for lines in pdf.page_lines:
        cash_start = cutoff = None
        for line in lines:
            rate = next(
                (
                    word
                    for word in line.words
                    if word.text.upper().lstrip("\\/") == "RATE"
                ),
                None,
            )
            debit = next(
                (word for word in line.words if word.text.upper() == "DEBIT"),
                None,
            )
            credit = next(
                (word for word in line.words if word.text.upper() == "CREDIT"),
                None,
            )
            if debit is not None and credit is not None:
                debit_center = (debit.x0 + debit.x1) / 2
                credit_center = (credit.x0 + credit.x1) / 2
                cutoff = (debit_center + credit_center) / 2
                if rate is not None:
                    rate_center = (rate.x0 + rate.x1) / 2
                    cash_start = (rate_center + debit_center) / 2
                else:
                    # The RATE word can be split by a PDF font encoding. The
                    # debit/credit spacing still gives a conservative left
                    # edge that excludes quantity and price columns.
                    cash_start = debit_center - (credit_center - debit_center) * 0.75
                break
        if cutoff is None or cash_start is None:
            continue
        for line in lines:
            debit_values: list[float] = []
            credit_values: list[float] = []
            saw_numeric = False
            for word in line.words:
                if not money.fullmatch(word.text):
                    continue
                saw_numeric = True
                center = (word.x0 + word.x1) / 2
                if center < cash_start:
                    continue
                value = parse_money(word.text)
                if value is None:
                    continue
                if center < cutoff:
                    debit_values.append(abs(value))
                else:
                    credit_values.append(abs(value))
            if not saw_numeric:
                continue
            key = re.sub(r"\s+", " ", line.text).strip().upper()
            effects[key].append(round(sum(credit_values) - sum(debit_values), 2))
    return dict(effects)


def _take_cash_effect(
    effects: dict[str, list[float]],
    line: str,
) -> float | None:
    key = re.sub(r"\s+", " ", line).strip().upper()
    matches = effects.get(key)
    return matches.pop(0) if matches else None


def _is_annual(relpath: str, text: str) -> bool:
    rl = relpath.lower()
    if "_annual_report" in rl or "annualreport" in rl:
        return True
    tl = text.lower()
    return ("annual investment report" in tl
            or "annual investment performance report" in tl)


def _parse_annual_period(text: str, fallback_year: int | None) -> tuple[str, str] | None:
    match = RE_ANNUAL_PERIOD.search(text)
    if match:
        try:
            start_month = _MONTH_ABBR[match.group(1).upper()[:3]]
            start_day = int(match.group(2))
            start_year = int(match.group(3))
            end_month = _MONTH_ABBR[match.group(4).upper()[:3]]
            end_day = int(match.group(5))
            end_year = int(match.group(6))
            return date(start_year, start_month, start_day).isoformat(), date(end_year, end_month, end_day).isoformat()
        except (KeyError, ValueError):
            pass
    if fallback_year is None:
        return None
    return f"{fallback_year}-01-01", f"{fallback_year}-12-31"


def _parse_since_date(block: str) -> str | None:
    match = re.search(r"Since([A-Z][a-z]+)\s*(\d{1,2}),\s*(\d{4})", block)
    if not match:
        return None
    month = _MONTH_ABBR.get(match.group(1).upper()[:3])
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(2))).isoformat()
    except ValueError:
        return None


def _annual_pair(block: str, label: str) -> tuple[float | None, float | None]:
    money = r"(-?[\d,]+(?:\.\d+)?|-)"
    match = re.search(rf"{label}\s+{money}\s+{money}", block)
    if not match:
        return None, None
    return parse_money(match.group(1)), parse_money(match.group(2))


def _parse_percent(token: str) -> float | None:
    token = token.strip()
    if token == "-":
        return None
    return parse_money(token.rstrip("%"))


def _annual_period_or_since(period_value: float | None, since_value: float | None) -> float | None:
    return period_value if period_value is not None else since_value


def _annual_returns(block: str) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    match = re.search(
        r"Money-weighted rate ofreturn\s+Past.*?\n.*?\n([^\n]+)",
        block,
        re.DOTALL,
    )
    if not match:
        return None, None, None, None, None
    tokens = re.findall(r"-?\d+(?:\.\d+)?%|-", match.group(1))
    values = [_parse_percent(token) for token in tokens[:5]]
    while len(values) < 5:
        values.append(None)
    return values[0], values[1], values[2], values[3], values[4]


def _parse_annual_performance(text: str, period_start: str, period_end: str) -> list[ParsedAnnualPerformance]:
    anchors = [("CAD", "Your Canadian dollar account"), ("USD", "Your U.S. dollar account")]
    out: list[ParsedAnnualPerformance] = []
    for idx, (currency, heading) in enumerate(anchors):
        start = text.find(heading)
        if start < 0:
            continue
        next_starts = [text.find(next_heading, start + len(heading)) for _, next_heading in anchors[idx + 1:]]
        next_starts.append(text.find("Additionalnotes:", start + len(heading)))
        end_candidates = [pos for pos in next_starts if pos > start]
        end = min(end_candidates) if end_candidates else len(text)
        block = text[start:end]
        beginning, beginning_since = _annual_pair(block, "Beginningmarketvalue")
        deposits, deposits_since = _annual_pair(block, "Depositsandtransfers-in")
        withdrawals, withdrawals_since = _annual_pair(block, "Withdrawalsandtransfers-out")
        net_return, net_return_since = _annual_pair(block, "Netinvestmentreturn")
        ending, ending_since = _annual_pair(block, r"Endingmarketvalueat[A-Z][a-z]+\d{1,2},\d{4}")
        mwrr_1y, mwrr_3y, mwrr_5y, mwrr_10y, mwrr_since = _annual_returns(block)
        out.append(ParsedAnnualPerformance(
            currency=currency,
            period_start=period_start,
            period_end=period_end,
            since_date=_parse_since_date(block),
            beginning_market_value=_annual_period_or_since(beginning, beginning_since),
            deposits_transfers_in=_annual_period_or_since(deposits, deposits_since),
            withdrawals_transfers_out=_annual_period_or_since(withdrawals, withdrawals_since),
            net_investment_return=_annual_period_or_since(net_return, net_return_since),
            ending_market_value=_annual_period_or_since(ending, ending_since),
            money_weighted_1y=mwrr_1y,
            money_weighted_3y=mwrr_3y,
            money_weighted_5y=mwrr_5y,
            money_weighted_10y=mwrr_10y,
            money_weighted_since=mwrr_since,
        ))
    return out


def _parse_block_period(text: str, year: int) -> tuple[str, str] | None:
    m_end = RE_PERIOD_END.search(text)
    m_last = RE_LAST_STMT.search(text)
    if not m_end:
        return None
    end_mon = _MONTH_ABBR.get(m_end.group(1).upper())
    end_day = int(m_end.group(2))
    if not end_mon:
        return None
    try:
        period_end = date(year, end_mon, end_day).isoformat()
    except ValueError:
        return None
    if m_last:
        try:
            last_y = int(m_last.group(3))
            last_m = _MONTH_ABBR.get(m_last.group(1).upper())
            last_d = int(m_last.group(2))
            if last_m:
                from datetime import timedelta
                d = date(last_y, last_m, last_d)
                period_start = (d + timedelta(days=1)).isoformat()
                return period_start, period_end
        except ValueError:
            pass
    # Fallback: first day of month
    return (date(year, end_mon, 1).isoformat(), period_end)


def _split_currency_blocks(
    text: str,
    *,
    page_index: PageTextIndex | None = None,
) -> list[_Block]:
    """Each PDF page repeats the 'Cdn. Dollar Statement' / 'U.S. Dollar Statement'
    header. We only split when currency CHANGES."""
    matches = list(RE_BLOCK_HDR.finditer(text))
    blocks: list[_Block] = []
    cur_ccy: str | None = None
    cur_start: int | None = None
    for m in matches:
        ccy = "CAD" if m.group(1).upper() == "CDN" else "USD"
        if cur_ccy is None:
            cur_ccy = ccy
            cur_start = m.start()
        elif ccy != cur_ccy:
            end = m.start()
            blocks.append(_Block(
                currency=cur_ccy,
                text=text[cur_start:end],
                page_numbers=page_index.pages_for_range(cur_start, end) if page_index else (),
            ))
            cur_ccy = ccy
            cur_start = m.start()
    if cur_ccy is not None and cur_start is not None:
        blocks.append(_Block(
            currency=cur_ccy,
            text=text[cur_start:],
            page_numbers=(
                page_index.pages_for_range(cur_start, len(text)) if page_index else ()
            ),
        ))
    return blocks


def _classify_activity(verb: str, desc: str = "") -> str | None:
    v = verb.upper().strip()
    # Try longest prefix match
    for k, t in ACT_VERBS.items():
        if v.startswith(k):
            # Refine WIRE TFR direction by description
            if k == "WIRE TFR":
                return "transfer_out" if "TO" in desc.upper() else "transfer_in"
            if k == "TRANSFER":
                desc_upper = desc.upper()
                return "transfer_out" if "TRANSFER TO" in desc_upper or " TO " in desc_upper else "transfer_in"
            return t
    return None


def _parse_asset_review(body: str, currency: str, stmt: ParsedStatement) -> bool:
    section = "Common Shares"
    saw_section = False
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("___") or s.startswith("Total ") or "Asset Review" in s \
           or s.startswith("SECURITY") or s.startswith("SYMBOL") or "Exchange rate" in s:
            continue
        if s in {"Common Shares", "Preferred Shares", "Foreign Securities",
                  "Mutual Funds", "Fixed Income", "Other"}:
            section = s
            saw_section = True
            continue

        if section == "Other":
            mo = RE_RBC_OPT_POS.match(s)
            if not mo:
                if re.search(r"\d", s):
                    stmt.quarantine.append(ParsedQuarantine(
                        raw_line=ln,
                        reason="unrecognized option asset-review row",
                    ))
                continue
            cp, root, ddstr, strike_s, qty_s, mp_s, book_s, mv_s = mo.groups()
            expiry = parse_option_expiry(ddstr)
            instr = ParsedInstrument(
                asset_type="option", symbol=root, currency=currency,
                option_root=root, option_expiry=expiry, option_strike=float(strike_s),
                option_type=cp, option_multiplier=100,
            )
            quantity = parse_money(qty_s)
            if quantity is None:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="option holding has no valid quantity",
                ))
                continue
            stmt.positions.append(ParsedPosition(
                instrument=instr,
                quantity=quantity,
                avg_cost=None, book_value=parse_money(book_s),
                market_price=parse_money(mp_s), market_value=parse_money(mv_s),
                unrealized_pnl=None, currency=currency, raw_line=ln,
            ))
            continue

        # Equity / mutual-fund holding line:
        # e.g. "CAMECO CORP CCO 3,022 168.410 155,728.83 $508,935.02"
        # Pattern: <name (greedy)> <SYMBOL> <qty> <price> <bookcost> <mktvalue>
        m = re.match(
            r"^(.+?)\s+([A-Z][A-Z0-9.\-]{0,8})\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+"
            r"(-?\$?[\d,]+(?:\.\d+)?-?)\s+(-?\$?[\d,]+(?:\.\d+)?-?)$",
            s,
        )
        if not m:
            if re.search(r"\d", s):
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="unrecognized asset-review row",
                ))
            continue
        name, sym, qty_s, price_s, book_s, mv_s = m.groups()
        atype = ("mutual_fund" if section == "Mutual Funds" else
                 "etf" if "ETF" in name.upper() else "equity")
        instr = ParsedInstrument(
            asset_type=atype, symbol=sym, currency=currency, name=name.strip()[:120],
        )
        quantity = parse_money(qty_s)
        if quantity is None:
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="holding has no valid quantity",
            ))
            continue
        stmt.positions.append(ParsedPosition(
            instrument=instr, quantity=quantity,
            avg_cost=None, book_value=parse_money(book_s),
            market_price=parse_money(price_s), market_value=parse_money(mv_s),
            unrealized_pnl=None, currency=currency, raw_line=ln,
        ))
    return saw_section


def _parse_activity(
    body: str,
    currency: str,
    year: int,
    stmt: ParsedStatement,
    cash_effects: dict[str, list[float]],
) -> bool:
    opening = closing = None
    cash_lines: list[str] = []
    cash_complete = False
    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            continue
        cash_effect = _take_cash_effect(cash_effects, s)
        # Skip page footers, headers, and column-name rows.
        if (s.startswith("-CONTINUEDONNEXTPAGE-") or s.startswith("0017") or
            s.startswith("Member-Canadian") or s.startswith("DATE ACTIVITY") or
            s.startswith("PRICE") or s.startswith("Account Activity") or
            s.startswith("Order Execution Only") or s.startswith("Cdn. Dollar Statement") or
            s.startswith("U.S. Dollar Statement") or s.startswith("Your Account Number:") or
            s.startswith("___") or s.startswith("YAT") or s.startswith("WAH FONG") or
            s.startswith("JERT") or s.startswith("Page ")):
            continue
        mo = RE_OPENING_BAL.search(s)
        if mo:
            opening = cash_effect if cash_effect is not None else parse_money(mo.group(1))
            cash_lines.append(ln)
            continue
        mc = RE_CLOSING_BAL.search(s)
        if mc:
            closing = cash_effect if cash_effect is not None else parse_money(mc.group(1))
            if closing is None:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="closing cash balance has no valid amount",
                ))
            else:
                cash_lines.append(ln)
                cash_complete = True
            # Open Orders and other non-ledger sections can contain dated BUY
            # rows after the closing balance. They are instructions, not
            # executed transactions, so the activity scope ends here.
            break
        if "closing balance" in s.lower():
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="closing cash balance has no valid amount",
            ))
            continue

        m = RE_ACT_DATE.match(s)
        if not m:
            # continuation line
            if stmt.transactions:
                last = stmt.transactions[-1]
                last.description = (last.description or "") + " | " + s
            continue
        mon, dd, verb_part, rest = m.groups()
        mn = _MONTH_ABBR.get(mon.upper())
        if not mn:
            continue
        try:
            trade_date = date(year, mn, int(dd)).isoformat()
        except ValueError:
            continue

        # The "verb" may consume multiple words (e.g. "INT FR"). Try each prefix.
        full = (verb_part + " " + rest).strip()
        txn_type = _classify_activity(verb_part, rest)
        if txn_type is None:
            # Try the first 2-3 tokens as compound verb
            tokens = full.split()
            for ntok in (3, 2, 1):
                if ntok <= len(tokens):
                    candidate = " ".join(tokens[:ntok])
                    txn_type = _classify_activity(candidate, full)
                    if txn_type:
                        break
        if txn_type is None:
            if cash_effect is not None and abs(cash_effect) > 0:
                # Some RBC rows leave the Activity column blank but still
                # print a dated description and an unambiguous debit/credit
                # cash value. Preserve that source fact as a generic
                # adjustment; do not invent an income/security subtype.
                stmt.transactions.append(ParsedTxn(
                    trade_date=trade_date,
                    settle_date=None,
                    txn_type="adjustment",
                    instrument=None,
                    quantity=None,
                    price=None,
                    gross_amount=None,
                    commission=None,
                    other_fees=None,
                    net_amount=cash_effect,
                    currency=currency,
                    description=full,
                    raw_line=ln,
                ))
                continue
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason=f"unknown verb: {verb_part}",
            ))
            continue

        # Pull DEBIT and CREDIT trailing numbers.
        nums = re.findall(r"-?\$?[\d,]+(?:\.\d+)?-?", full)
        qty = price = amount = None
        security_transfer = (
            txn_type in {"transfer_in", "transfer_out", "journal"}
            and cash_effect == 0.0
            and bool(nums)
        )
        # If the description contains an option token, treat as option txn.
        opm = RE_RBC_OPT_TXN.search(full)
        if txn_type in {"buy", "sell"} and opm:
            cp, root, ddstr, strike_s, qty_s = opm.groups()
            expiry = parse_option_expiry(ddstr)
            instrument = ParsedInstrument(
                asset_type="option", symbol=root, currency=currency,
                option_root=root, option_expiry=expiry, option_strike=float(strike_s),
                option_type=cp, option_multiplier=100,
            )
            qty = parse_money(qty_s)
            # Trailing numbers after the option token: <price> <amount>
            tail = full[opm.end():]
            tail_nums = re.findall(r"-?\$?[\d,]+(?:\.\d+)?-?", tail)
            if len(tail_nums) >= 2:
                price = parse_money(tail_nums[0])
                amount = parse_money(tail_nums[1])
            elif tail_nums:
                amount = parse_money(tail_nums[0])
            if txn_type == "buy" and amount and amount > 0:
                amount = -amount
            if cash_effect is not None:
                amount = cash_effect
            # Open vs close: RBC doesn't say, infer by sign.
            if txn_type == "buy":
                if qty is not None:
                    if qty > 0:
                        txn_type = "option_buy_to_open"
                    elif qty < 0:
                        txn_type = "option_buy_to_close"
            elif qty is not None:
                if qty < 0:
                    txn_type = "option_sell_to_open"
                elif qty > 0:
                    txn_type = "option_sell_to_close"
            stmt.transactions.append(ParsedTxn(
                trade_date=trade_date, settle_date=None, txn_type=txn_type,
                instrument=instrument, quantity=qty, price=price,
                gross_amount=None, commission=None, other_fees=None,
                net_amount=amount, currency=currency,
                description=full, raw_line=ln,
            ))
            continue
        if txn_type in {"buy", "sell"}:
            if len(nums) >= 3:
                qty = parse_money(nums[-3])
                price = parse_money(nums[-2])
                amount = parse_money(nums[-1])
                if txn_type == "buy" and amount and amount > 0:
                    amount = -amount
            elif len(nums) == 2 and cash_effect is not None:
                # Nominal-cost corporate distributions can print quantity and
                # only one other value. Geometry distinguishes a rate-column
                # value with zero cash from a cash-column amount.
                qty = parse_money(nums[0])
                if cash_effect == 0.0:
                    price = parse_money(nums[1])
                    amount = 0.0
                else:
                    amount = parse_money(nums[1])
                    if txn_type == "buy" and amount and amount > 0:
                        amount = -amount
        elif txn_type in {"dividend", "distribution", "interest_income"}:
            if nums:
                amount = parse_money(nums[-1])
                if len(nums) >= 2:
                    # Could be (rate, amount)
                    pass
        elif txn_type in {"option_expiration", "option_assignment", "option_exercise"}:
            opm = RE_RBC_OPT_TXN.search(full)
            if opm:
                cp, root, ddstr, strike_s, qty_s = opm.groups()
                expiry = parse_option_expiry(ddstr)
                instr = ParsedInstrument(
                    asset_type="option", symbol=root, currency=currency,
                    option_root=root, option_expiry=expiry, option_strike=float(strike_s),
                    option_type=cp, option_multiplier=100,
                )
                qty = parse_money(qty_s)
                stmt.transactions.append(ParsedTxn(
                    trade_date=trade_date, settle_date=None, txn_type=txn_type,
                    instrument=instr, quantity=qty, price=None,
                    gross_amount=None, commission=None, other_fees=None,
                    net_amount=None, currency=currency,
                    description=full, raw_line=ln,
                ))
                continue
        else:
            if nums:
                amount = parse_money(nums[-1])

        if security_transfer:
            qty = parse_money(nums[-1])
            amount = None
            if txn_type != "journal" and qty is not None:
                txn_type = "transfer_out" if qty < 0 else "transfer_in"

        if cash_effect is not None:
            amount = cash_effect
        if txn_type == "interest_income" and amount is not None and amount < 0:
            txn_type = "interest_expense"

        # Derive instrument from leading description tokens
        instrument = None
        if txn_type in {"buy", "sell", "dividend", "distribution",
                        "return_of_capital"} or security_transfer:
            desc_only = re.split(
                r"\s+\(?-?\$?[\d,]+(?:\.\d+)?", full, maxsplit=1,
            )[0].strip()
            if desc_only:
                # Strip leading verbs (BOUGHT/SOLD/DIVIDEND/...) so the first
                # token isn't mistaken for a ticker. Then try a known-name map
                # before falling back to a synthetic symbol.
                from .name_resolver import (
                    resolve_ticker,
                    strip_leading_verbs,
                    synthetic_symbol,
                )
                cleaned = strip_leading_verbs(desc_only)
                known = resolve_ticker(cleaned, currency)
                if known is not None:
                    tkr, atype = known
                    instrument = ParsedInstrument(
                        asset_type=atype, symbol=tkr,
                        currency=currency, name=cleaned[:120],
                    )
                else:
                    sym = synthetic_symbol(cleaned) if cleaned else "UNKNOWN"
                    instrument = ParsedInstrument(
                        asset_type="equity", symbol=sym,
                        currency=currency, name=cleaned[:120],
                        resolution_method="unresolved_printed_identity",
                        resolution_confidence=0.0,
                    )

        stmt.transactions.append(ParsedTxn(
            trade_date=trade_date, settle_date=None, txn_type=txn_type,
            instrument=instrument, quantity=qty, price=price,
            gross_amount=None, commission=None, other_fees=None,
            net_amount=amount, currency=currency,
            description=full, raw_line=ln,
            cash_delta=0.0 if security_transfer else None,
        ))

    if closing is not None:
        stmt.cash_balances.append(ParsedCashBalance(
            currency=currency, opening_balance=opening,
            closing_balance=closing,
            raw_line="\n".join(cash_lines) or None,
        ))
    elif opening is not None:
        stmt.quarantine.append(ParsedQuarantine(
            raw_line="\n".join(cash_lines),
            reason="opening cash balance has no valid closing balance",
        ))
    return cash_complete


# ----------------------------------------------------------------- Parser
class RBCParser:
    NAME = "rbc"
    VERSION = "2.6.0"

    def can_handle(self, folder_name: str, first_page_text: str) -> bool:
        if folder_name == "RBC Invest Direct":
            return True
        return "RBC Direct Investing" in first_page_text

    def parse(self, pdf: PdfText) -> ParseResult:
        result = ParseResult(parser_name=self.NAME, parser_version=self.VERSION)
        page_index = PageTextIndex.from_pdf(pdf)
        text = page_index.text
        cash_effects = _layout_cash_effects(pdf)

        if _is_annual(pdf.relpath, text):
            # Emit empty annual entry to record the file
            ym = re.search(r"(20\d{2})", pdf.relpath)
            year = int(ym.group(1)) if ym else None
            acct_m = RE_ACCT_NUM.search(text)
            acct = acct_m.group(1) if acct_m else None
            if acct is None:
                # Fall back to filename: 66844715-... -> 668-44715-?-?
                fn = re.search(r"(\d{3})(\d{5})", pdf.relpath)
                if fn:
                    acct = f"{fn.group(1)}-{fn.group(2)}-?-?"
            if acct and year:
                annual_period = _parse_annual_period(text, year)
                if annual_period is None:
                    result.errors.append("could not parse annual performance period")
                    return result
                ps, pe = annual_period
                result.statements.append(ParsedStatement(
                    account=ParsedAccount(account_number=acct,
                                          account_type="Margin",
                                          base_currency="CAD"),
                    period_start=ps, period_end=pe,
                    statement_type="annual",
                    annual_performance=_parse_annual_performance(text, ps, pe),
                    page_numbers=page_index.all_pages,
                ))
            return result

        # RBC's CAD and USD blocks are scopes of one physical broker account
        # and period, not independent statements.  Preserve both blocks under
        # one ParsedStatement so persistence cannot overwrite either currency.
        statements: dict[tuple[str, str, str], ParsedStatement] = {}
        scope_state: dict[tuple[str, str, str], tuple[dict[str, str], dict[str, str]]] = {}

        # Year is in the block header line.
        for block in _split_currency_blocks(text, page_index=page_index):
            ym = re.search(r"Statement\s+(\d{4})", block.text)
            if not ym:
                continue
            year = int(ym.group(1))
            period = _parse_block_period(block.text, year)
            if not period:
                result.errors.append("could not parse period in block")
                continue
            ps, pe = period

            acct_m = RE_ACCT_NUM.search(block.text)
            if not acct_m:
                result.errors.append("could not parse account number")
                continue
            acct = acct_m.group(1).replace(" ", "")

            atype_m = RE_ACCT_TYPE.search(block.text)
            atype = atype_m.group(1).title() if atype_m else None

            key = (acct, ps, pe)
            stmt = statements.get(key)
            if stmt is None:
                stmt = ParsedStatement(
                    account=ParsedAccount(account_number=acct, account_type=atype,
                                          base_currency="CAD"),
                    period_start=ps, period_end=pe, statement_type="monthly",
                    page_numbers=block.page_numbers,
                )
                statements[key] = stmt
                scope_state[key] = ({}, {})
            else:
                stmt.page_numbers = tuple(
                    sorted(set(stmt.page_numbers) | set(block.page_numbers))
                )
            position_scopes, cash_scopes = scope_state[key]

            # Asset Review block
            ar = re.search(r"Asset Review", block.text)
            ac = re.search(r"Account Activity", block.text)
            if ar and ac and ac.start() > ar.start():
                if _parse_asset_review(block.text[ar.end():ac.start()],
                                       block.currency, stmt):
                    position_scopes[block.currency] = "complete"
                # Activity body extends until FOOTNOTES heading. The
                # "-CONTINUEDONNEXTPAGE-" markers are page footers, not
                # section terminators — we filter them out at the line level.
                tail = block.text[ac.end():]
                fn = re.search(r"\bFOOTNOTES\b", tail)
                act_body = tail[:fn.start()] if fn else tail
                if _parse_activity(
                    act_body,
                    block.currency,
                    year,
                    stmt,
                    cash_effects,
                ):
                    cash_scopes[block.currency] = "complete"

        for key, stmt in statements.items():
            position_scopes, cash_scopes = scope_state[key]
            declare_snapshot_scopes(
                stmt,
                position_scopes=position_scopes,
                cash_scopes=cash_scopes,
            )
            result.statements.append(stmt)
        quarantine_unsupported_rows(result)
        attach_source_spans(pdf, result, parser_name=self.NAME)
        return result


register(RBCParser())
