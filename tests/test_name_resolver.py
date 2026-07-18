from ledger.parsers.name_resolver import resolve_ticker


def test_dual_listed_security_names_resolve_in_native_currency():
    assert resolve_ticker("BARRICK MINING CORP", "CAD") == ("ABX", "equity")
    assert resolve_ticker("BARRICK MINING CORP", "USD") == ("GOLD", "equity")
    assert resolve_ticker("CAMECO CORP", "CAD") == ("CCO", "equity")
    assert resolve_ticker("CAMECO CORP", "USD") == ("CCJ", "equity")
    assert resolve_ticker("NEWMONT CORPORATION", "CAD") == ("NGT", "equity")
    assert resolve_ticker("NEWMONT CORPORATION", "USD") == ("NEM", "equity")
