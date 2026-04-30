"""TD Direct Investing / TD Waterhouse parser.

Filename styles:
    Statement_<acct>_YYYY-MM.pdf       - modern monthly
    Statement_<acct>_YYYY_MM-MM.pdf    - legacy quarterly
    Statement_<acct>_YYYY_summary.pdf  - annual summary

A single PDF contains one or more account sub-statements identified by
"Account number: <acct>" + "Account type: Direct Trading - {CDN|US}". Each
emits a separate ParsedStatement with synthetic account_number
"<acct>-CDN" / "<acct>-US".

Option formats:
    Activity row (single line):
        Buy  PUT -100 SLV'26 FB@100  30   9.050   -27,187.50   -20,978.94
        Sell CALL-100 SLV'26 13FB@115 -30 8.450    25,302.51    4,323.57
        Buy  CALL-100 PAAS'27-US JA@60 20 11.550 -23,125.00 -12,636.88

    Position rows (two physical lines):
        CALL-100 PAAS'27-US  20  9.380  23,125.00  18,760.00  -4,365.00  0.98%
        JA@60
        CALL-100 SLV'26  30  0.640  4,007.49  1,920.00  -2,087.49  0.10%
        MR@115
        CALL-100 AMD'26  10  9.650  9,962.49  9,650.00  -312.49  0.50%
        18JN@300
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..pdf_text import PdfText
from .helpers import _OPT_MON, _third_friday, parse_money
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

_MON = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_MON_FULL = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}

RE_PERIOD_FULL = re.compile(
    r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})\s+to\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})",
)
# Legacy 2016-2017 format: "Statement for January 1 to January 31, 2016"
RE_PERIOD_LEGACY = re.compile(
    r"Statement for\s+([A-Z][a-z]+)\s+(\d{1,2})\s+to\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})",
)
RE_PERIOD_END_ONLY = re.compile(
    r"For the period ending\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})",
)
RE_ACCT_NUM = re.compile(r"Account number:\s+([A-Z0-9]+)")
RE_ACCT_TYPE = re.compile(r"Account type:\s+Direct Trading\s*-\s*(CDN|US)")
RE_BEGIN_BAL = re.compile(r"Beginning cash balance\s+\$?\(?(-?[\d,]+(?:\.\d+)?)\)?")
RE_END_BAL = re.compile(r"Ending cash balance\s+\$?\(?(-?[\d,]+(?:\.\d+)?)\)?")

# Option token in activity (single line). Captures: cp, mult sign, root,
# yy, [dd]mm, strike. Examples: "PUT -100 SLV'26 FB@100", "CALL-100 SLV'26 13FB@115"
RE_OPT_TOKEN = re.compile(
    r"(CALL|PUT)\s*[- ]\s*(?:-)?100\s+([A-Z][A-Z0-9.]{0,5})'(\d{2})(?:-US)?\s+"
    r"(\d{0,2})([A-Z]{2})@(\d+(?:\.\d+)?)"
)
# Expiry-only token used for stitching position rows: "[dd]mm@strike"
RE_OPT_TAIL = re.compile(r"^(\d{0,2})([A-Z]{2})@(\d+(?:\.\d+)?)$")

# A bare equity holding row: "BANK OF MONTREAL 1,600 SEG 174.230 45,606.97 278,768.00 233,161.03 16.87%"
# Symbol may appear on this line (in parens) or on the *next* line.
RE_HOLDING_LINE = re.compile(
    r"^(.+?)\s+([\d,]+(?:\.\d+)?)\s*(?:SEG\s+)?"
    r"([\d,]+(?:\.\d+)?)\s+(-?[\d,]+(?:\.\d+)?)\s+(-?[\d,]+(?:\.\d+)?)\s+"
    r"(-?[\d,]+(?:\.\d+)?)\s+(-?[\d,]+(?:\.\d+)?)\s*%$"
)
RE_TRAIL_SYM = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,8})\s*\)")

# Activity date prefix:  "Oct 31", "Sep 30"
RE_ACT_DATE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s+(.*)$"
)

ACT_VERBS = {
    "Buy": "buy",
    "Sell": "sell",
    "Dividend": "dividend",
    "Dividends": "dividend",
    "Distribution": "distribution",
    "Distributions": "distribution",
    "Interest": "interest_income",
    "Reinvestment": "dividend",
    "Expiration": "option_expiration",
    "Exercise Option": "option_exercise",
    "Exercise": "option_exercise",
    "Assignment": "option_assignment",
    "Assigned": "option_assignment",
    "Transfer": "transfer_in",
    "Contribution": "deposit",
    "Deposit": "deposit",
    "Withdrawal": "withdrawal",
    "Cash withdrawal": "withdrawal",
    "Fee": "fee",
    "Service Charge": "fee",
    "Foreign Tax": "tax_withholding",
    "Withholding Tax": "tax_withholding",
    "Non Resident Tax": "tax_withholding",
    "Tax": "tax_withholding",
    "Adjustment": "adjustment",
    "Journal": "journal",
    "Return of capital": "return_of_capital",
    "Stock split": "split",
    "Stock dividend": "dividend",
}


@dataclass
class _Sub:
    currency: str
    account_number: str
    text: str


def _is_summary(relpath: str) -> bool:
    rl = relpath.lower()
    return rl.endswith("_summary.pdf") or "_summary." in rl or "mid_year_summary" in rl


def _parse_period(text: str) -> tuple[str, str] | None:
    m = RE_PERIOD_FULL.search(text)
    if m:
        try:
            sm = _MON_FULL[m.group(1)]
            sd = int(m.group(2))
            sy = int(m.group(3))
            em = _MON_FULL[m.group(4)]
            ed = int(m.group(5))
            ey = int(m.group(6))
            return date(sy, sm, sd).isoformat(), date(ey, em, ed).isoformat()
        except (KeyError, ValueError):
            pass
    ml = RE_PERIOD_LEGACY.search(text)
    if ml:
        try:
            sm = _MON_FULL[ml.group(1)]
            sd = int(ml.group(2))
            em = _MON_FULL[ml.group(3)]
            ed = int(ml.group(4))
            y = int(ml.group(5))
            return date(y, sm, sd).isoformat(), date(y, em, ed).isoformat()
        except (KeyError, ValueError):
            pass
    m2 = RE_PERIOD_END_ONLY.search(text)
    if m2:
        try:
            em = _MON_FULL[m2.group(1)]
            ed = int(m2.group(2))
            ey = int(m2.group(3))
            return date(ey, em, 1).isoformat(), date(ey, em, ed).isoformat()
        except (KeyError, ValueError):
            pass
    return None


def _split_subs(text: str) -> list[_Sub]:
    """Split the PDF text on each 'Account number: X / Account type: Direct Trading - Y' header.

    For legacy 2016-2017 statements (no 'Account type:' literal), fall back
    to splitting on lines containing 'Direct Trading - (CDN|US)' as a
    standalone marker. Account number is then taken from the filename via
    a separate regex (caller responsibility).
    """
    subs: list[_Sub] = []
    marks: list[tuple[int, str, str]] = []
    for m in RE_ACCT_TYPE.finditer(text):
        start_search = max(0, m.start() - 200)
        an = None
        for am in RE_ACCT_NUM.finditer(text, start_search, m.start()):
            an = am.group(1)
        if not an:
            continue
        ccy = "CAD" if m.group(1) == "CDN" else "USD"
        marks.append((m.start(), an, ccy))

    if not marks:
        # Legacy fallback: find 'Direct Trading - CDN'/'... - US' standalone
        # markers; account number from "Account number\n<ID>" header.
        an_match = re.search(r"Account number\s*[\n\r]+\s*([A-Z0-9]+)", text)
        an_legacy = an_match.group(1) if an_match else None
        for m in re.finditer(r"Direct Trading\s*-\s*(CDN|US)\b", text):
            if not an_legacy:
                continue
            ccy = "CAD" if m.group(1) == "CDN" else "USD"
            marks.append((m.start(), an_legacy, ccy))

    if not marks:
        return subs
    cur_an, cur_ccy = marks[0][1], marks[0][2]
    cur_start = marks[0][0]
    for i in range(1, len(marks)):
        pos, an, ccy = marks[i]
        if (an, ccy) != (cur_an, cur_ccy):
            subs.append(_Sub(currency=cur_ccy, account_number=cur_an,
                             text=text[cur_start:pos]))
            cur_an, cur_ccy, cur_start = an, ccy, pos
    subs.append(_Sub(currency=cur_ccy, account_number=cur_an,
                     text=text[cur_start:]))
    return subs


def _option_expiry(yy: str, dd: str, mon: str) -> str | None:
    m = _OPT_MON.get(mon.upper())
    if not m:
        return None
    year = 2000 + int(yy)
    if dd:
        try:
            return date(year, m, int(dd)).isoformat()
        except ValueError:
            return None
    return _third_friday(year, m).isoformat()


def _classify(verb_phrase: str) -> str | None:
    # Try longest match first.
    for k in sorted(ACT_VERBS, key=len, reverse=True):
        if verb_phrase.startswith(k):
            return ACT_VERBS[k]
    return None


def _parse_holdings(body: str, currency: str, stmt: ParsedStatement) -> None:
    """Parse holdings table. Tracks current asset_type by subsection header."""
    section = None
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        i += 1
        if not s:
            continue

        # Section detection
        sl = s.lower()
        if sl.startswith("cash") and not RE_HOLDING_LINE.match(s):
            section = "cash"; continue
        if "common shares" in sl and ("canadian" in sl or "foreign" in sl or "us " in sl):
            section = "equity"; continue
        if "preferred" in sl and "shares" in sl:
            section = "equity"; continue
        if "mutual fund" in sl:
            section = "mutual_fund"; continue
        if "exchange traded fund" in sl or "etf" in sl:
            section = "etf"; continue
        if sl.startswith("options") or sl == "options":
            section = "option"; continue
        if sl.startswith("fixed income") or "bond" in sl:
            section = "bond"; continue
        if sl.startswith("equities") or "(continued)" in sl or sl.startswith("total ") \
           or sl.startswith("description") or sl.startswith("quantity") \
           or sl.startswith("holdings in"):
            continue
        if sl.startswith("definitions") or sl.startswith("an explanation"):
            break

        # Option holding line — first line has "(CALL|PUT)[-\s]?100 ROOT'YY[-US]?"
        # then numeric columns; the strike "[dd]MM@strike" is on the next line.
        opt_head = re.match(
            r"^(CALL|PUT)\s*[- ]\s*(?:-)?100\s+([A-Z][A-Z0-9.]{0,5})'(\d{2})(?:-US)?\s+(.*)$",
            s,
        )
        if opt_head:
            cp, root, yy, num_part = opt_head.groups()
            tail_line = ""
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                tail_line = lines[j].strip()
            tm = RE_OPT_TAIL.match(tail_line)
            if tm:
                dd, mon, strike = tm.groups()
                expiry = _option_expiry(yy, dd, mon)
                nums = re.findall(r"-?[\d,]+(?:\.\d+)?", num_part)
                qty = parse_money(nums[0]) if nums else None
                price = parse_money(nums[1]) if len(nums) > 1 else None
                book = parse_money(nums[2]) if len(nums) > 2 else None
                mv = parse_money(nums[3]) if len(nums) > 3 else None
                instr = ParsedInstrument(
                    asset_type="option", symbol=root, currency=currency,
                    option_root=root, option_expiry=expiry,
                    option_strike=float(strike) if strike else None,
                    option_type=cp, option_multiplier=100,
                )
                stmt.positions.append(ParsedPosition(
                    instrument=instr, quantity=qty or 0.0,
                    avg_cost=None, book_value=book, market_price=price,
                    market_value=mv, unrealized_pnl=None, currency=currency,
                    raw_line=ln,
                ))
                i = j + 1
                continue
            # No tail; quarantine
            stmt.quarantine.append((ln, "option holding without strike tail"))
            continue

        # Equity / fund holding row. Symbol may be in parens on this line OR next line.
        m = RE_HOLDING_LINE.match(s)
        if not m:
            continue
        name_part, qty_s, price_s, book_s, mv_s, _unr_s, _pct_s = m.groups()
        sym_match = RE_TRAIL_SYM.search(name_part)
        if sym_match:
            symbol = sym_match.group(1)
            name = name_part[:sym_match.start()].strip()
        else:
            # Symbol on a following line (may span 1-3 lines, e.g. "CHECK POINT\nSOFTWARE TECH\n(CHKP)").
            j = i
            extra = ""
            symbol = None
            consumed = i
            while j < len(lines) and j < i + 4:
                nxt = lines[j].strip()
                if not nxt:
                    j += 1; continue
                # If the next line itself looks like a new holding row, stop.
                if RE_HOLDING_LINE.match(nxt):
                    break
                sm2 = RE_TRAIL_SYM.search(nxt)
                if sm2:
                    symbol = sm2.group(1)
                    extra = (extra + " " + nxt[:sm2.start()]).strip()
                    consumed = j + 1
                    break
                extra = (extra + " " + nxt).strip()
                j += 1
            if not symbol:
                stmt.quarantine.append((ln, "holding without symbol"))
                continue
            name = (name_part + " " + extra).strip()
            i = consumed

        if section == "cash":
            continue  # cash row handled separately below if needed
        atype = section if section in {"equity", "etf", "mutual_fund", "bond"} else "equity"
        instr = ParsedInstrument(
            asset_type=atype, symbol=symbol, currency=currency,
            name=name[:120],
        )
        stmt.positions.append(ParsedPosition(
            instrument=instr, quantity=parse_money(qty_s) or 0.0,
            avg_cost=None, book_value=parse_money(book_s),
            market_price=parse_money(price_s), market_value=parse_money(mv_s),
            unrealized_pnl=None, currency=currency, raw_line=ln,
        ))


def _parse_activity(body: str, currency: str, year_end: int,
                    period_end_month: int, stmt: ParsedStatement) -> None:
    opening = closing = None
    cur: ParsedTxn | None = None
    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            continue
        # noise
        if (s.startswith("Order-Execution-Only") or s.startswith("TD Waterhouse")
            or s.startswith("Page ") or s.startswith("Member ")
            or s.startswith("(continued") or s.startswith("Activity in your account")
            or s.startswith("Date Activity") or s.startswith("Cash")
            or s.startswith("This period") or s.startswith("Earnings/")
            or s.startswith("Account number:") or s.startswith("Account type:")
            or s.startswith("Your investment") or s.startswith("i Important")
            or s.startswith("Details of")):
            continue
        mb = RE_BEGIN_BAL.search(s)
        if mb:
            opening = parse_money(mb.group(1)); continue
        me = RE_END_BAL.search(s)
        if me:
            closing = parse_money(me.group(1)); continue

        m = RE_ACT_DATE.match(s)
        if not m:
            # continuation
            if cur is not None:
                cur.description = (cur.description or "") + " | " + s
            continue
        mon_s, dd_s, rest = m.groups()
        mn = _MON.get(mon_s[:3].upper())
        if not mn:
            continue
        # Year inference: if month > period_end_month, txn is from prior year
        ty = year_end if mn <= period_end_month else year_end - 1
        try:
            trade_date = date(ty, mn, int(dd_s)).isoformat()
        except ValueError:
            continue

        txn_type = _classify(rest)
        if txn_type is None:
            stmt.quarantine.append((ln, f"unknown verb in '{rest[:60]}'"))
            cur = None
            continue

        # Strip the verb phrase from rest to get description
        verb_key = next((k for k in sorted(ACT_VERBS, key=len, reverse=True)
                        if rest.startswith(k)), None)
        desc = rest[len(verb_key):].strip() if verb_key else rest

        # Pull instrument + numbers
        instrument: ParsedInstrument | None = None
        qty = price = amount = cash_after = None
        nums = re.findall(r"-?[\d,]+(?:\.\d+)?", desc)
        # An option token in description?
        m_opt = RE_OPT_TOKEN.search(desc)
        if m_opt:
            cp, root, yy, dd, mon, strike = m_opt.groups()
            expiry = _option_expiry(yy, dd, mon)
            instrument = ParsedInstrument(
                asset_type="option", symbol=root, currency=currency,
                option_root=root, option_expiry=expiry,
                option_strike=float(strike) if strike else None,
                option_type=cp, option_multiplier=100,
            )
            tail = desc[m_opt.end():]
            tnums = re.findall(r"-?[\d,]+(?:\.\d+)?", tail)
            if txn_type in {"buy", "sell"} and len(tnums) >= 4:
                qty = parse_money(tnums[0])
                price = parse_money(tnums[1])
                amount = parse_money(tnums[2])
                cash_after = parse_money(tnums[3])
                # Map to canonical option_*_to_open/close: open if direction
                # adds to a position, close if reduces. With TD we don't know
                # prior position; leave generic and let downstream infer.
                if txn_type == "buy":
                    txn_type = "option_buy_to_open" if (qty or 0) > 0 else "option_buy_to_close"
                else:
                    txn_type = "option_sell_to_open" if (qty or 0) < 0 else "option_sell_to_close"
            elif txn_type in {"option_expiration", "option_exercise", "option_assignment"} and tnums:
                qty = parse_money(tnums[0])
        elif txn_type in {"buy", "sell"} and len(nums) >= 4:
            qty = parse_money(nums[-4])
            price = parse_money(nums[-3])
            amount = parse_money(nums[-2])
            cash_after = parse_money(nums[-1])
            # symbol: look for trailing "(SYM)" in desc
            sm = RE_TRAIL_SYM.search(desc)
            if sm:
                instrument = ParsedInstrument(
                    asset_type="equity", symbol=sm.group(1),
                    currency=currency, name=desc[:sm.start()].strip()[:120],
                )
        elif txn_type in {"dividend", "distribution", "interest_income",
                          "tax_withholding", "return_of_capital"} and nums:
            # Last number is running cash balance; second-last is amount.
            if len(nums) >= 2:
                amount = parse_money(nums[-2])
                cash_after = parse_money(nums[-1])
            else:
                amount = parse_money(nums[-1])
            sm = RE_TRAIL_SYM.search(desc)
            if sm:
                instrument = ParsedInstrument(
                    asset_type="equity", symbol=sm.group(1),
                    currency=currency, name=desc[:sm.start()].strip()[:120],
                )
        elif nums:
            amount = parse_money(nums[-2]) if len(nums) >= 2 else parse_money(nums[-1])
            cash_after = parse_money(nums[-1]) if len(nums) >= 2 else None

        cur = ParsedTxn(
            trade_date=trade_date, settle_date=None, txn_type=txn_type,
            instrument=instrument, quantity=qty, price=price,
            gross_amount=None, commission=None, other_fees=None,
            net_amount=amount, currency=currency,
            description=desc.strip(), raw_line=ln,
        )
        stmt.transactions.append(cur)

    if opening is not None or closing is not None:
        stmt.cash_balances.append(ParsedCashBalance(
            currency=currency, opening_balance=opening,
            closing_balance=closing if closing is not None else 0.0,
        ))


# ---------------------------------------------------------------- Parser
class TDParser:
    NAME = "td"
    VERSION = "1.0.0"

    def can_handle(self, folder_name: str, first_page_text: str) -> bool:
        if folder_name == "TD Webbroker":
            return True
        return ("TD Direct Investing" in first_page_text
                or "TD Waterhouse Canada" in first_page_text)

    def parse(self, pdf: PdfText) -> ParseResult:
        result = ParseResult(parser_name=self.NAME, parser_version=self.VERSION)
        text = pdf.full_text

        if _is_summary(pdf.relpath):
            ym = re.search(r"_(\d{4})_(?:mid_year_)?summary", pdf.relpath)
            if not ym:
                ym = re.search(r"(\d{4})", pdf.relpath)
            an = re.search(r"Statement_([A-Z0-9]+)_", pdf.relpath)
            if ym and an:
                year = int(ym.group(1))
                result.statements.append(ParsedStatement(
                    account=ParsedAccount(account_number=an.group(1),
                                          account_type="Direct Trading",
                                          base_currency="CAD"),
                    period_start=f"{year}-01-01",
                    period_end=f"{year}-12-31",
                    statement_type="annual",
                ))
            return result

        period = _parse_period(text)
        if not period:
            result.errors.append("could not parse period")
            return result
        ps, pe = period
        period_end_month = int(pe[5:7])
        period_end_year = int(pe[:4])

        for sub in _split_subs(text):
            stmt = ParsedStatement(
                account=ParsedAccount(
                    account_number=f"{sub.account_number}-{sub.currency[:3]}",
                    account_type="Direct Trading",
                    base_currency=sub.currency,
                ),
                period_start=ps, period_end=pe, statement_type="monthly",
            )
            # Holdings section starts at "Holdings in your account" and ends
            # at "Activity in your account this period" or "Definitions".
            hm = re.search(r"Holdings in your account", sub.text)
            am = re.search(r"Activity in your account this period", sub.text)
            if hm and am and am.start() > hm.start():
                _parse_holdings(sub.text[hm.end():am.start()], sub.currency, stmt)
                tail = sub.text[am.end():]
                # End markers: "Details of investment income" / "Disclosures"
                end = re.search(r"Details of investment income|^\s*Disclosures",
                                tail, re.MULTILINE)
                act_body = tail[:end.start()] if end else tail
                _parse_activity(act_body, sub.currency, period_end_year,
                                period_end_month, stmt)
            elif hm:
                _parse_holdings(sub.text[hm.end():], sub.currency, stmt)
            result.statements.append(stmt)
        return result


register(TDParser())
