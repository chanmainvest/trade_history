from __future__ import annotations

import re
from pathlib import Path

from trade_history.parsers.regex_parser import RegexStatementParser


class HSBCDirectInvestParser(RegexStatementParser):
    institution = "HSBC direct invest"

    def _fallback_account_id(self, file_path: Path) -> str:
        name = file_path.stem.upper()
        match = re.search(r"HSBC_([A-Z0-9]{6,8})", name)
        if match:
            return match.group(1)
        year_month = re.search(r"(\d{4})-(\d{2})", name)
        if year_month:
            return f"HSBC-{year_month.group(1)}{year_month.group(2)}"
        return f"HSBC-{super()._fallback_account_id(file_path)}"

