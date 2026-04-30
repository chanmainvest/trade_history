"""HSBC InvestDirect parser.

Layout (PDF text from pdfplumber tends to drop spaces):

    Statementperiod September1,2023toSeptember30,2023
    Your portfolio overview
        <table of accounts: 6Y-6HF9-E Canadian Margin Account, 6Y-6HF9-F USD Margin Account, ...>
    [per account]
    Your <Type> Account
    Account#<NUM>
    Details of holdings in your account
        <holdings table by sub-section: Cash / Equities / Options/Rights/Warrants / Mutual Funds>
    Account activity since your last statement
        OpeningBalance $X
        <DateMon DD> Activity Description Quantity Price Amount
        ...
        ClosingBalance $X
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..pdf_text import PdfText
from .helpers import _option_mon, _third_friday, parse_money
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

_MONTH3 = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

RE_PERIOD = re.compile(
    r"Statement\s*period\s*([A-Za-z]+)\s*(\d{1,2})\s*,\s*(\d{4})\s*to\s*([A-Za-z]+)\s*(\d{1,2})\s*,\s*(\d{4})",
    re.IGNORECASE,
)
RE_ACCOUNT_HDR = re.compile(
    r"Your\s+([A-Za-z $]+?)\s*Account[^\n]*?\n[^\n]*?Account#?\s*([0-9A-Z]{2}-?[0-9A-Z]{4}-?[0-9A-Z])",
    re.IGNORECASE,
)
# Activity row: e.g. "Sep5 Dividend SPROTTINC-NEW 1,300 438.62"
RE_ACT_ROW = re.compile(
    r"^([A-Z][a-z]{2})(\d{1,2})\s+([A-Za-z\-./]+?)\s+(.*)$"
)
RE_OPENING = re.compile(r"^OpeningBalance\s+\$?\s*\(?(-?[\d,]+(?:\.\d+)?)\)?", re.IGNORECASE)
RE_CLOSING = re.compile(
    r"^(?:[A-Z][a-z]{2}\d{1,2}\s+)?ClosingBalance(?:afterSettlement)?\s+\$?\s*\(?(-?[\d,]+(?:\.\d+)?)\)?",
    re.IGNORECASE,
)

# Compact HSBC option token (no spaces): (CALL|PUT)-<mult><ROOT>'<YY>[<DD>]<MM>@<strike>
RE_OPT = re.compile(
    r"\b(CALL|PUT)-(\d+)([A-Z][A-Z0-9.\-]{0,5}?)'(\d{2})(\d{0,2})([A-Z]{2})@(\d+(?:\.\d+)?)"
)
RE_PARENS_TICKER = re.compile(r"\(([A-Z0-9.\-]{1,8})\)")


@dataclass
class _AcctSection:
    account_number: str
    account_type: str
    base_currency: str
    text: str  # body text for this account


def _is_fee_summary(relpath: str) -> bool:
    return "_fees.pdf" in relpath.lower() or "fees.pdf" in relpath.lower() or "_fees.txt" in relpath.lower()


def _normalize(text: str) -> str:
    """pdfplumber drops spaces; we still leave tokens intact for regex.

    But we DO need spaces between activity-row date and rest. Insert a space
    after every 'MmmDD' prefix at start of line so the activity regex works.
    """
    out = []
    for ln in text.splitlines():
        m = re.match(r"^([A-Z][a-z]{2})(\d{1,2})(?=\S)", ln)
        if m:
            ln = ln[:m.end()] + " " + ln[m.end():]
        out.append(ln)
    return "\n".join(out)


def _opt_expiry(yy: str, dd: str | None, mm: str) -> str | None:
    mn = _option_mon(mm)
    if mn is None:
        return None
    year = 2000 + int(yy)
    if dd:
        try:
            return date(year, mn, int(dd)).isoformat()
        except ValueError:
            return None
    return _third_friday(year, mn).isoformat()


def _option_from_match(m: re.Match, currency: str) -> ParsedInstrument | None:
    cp, mult, root, yy, dd, mm, strike = m.groups()
    expiry = _opt_expiry(yy, dd if dd else None, mm)
    if not expiry:
        return None
    return ParsedInstrument(
        asset_type="option", symbol=root, currency=currency,
        option_root=root, option_expiry=expiry, option_strike=float(strike),
        option_type=cp, option_multiplier=int(mult),
    )


def _classify_activity(verb: str) -> str | None:
    v = verb.lower().replace(".", "").replace("-", "").replace("_", "")
    table = {
        "bought": "buy", "sold": "sell",
        "dividend": "dividend",
        "incomedist": "distribution",
        "interest": "interest_income",
        "nonrestax": "tax_withholding", "tax": "tax_withholding",
        "expire": "option_expiration",
        "assign": "option_assignment", "assigned": "option_assignment",
        "exercise": "option_exercise",
        "internaltfr": "journal",
        "eps": "transfer_out", "eft": "deposit",
        "fee": "fee",
        "deposit": "deposit",
        "withdrawal": "withdrawal", "withdraw": "withdrawal",
        "journal": "journal",
        "split": "stock_split",
    }
    return table.get(v)


def _parse_period(text: str) -> tuple[str, str] | None:
    m = RE_PERIOD.search(text.replace("\n", " "))
    if not m:
        return None
    m1, d1, y1, m2, d2, y2 = m.groups()
    try:
        from dateutil import parser as dp
        return (dp.parse(f"{m1} {d1} {y1}").date().isoformat(),
                dp.parse(f"{m2} {d2} {y2}").date().isoformat())
    except Exception:
        return None


# Account currency inference: suffix '-E' = CAD, '-F' = USD (per HSBC's listing)
_SUFFIX_CCY = {"E": "CAD", "F": "USD"}


def _split_account_sections(text: str) -> list[_AcctSection]:
    """Split by 'Your X Account ... Account#NNN' headers (per-account blocks)."""
    out: list[_AcctSection] = []
    # Find boundaries
    pat = re.compile(
        r"Your\s+([A-Za-z $]+?Account)[^\n]*\n[^\n]*Account#?\s*([0-9A-Z]{2}-?[0-9A-Z]{4}-?[0-9A-Z])",
        re.IGNORECASE,
    )
    matches = list(pat.finditer(text))
    if not matches:
        return out
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        type_label = m.group(1).strip()
        if type_label.lower().endswith("(continued)"):
            continue
        acct = m.group(2).upper()
        suffix = acct[-1]
        if "USD" in type_label.upper():
            ccy = "USD"
        elif "CAD" in type_label.upper() or "CANADIAN" in type_label.upper():
            ccy = "CAD"
        else:
            ccy = _SUFFIX_CCY.get(suffix, "CAD")
        out.append(_AcctSection(
            account_number=acct, account_type=type_label,
            base_currency=ccy, text=text[start:end],
        ))
    # Merge "(continued)" sections back into their preceding section.
    merged: list[_AcctSection] = []
    for sec in out:
        if merged and merged[-1].account_number == sec.account_number:
            merged[-1].text += "\n" + sec.text
        else:
            merged.append(sec)
    return merged


def _parse_holdings(text: str, currency: str, stmt: ParsedStatement) -> None:
    # Look for "Details of holdings in your account" then table rows until
    # "Account activity" or end of section.
    m_start = re.search(r"Details of holdings in your account", text, re.IGNORECASE)
    if not m_start:
        return
    rest = text[m_start.end():]
    m_end = re.search(r"Account activity since your last statement", rest, re.IGNORECASE)
    body = rest[:m_end.start()] if m_end else rest

    section = "Equities"
    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            continue
        # Section labels HSBC uses
        lower = s.lower()
        if lower.startswith("equities"):
            section = "Equities"; continue
        if "options/rights/warrants" in lower:
            section = "Options"; continue
        if lower.startswith("mutual funds"):
            section = "MutualFunds"; continue
        if lower.startswith("cash"):
            section = "Cash"; continue
        if lower.startswith("total"):
            continue

        if section == "Cash":
            continue

        # Option holdings: "PUT-100QQQ'24MR@250  10  1.250  10,059.38  1,250.00  0.1"
        opt_m = RE_OPT.search(s)
        if section == "Options" and opt_m:
            after = s[opt_m.end():].strip()
            nums = re.findall(r"-?\(?[\d,]+(?:\.\d+)?\)?-?", after)
            if len(nums) < 4:
                stmt.quarantine.append((ln, "option holding: not enough numbers"))
                continue
            qty = parse_money(nums[0])
            mp = parse_money(nums[1])
            book = parse_money(nums[2])
            mv = parse_money(nums[3])
            instr = _option_from_match(opt_m, currency)
            if instr is None:
                stmt.quarantine.append((ln, "option holding: bad expiry"))
                continue
            stmt.positions.append(ParsedPosition(
                instrument=instr, quantity=qty or 0.0,
                avg_cost=None, book_value=book,
                market_price=mp, market_value=mv,
                unrealized_pnl=None, currency=currency, raw_line=ln,
            ))
            continue

        # Equity / MF holding line, ends with: <qty> <S?> <price> <bookcost> <mktvalue> <%>
        # pdfplumber output is space-collapsed; numbers separated by spaces.
        nums = re.findall(r"-?\(?[\d,]+(?:\.\d+)?\)?-?", s)
        if len(nums) < 4:
            continue
        # Symbol: parens at end of description on same/next line
        tk = RE_PARENS_TICKER.search(s)
        sym = tk.group(1) if tk else None
        # Strip numbers + symbol parens to get description
        desc = re.sub(RE_PARENS_TICKER, "", s)
        desc = re.sub(r"-?\(?[\d,]+(?:\.\d+)?\)?-?\s*$", "", desc).strip()
        if not sym:
            # try previous-line technique skipped; quarantine
            stmt.quarantine.append((ln, "holding: no ticker found"))
            continue
        # Heuristic asset type
        atype = "etf" if "ETF" in desc.upper() else (
            "mutual_fund" if section == "MutualFunds" else "equity"
        )
        qty = parse_money(nums[-5]) if len(nums) >= 5 else parse_money(nums[0])
        mp = parse_money(nums[-4])
        book = parse_money(nums[-3])
        mv = parse_money(nums[-2])
        instr = ParsedInstrument(
            asset_type=atype, symbol=sym, currency=currency, name=desc[:120],
        )
        stmt.positions.append(ParsedPosition(
            instrument=instr, quantity=qty or 0.0,
            avg_cost=None, book_value=book,
            market_price=mp, market_value=mv,
            unrealized_pnl=None, currency=currency, raw_line=ln,
        ))


def _parse_activity(text: str, currency: str, year_default: int,
                    stmt: ParsedStatement) -> None:
    m_start = re.search(r"Account activity since your last statement", text, re.IGNORECASE)
    if not m_start:
        return
    body = text[m_start.end():]
    # Stop at "Pending transactions" or "Important Information" or page footer.
    cutoff = re.search(r"\bPending transactions\b|\bImportant Information\b", body, re.IGNORECASE)
    if cutoff:
        body = body[:cutoff.start()]

    opening = closing = None
    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            continue
        mo = RE_OPENING.match(s)
        if mo:
            opening = parse_money(mo.group(1)); continue
        mc = RE_CLOSING.match(s)
        if mc:
            closing = parse_money(mc.group(1)); continue

        m = RE_ACT_ROW.match(s)
        if not m:
            continue
        mon, dd, verb, rest = m.groups()
        mn = _MONTH3.get(mon)
        if not mn:
            continue
        try:
            trade_date = date(year_default, mn, int(dd)).isoformat()
        except ValueError:
            continue
        txn_type = _classify_activity(verb)
        if txn_type is None:
            stmt.quarantine.append((ln, f"unknown verb: {verb}"))
            continue

        # Try option in description
        opt_m = RE_OPT.search(rest)
        instrument = None
        if opt_m:
            instrument = _option_from_match(opt_m, currency)

        # Trailing numbers: depends on activity row shape. Common shapes:
        #   <qty> <price> <amount>          (Bought/Sold)
        #   <qty>                           (Expire/Assign)
        #   <qty> <amount>                  (Dividend/Tax/Interest)
        nums = re.findall(r"\(?-?[\d,]+(?:\.\d+)?\)?-?", rest)
        # Filter out strike-like numbers when option is present (last token of opt regex
        # is the strike — already consumed in regex match).
        qty = price = amount = None
        if txn_type in {"buy", "sell", "option_buy_to_open", "option_sell_to_open",
                        "option_buy_to_close", "option_sell_to_close"}:
            # Take last 3 numbers as qty/price/amount.
            if opt_m:
                tail = rest[opt_m.end():].strip()
                tail_nums = re.findall(r"\(?-?[\d,]+(?:\.\d+)?\)?-?", tail)
                if len(tail_nums) >= 1:
                    qty = parse_money(tail_nums[0])
                # No explicit price/amount in HSBC option open/close rows
            else:
                # Equity buy/sell: pull last 3 numbers.
                tail_nums = nums
                if len(tail_nums) >= 3:
                    qty = parse_money(tail_nums[-3])
                    price = parse_money(tail_nums[-2])
                    amount = parse_money(tail_nums[-1])
        elif txn_type in {"dividend", "distribution", "interest_income",
                          "tax_withholding", "fee", "interest_expense"}:
            if len(nums) >= 2:
                qty = parse_money(nums[-2]) if txn_type in {"dividend", "distribution",
                                                            "tax_withholding"} else None
                amount = parse_money(nums[-1])
            elif nums:
                amount = parse_money(nums[-1])
        elif txn_type in {"option_expiration", "option_assignment", "option_exercise"}:
            if opt_m:
                tail = rest[opt_m.end():].strip()
                tail_nums = re.findall(r"\(?-?[\d,]+(?:\.\d+)?\)?-?", tail)
                if tail_nums:
                    qty = parse_money(tail_nums[0])
        else:  # journal, transfer_out, deposit, withdrawal etc.
            if nums:
                amount = parse_money(nums[-1])

        # Refine option open/close direction
        if opt_m and txn_type in {"buy", "sell"}:
            if qty is not None:
                if txn_type == "buy":
                    txn_type = "option_buy_to_open" if qty > 0 else "option_buy_to_close"
                else:
                    txn_type = "option_sell_to_open" if qty < 0 else "option_sell_to_close"

        # Build instrument for non-option transactions: try to pull symbol from rest
        if instrument is None and txn_type not in {"interest_income", "fee", "journal",
                                                    "deposit", "withdrawal", "transfer_in",
                                                    "transfer_out"}:
            # Description string is everything before the first number block
            desc_only = re.split(r"\s+\(?-?[\d,]+(?:\.\d+)?", rest, maxsplit=1)[0].strip()
            if desc_only:
                instrument = ParsedInstrument(
                    asset_type="equity", symbol=desc_only.split()[0][:12],
                    currency=currency, name=desc_only,
                )

        stmt.transactions.append(ParsedTxn(
            trade_date=trade_date, settle_date=None, txn_type=txn_type,
            instrument=instrument, quantity=qty, price=price,
            gross_amount=None, commission=None, other_fees=None,
            net_amount=amount, currency=currency,
            description=rest, raw_line=ln,
        ))

    if opening is not None or closing is not None:
        stmt.cash_balances.append(ParsedCashBalance(
            currency=currency, opening_balance=opening,
            closing_balance=closing or 0.0,
        ))


# ----------------------------------------------------------------- Parser
class HSBCParser:
    NAME = "hsbc"
    VERSION = "1.0.0"

    def can_handle(self, folder_name: str, first_page_text: str) -> bool:
        if folder_name == "HSBC direct invest":
            return True
        return "HSBC InvestDirect" in first_page_text or "HSBCSecurities" in first_page_text.replace(" ", "")

    def parse(self, pdf: PdfText) -> ParseResult:
        result = ParseResult(parser_name=self.NAME, parser_version=self.VERSION)
        text = _normalize(pdf.full_text)

        # Annual fee summary PDFs: emit empty annual statement so they're recorded.
        if _is_fee_summary(pdf.relpath):
            # Extract account from filename like hsbc_6y6hf9_2023_fees.pdf
            m = re.search(r"hsbc_([0-9a-z]+)_(\d{4})_fees", pdf.relpath, re.IGNORECASE)
            if m:
                acct_raw = m.group(1).upper()
                # 6y6hf9 -> 6Y-6HF9
                if len(acct_raw) == 6:
                    acct = f"{acct_raw[:2]}-{acct_raw[2:]}"
                else:
                    acct = acct_raw
                year = int(m.group(2))
                result.statements.append(ParsedStatement(
                    account=ParsedAccount(account_number=acct, account_type="Margin",
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
        period_start, period_end = period
        year = int(period_end[:4])

        sections = _split_account_sections(text)
        if not sections:
            result.errors.append("could not split account sections")
            return result

        for sec in sections:
            stmt = ParsedStatement(
                account=ParsedAccount(account_number=sec.account_number,
                                      account_type=sec.account_type,
                                      base_currency=sec.base_currency),
                period_start=period_start, period_end=period_end,
                statement_type="monthly",
            )
            try:
                _parse_holdings(sec.text, sec.base_currency, stmt)
                _parse_activity(sec.text, sec.base_currency, year, stmt)
            except Exception as e:
                stmt.quarantine.append(("<section>", f"section error: {e}"))
            result.statements.append(stmt)

        return result


register(HSBCParser())
