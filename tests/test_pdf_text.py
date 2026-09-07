"""Self-contained tests for the shared PDF text extraction utilities."""
from ledger.pdf_text import _without_leader_dot_rows


class _FakePage:
    """Minimal page stand-in exposing chars and pdfplumber's filter API."""

    def __init__(self, chars):
        self.chars = chars

    def filter(self, predicate):
        return _FakePage([c for c in self.chars if predicate(c)])


def _char(text, top, x):
    return {"object_type": "char", "text": text, "top": top, "x0": x}


def test_leader_dot_rows_are_dropped_without_touching_decimals():
    # A content row ("1,600 Seg BMO 75.220 ...") plus a leader-dot row of
    # the kind old TD WebBroker statements print between holdings rows.
    content = [
        _char("1", 100.0, 10.0), _char(",", 100.0, 14.0), _char("6", 100.0, 18.0),
        _char("0", 100.0, 22.0), _char("0", 100.0, 26.0), _char(".", 100.0, 30.0),
        _char("2", 100.0, 32.0), _char("2", 100.0, 36.0), _char("S", 100.0, 42.0),
    ]
    leader = [_char(".", 103.0, 10.0 + 3 * k) for k in range(30)]
    filtered = _without_leader_dot_rows(_FakePage(content + leader))
    assert [c["text"] for c in filtered.chars] == list("1,600.22S")


def test_pages_without_leader_rows_are_returned_unchanged():
    chars = [_char("a", 100.0, 1.0), _char(".", 100.0, 3.0), _char("b", 100.0, 5.0)]
    page = _FakePage(chars)
    assert _without_leader_dot_rows(page) is page
