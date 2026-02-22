from __future__ import annotations

from pathlib import Path

from trade_history.parsers.base import StatementParser
from trade_history.parsers.cibc import (
    CIBCImperialServiceParser,
    CIBCInvestDirectParser,
    CIBCTSFAParser,
)
from trade_history.parsers.hsbc import HSBCDirectInvestParser
from trade_history.parsers.rbc import RBCInvestDirectParser
from trade_history.parsers.td import TDWebBrokerParser


PARSER_BY_FOLDER: dict[str, StatementParser] = {
    "cibc invest direct": CIBCInvestDirectParser(),
    "cibc imperial service": CIBCImperialServiceParser(),
    "cibc tsfa": CIBCTSFAParser(),
    "hsbc direct invest": HSBCDirectInvestParser(),
    "rbc invest direct": RBCInvestDirectParser(),
    "td webbroker": TDWebBrokerParser(),
}


def parser_for_path(file_path: Path) -> StatementParser | None:
    folder = file_path.parent.name.strip().lower()
    return PARSER_BY_FOLDER.get(folder)

