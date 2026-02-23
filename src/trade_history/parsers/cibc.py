from __future__ import annotations

import re
from pathlib import Path

from trade_history.parsers.regex_parser import RegexStatementParser


class CIBCInvestDirectParser(RegexStatementParser):
    institution = "CIBC Invest Direct"

    def _fallback_account_id(self, file_path: Path) -> str:
        name = file_path.stem.upper()
        match = re.search(r"([A-Z0-9]{6,8})$", name)
        if match:
            return match.group(1)
        return f"CIBCID-{super()._fallback_account_id(file_path)}"


class CIBCImperialServiceParser(RegexStatementParser):
    institution = "CIBC Imperial Service"

    def _fallback_account_id(self, file_path: Path) -> str:
        # Try to find an account-like token in the filename (e.g. 586-33338).
        name = file_path.stem.upper()
        acct = re.search(r"(\d{3}[-]?\d{5})", name)
        if acct:
            return acct.group(1)
        # Use a stable fallback instead of YYYYMM which creates phantom accounts.
        return f"CIBCIS-{super()._fallback_account_id(file_path)}"


class CIBCTSFAParser(RegexStatementParser):
    institution = "CIBC TSFA"

    def _fallback_account_id(self, file_path: Path) -> str:
        return f"CIBCTF-{super()._fallback_account_id(file_path)}"

