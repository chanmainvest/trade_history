"""Tests for shared extractor utilities."""

from datetime import date
from decimal import Decimal

import pytest

from trade_history.extractors.utils import (
    is_valid_account_id,
    normalize_description,
    parse_amount,
    parse_cibc_option,
    parse_date_flexible,
    parse_hsbc_option,
    parse_quantity,
    parse_rbc_option,
    parse_td_option,
)

# ── parse_amount ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("1,234.56", Decimal("1234.56")),
    ("$1,234.56", Decimal("1234.56")),
    ("(1,234.56)", Decimal("-1234.56")),
    ("-500.00", Decimal("-500.00")),
    ("0", Decimal("0")),
    ("  $0.00 ", Decimal("0")),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


# ── parse_quantity ────────────────────────────────────────────────────────────

def test_parse_quantity_basic():
    assert parse_quantity("100") == Decimal("100")
    assert parse_quantity("1,000") == Decimal("1000")
    assert parse_quantity("") is None


# ── parse_date_flexible ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("2024-01-15", date(2024, 1, 15)),
    ("01/15/2024", date(2024, 1, 15)),
    ("Jan 15, 2024", date(2024, 1, 15)),
    ("15-Jan-2024", date(2024, 1, 15)),
    ("January 15, 2024", date(2024, 1, 15)),
])
def test_parse_date_flexible(raw, expected):
    assert parse_date_flexible(raw) == expected


def test_parse_date_flexible_invalid():
    assert parse_date_flexible("not a date") is None


# ── is_valid_account_id ───────────────────────────────────────────────────────

@pytest.mark.parametrize("token,expected", [
    ("588-93738", True),
    ("6Y-6HF9-E", True),
    ("77FF49", True),
    ("58MRB0", True),
    ("668-44715-2-4", True),
    ("1234", False),       # too short
    ("12345", False),      # short numeric
    ("", False),           # empty
])
def test_is_valid_account_id(token, expected):
    assert is_valid_account_id(token) == expected


# ── Option parsers ────────────────────────────────────────────────────────────

def test_parse_cibc_option_put():
    opt = parse_cibc_option("PUT AG JAN 19 2024 6")
    assert opt is not None
    assert opt.put_call == "put"
    assert opt.root == "AG"
    assert opt.expiry == date(2024, 1, 19)
    assert opt.strike == Decimal("6")


def test_parse_cibc_option_call_with_dot():
    opt = parse_cibc_option("CALL .FNV MAR 15 2024 180")
    assert opt is not None
    assert opt.put_call == "call"
    assert opt.root == "FNV"
    assert opt.expiry == date(2024, 3, 15)
    assert opt.strike == Decimal("180")


def test_parse_cibc_option_none():
    assert parse_cibc_option("SOME REGULAR STOCK") is None


def test_parse_cibc_option_from_raw_text():
    """CIBC extractor strips day/year/strike from description; raw_text retains them."""
    raw = "Apr 24 Sold PUT AG JAN 19 2024 6 10 0.200 1,982.91 -$29,717.91"
    opt = parse_cibc_option(raw)
    assert opt is not None
    assert opt.put_call == "put"
    assert opt.root == "AG"
    assert opt.expiry == date(2024, 1, 19)
    assert opt.strike == Decimal("6")


def test_parse_cibc_option_no_strike():
    """Older CIBC format: strike absent, negative qty follows year; strike defaults to 0."""
    raw = "Nov 23 Sold PUT BHP DEC 17 2021 -20 1.000 $1,968.03"
    opt = parse_cibc_option(raw)
    assert opt is not None
    assert opt.put_call == "put"
    assert opt.root == "BHP"
    assert opt.expiry == date(2021, 12, 17)
    assert opt.strike == Decimal("0")


def test_parse_rbc_option_from_raw_text():
    """RBC raw_text starts with date + action word; option details found via search."""
    raw = "NOV. 12 BOUGHT CALL .BCE 03/21/25 40 30 1.04 3,157.50"
    opt = parse_rbc_option(raw)
    assert opt is not None
    assert opt.put_call == "call"
    assert opt.root == "BCE"
    assert opt.strike == Decimal("40")


def test_parse_hsbc_option():
    opt = parse_hsbc_option("PUT -100 BCE'23 SP@58")
    assert opt is not None
    assert opt.put_call == "put"
    assert opt.root == "BCE"
    assert opt.strike == Decimal("58")
    assert opt.expiry.year == 2023
    assert opt.expiry.month == 9


def test_parse_rbc_option_call():
    opt = parse_rbc_option("CALL SHOP 01/20/23 700")
    assert opt is not None
    assert opt.put_call == "call"
    assert opt.root == "SHOP"
    assert opt.strike == Decimal("700")


