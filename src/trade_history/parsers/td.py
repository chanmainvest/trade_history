from __future__ import annotations

import re
from pathlib import Path

from trade_history.parsers.regex_parser import RegexStatementParser


class TDWebBrokerParser(RegexStatementParser):
    institution = "TD Webbroker"

    def _fallback_account_id(self, file_path: Path) -> str:
        name = file_path.stem.upper()
        match = re.search(r"STATEMENT_([A-Z0-9]{6,8})", name)
        if match:
            return match.group(1)
        return f"TD-{super()._fallback_account_id(file_path)}"

