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
from dataclasses import dataclass
from datetime import date

from ..pdf_text import PdfText
from .helpers import parse_money, parse_option_expiry
from .registry import register
from .types import (
    ParsedAccount,
    ParsedCashBalance,
    ParsedInstrument,
    ParsedPosition,
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
RE_ACCT_NUM = re.compile(r"Your Account Number:\s+(\d{3}[- ]?\d{5}-\d-\d)")
RE_ACCT_TYPE = re.compile(r"^\s*(Margin|Cash|RRSP|TFSA|RRIF|RESP|LIRA)\s*-\s*(Long|Short)?",
                          re.IGNORECASE | re.MULTILINE)

# Activity row: "JAN. 06 BOUGHT NUTRIEN LTD ..." — we capture date prefix.
RE_ACT_DATE = re.compile(r"^([A-Z]+)\.?\s+(\d{1,2})\s+([A-Z][A-Z .'/&-]+?)\s+(.*)$")
RE_OPENING_BAL = re.compile(r"Opening Balance\s*\([^)]+\)\s+\$?\s*\(?(-?[\d,]+(?:\.\d+)?)\)?")
RE_CLOSING_BAL = re.compile(r"Closing Balance\s*\([^)]+\)\s+\$?\s*\(?(-?[\d,]+(?:\.\d+)?)\)?")

# RBC option position: "CALL .BCE 09/20/24 54   40   0.020   80.00 ²   $80.00"
RE_RBC_OPT_POS = re.compile(
    r"^(CALL|PUT)\s+\.?([A-Z]{1,6})\s+(\d{2}/\d{2}/\d{2})\s+(\d+(?:\.\d+)?)\s+"
    r"(-?\d[\d,]*-?)\s+(\d+(?:\.\d+)?)\s+(-?\$?[\d,]+(?:\.\d+)?-?)\s*[#²³¤*]?\s+"
    r"(-?\$?[\d,]+(?:\.\d+)?-?)"
)

# Activity option line e.g. "PUT .NTR 09/20/24 75 8" (assignment)
RE_RBC_OPT_TXN = re.compile(
    r"\b(CALL|PUT)\s+\.?([A-Z]{1,6})\s+(\d{2}/\d{2}/\d{2})\s+(\d+(?:\.\d+)?)\s+(-?\d+)"
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
    "TRANSFER": "transfer_in",
    "JOURNAL": "journal", "JNL": "journal",
    "REINVEST": "dividend",
    "FEE": "fee",
    "ADJUSTMENT": "adjustment",
    "WITHDRAWAL": "withdrawal",
    "DEPOSIT": "deposit",
    "CONTRIBUTION": "deposit",
    "RETURN OF CAPITAL": "return_of_capital",
}


@dataclass
class _Block:
    currency: str
    text: str


def _is_annual(relpath: str, text: str) -> bool:
    rl = relpath.lower()
    if "_annual_report" in rl or "annualreport" in rl:
        return True
    tl = text.lower()
    return ("annual investment report" in tl
            or "annual investment performance report" in tl)


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


def _split_currency_blocks(text: str) -> list[_Block]:
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
            blocks.append(_Block(currency=cur_ccy, text=text[cur_start:m.start()]))
            cur_ccy = ccy
            cur_start = m.start()
    if cur_ccy is not None and cur_start is not None:
        blocks.append(_Block(currency=cur_ccy, text=text[cur_start:]))
    return blocks


def _classify_activity(verb: str, desc: str = "") -> str | None:
    v = verb.upper().strip()
    # Try longest prefix match
    for k, t in ACT_VERBS.items():
        if v.startswith(k):
            # Refine WIRE TFR direction by description
            if k == "WIRE TFR":
                return "transfer_out" if "TO" in desc.upper() else "transfer_in"
            return t
    return None


def _parse_asset_review(body: str, currency: str, stmt: ParsedStatement) -> None:
    section = "Common Shares"
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("___") or s.startswith("Total ") or "Asset Review" in s \
           or s.startswith("SECURITY") or s.startswith("SYMBOL") or "Exchange rate" in s:
            continue
        if s in {"Common Shares", "Preferred Shares", "Foreign Securities",
                 "Mutual Funds", "Fixed Income", "Other"}:
            section = s
            continue

        if section == "Other":
            mo = RE_RBC_OPT_POS.match(s)
            if not mo:
                continue
            cp, root, ddstr, strike_s, qty_s, mp_s, book_s, mv_s = mo.groups()
            expiry = parse_option_expiry(ddstr)
            instr = ParsedInstrument(
                asset_type="option", symbol=root, currency=currency,
                option_root=root, option_expiry=expiry, option_strike=float(strike_s),
                option_type=cp, option_multiplier=100,
            )
            stmt.positions.append(ParsedPosition(
                instrument=instr,
                quantity=parse_money(qty_s) or 0.0,
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
            continue
        name, sym, qty_s, price_s, book_s, mv_s = m.groups()
        atype = ("mutual_fund" if section == "Mutual Funds" else
                 "etf" if "ETF" in name.upper() else "equity")
        instr = ParsedInstrument(
            asset_type=atype, symbol=sym, currency=currency, name=name.strip()[:120],
        )
        stmt.positions.append(ParsedPosition(
            instrument=instr, quantity=parse_money(qty_s) or 0.0,
            avg_cost=None, book_value=parse_money(book_s),
            market_price=parse_money(price_s), market_value=parse_money(mv_s),
            unrealized_pnl=None, currency=currency, raw_line=ln,
        ))


def _parse_activity(body: str, currency: str, year: int,
                    stmt: ParsedStatement) -> None:
    opening = closing = None
    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            continue
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
            opening = parse_money(mo.group(1)); continue
        mc = RE_CLOSING_BAL.search(s)
        if mc:
            closing = parse_money(mc.group(1)); continue

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
            stmt.quarantine.append((ln, f"unknown verb: {verb_part}"))
            continue

        # Pull DEBIT and CREDIT trailing numbers.
        nums = re.findall(r"-?\$?[\d,]+(?:\.\d+)?-?", full)
        qty = price = amount = None
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
            # Open vs close: RBC doesn't say, infer by sign.
            if txn_type == "buy":
                txn_type = "option_buy_to_open" if (qty or 0) > 0 else "option_buy_to_close"
            else:
                txn_type = "option_sell_to_open" if (qty or 0) < 0 else "option_sell_to_close"
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

        # Derive instrument from leading description tokens
        instrument = None
        if txn_type in {"buy", "sell", "dividend", "distribution",
                        "return_of_capital"}:
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
                known = resolve_ticker(cleaned)
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
                    )

        stmt.transactions.append(ParsedTxn(
            trade_date=trade_date, settle_date=None, txn_type=txn_type,
            instrument=instrument, quantity=qty, price=price,
            gross_amount=None, commission=None, other_fees=None,
            net_amount=amount, currency=currency,
            description=full, raw_line=ln,
        ))

    if opening is not None or closing is not None:
        stmt.cash_balances.append(ParsedCashBalance(
            currency=currency, opening_balance=opening,
            closing_balance=closing or 0.0,
        ))


# ----------------------------------------------------------------- Parser
class RBCParser:
    NAME = "rbc"
    VERSION = "1.0.0"

    def can_handle(self, folder_name: str, first_page_text: str) -> bool:
        if folder_name == "RBC Invest Direct":
            return True
        return "RBC Direct Investing" in first_page_text

    def parse(self, pdf: PdfText) -> ParseResult:
        result = ParseResult(parser_name=self.NAME, parser_version=self.VERSION)
        text = pdf.full_text

        if _is_annual(pdf.relpath, text):
            # Emit empty annual entry to record the file
            ym = re.search(r"(\d{4})", pdf.relpath)
            year = int(ym.group(1)) if ym else None
            acct_m = RE_ACCT_NUM.search(text)
            acct = acct_m.group(1) if acct_m else None
            if acct is None:
                # Fall back to filename: 66844715-... -> 668-44715-?-?
                fn = re.search(r"(\d{3})(\d{5})", pdf.relpath)
                if fn:
                    acct = f"{fn.group(1)}-{fn.group(2)}-?-?"
            if acct and year:
                result.statements.append(ParsedStatement(
                    account=ParsedAccount(account_number=acct,
                                          account_type="Margin",
                                          base_currency="CAD"),
                    period_start=f"{year}-01-01", period_end=f"{year}-12-31",
                    statement_type="annual",
                ))
            return result

        # Year is in the block header line.
        for block in _split_currency_blocks(text):
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

            stmt = ParsedStatement(
                account=ParsedAccount(account_number=acct, account_type=atype,
                                      base_currency=block.currency),
                period_start=ps, period_end=pe, statement_type="monthly",
            )

            # Asset Review block
            ar = re.search(r"Asset Review", block.text)
            ac = re.search(r"Account Activity", block.text)
            if ar and ac and ac.start() > ar.start():
                _parse_asset_review(block.text[ar.end():ac.start()],
                                    block.currency, stmt)
                # Activity body extends until FOOTNOTES heading. The
                # "-CONTINUEDONNEXTPAGE-" markers are page footers, not
                # section terminators — we filter them out at the line level.
                tail = block.text[ac.end():]
                fn = re.search(r"\bFOOTNOTES\b", tail)
                act_body = tail[:fn.start()] if fn else tail
                _parse_activity(act_body, block.currency, year, stmt)

            result.statements.append(stmt)
        return result


register(RBCParser())