def test_parse_rbc_option_put_with_dot():
    opt = parse_rbc_option("PUT .BCE 03/21/25 40")
    assert opt is not None
    assert opt.put_call == "put"
    assert opt.root == "BCE"
    assert opt.strike == Decimal("40")


def test_parse_td_option():
    opt = parse_td_option("CALL-100 CNQ'25 JA@50")
    assert opt is not None
    assert opt.put_call == "call"
    assert opt.root == "CNQ"
    assert opt.strike == Decimal("50")
    assert opt.expiry.year == 2025
    assert opt.expiry.month == 1
    assert opt.multiplier == 100


def test_parse_td_option_expiration_prefix():
    """TD expiry events include 'Expiration' prefix — should still parse."""
    opt = parse_td_option("Expiration PUT -100 NVDA'23 24FB@160")
    assert opt is not None
    assert opt.put_call == "put"
    assert opt.root == "NVDA"
    assert opt.strike == Decimal("160")
    assert opt.expiry == date(2023, 2, 24)


def test_parse_td_option_exercise_option_prefix():
    """TD exercise events include 'Exercise Option' prefix — should still parse."""
    opt = parse_td_option("Exercise Option CALL-100 ABNB'24 SP@125")
    assert opt is not None
    assert opt.put_call == "call"
    assert opt.root == "ABNB"
    assert opt.strike == Decimal("125")


def test_parse_td_option_option_prefix():
    """TD 'Option CALL/PUT' prefix — should still parse."""
    opt = parse_td_option("Option CALL-100 SMCI'24 DC@20")
    assert opt is not None
    assert opt.put_call == "call"
    assert opt.root == "SMCI"
    assert opt.strike == Decimal("20")


def test_parse_td_option_no_space_before_root():
    """TD: multiplier and root run together without space — 'PUT -100INTC'25 17JA@17.5'."""
    opt = parse_td_option("Sell PUT -100INTC'25 17JA@17.5")
    assert opt is not None
    assert opt.put_call == "put"
    assert opt.root == "INTC"
    assert opt.strike == Decimal("17.5")
    assert opt.expiry == date(2025, 1, 17)


def test_parse_td_option_adjusted_symbol():
    """TD: adjusted-option root with special chars 'BABA+$' and no space before month."""
    opt = parse_td_option("Buy PUT -100 BABA+$'25JA@72.5")
    assert opt is not None
    assert opt.put_call == "put"
    assert opt.root == "BABA+$"
    assert opt.strike == Decimal("72.5")


def test_parse_td_option_sold_prefix():
    """HSBC newer format uses past-tense 'Sold'/'Bought' with TD-style compact option."""
    opt = parse_td_option("Sold PUT-100TSLA'23FB@60")
    assert opt is not None
    assert opt.put_call == "put"
    assert opt.root == "TSLA"
    assert opt.strike == Decimal("60")
    assert opt.expiry.year == 2023
    assert opt.expiry.month == 2


def test_parse_td_option_market_suffix():
    """TD: year followed by market suffix '-US' before the month code."""
    opt = parse_td_option("Sell CALL-100 PAAS'27-US JA@80")
    assert opt is not None
    assert opt.put_call == "call"
    assert opt.root == "PAAS"
    assert opt.strike == Decimal("80")
    assert opt.expiry.year == 2027
    assert opt.expiry.month == 1


# ── normalize_description ────────────────────────────────────────────────────


@pytest.mark.parametrize("desc,expected", [
    ("BOUGHT BHP GROUP LIMITED", "BHP GROUP LIMITED"),
    ("SOLD CAMECOCORP", "CAMECOCORP"),
    ("DIVIDEND ROYAL BANK", "ROYAL BANK"),
    ("BUY SPROTTINC 2,000 SII 45.980", "SPROTTINC 2,000 SII"),
    ("BOUGHT PURPOSEETHERETF 14,000 14.31 200,349.95", "PURPOSEETHERETF"),
    ("DIVREIN PANAMERICANSILVERCORP 3 96.73", "PANAMERICANSILVERCORP"),
    ("DEPOSIT TRANSFERFUNDSFROMRBC 500,000.00", "TRANSFERFUNDSFROMRBC"),
    ("", None),
    ("BOUGHT 100 50.00 5000.00", None),
    # Lot code stripping (TD broker appends tracking codes)
    ("Buy PFIZER INC EZ-640338 300 25.00 7500.00", "PFIZER INC"),
    ("Sell AIRBNB INC CL-A BT-725813 200 140.00 28000.00", "AIRBNB INC CL-A"),
    # "UNSOLICITED" qualifier stripping
    ("BOUGHT BARRICK GOLD CORP UNSOLICITED 2000 20.00 40000.00", "BARRICK GOLD CORP"),
    # "AS" preposition stripping
    ("Sell HALLIBURTON CO AS OF JAN 1500 35.00 52500.00", "HALLIBURTON CO"),
])
def test_normalize_description(desc, expected):
    assert normalize_description(desc) == expected
