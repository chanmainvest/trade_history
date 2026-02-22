from __future__ import annotations

import re
from pathlib import Path

from trade_history.parsers.regex_parser import RegexStatementParser


class RBCInvestDirectParser(RegexStatementParser):
    institution = "RBC Invest Direct"

    def _fallback_account_id(self, file_path: Path) -> str:
        name = file_path.stem.upper()
        acct = re.search(r"(\d{8})", name)
        if acct:
            return acct.group(1)
        suffix = re.search(r"STATEMENT-(\d{4})", name)
        if suffix:
            return f"RBC-{suffix.group(1)}"
        return f"RBC-{super()._fallback_account_id(file_path)}"

