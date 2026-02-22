from __future__ import annotations

from datetime import date
import re
from pathlib import Path

from trade_history.parsers.base import ParseIssue, ParsedAccount, ParsedStatement
from trade_history.parsers.common import (
    extract_account_id_from_text,
    extract_statement_snapshots,
    extract_text_lines,
    parse_account_ids,
    parse_trade_like_line,
)


class RegexStatementParser:
    institution: str = "UNKNOWN"

    def detect_format(self, file_path: Path) -> str:
        name = file_path.name.lower()
        if "summary" in name or "annual" in name or "tax-document" in name:
            return "summary_document"
        if re.search(r"\d{4}[_-]\d{2}", name):
            year = int(re.search(r"\d{4}", name).group(0))  # type: ignore[union-attr]
            if year < 2020:
                return "legacy_pre_2020"
            if year < 2024:
                return "transition_2020_2023"
            return "modern_2024_plus"
        return "unknown"

    def parse(self, file_path: Path) -> ParsedStatement:
        lines = extract_text_lines(file_path)
        format_version = self.detect_format(file_path)
        fallback_account = self._fallback_account_id(file_path)
        account_ids = parse_account_ids(lines, fallback=fallback_account)
        accounts = [
            ParsedAccount(
                account_id=a,
                institution=self.institution,
                account_name=f"{self.institution} {a[-4:]}",
                masked_number=a[-4:],
            )
            for a in account_ids
        ]
        primary_account = account_ids[0]

        events = []
        issues: list[ParseIssue] = []
        earliest: date | None = None
        latest: date | None = None
        inferred_year = self._infer_statement_year(file_path)
        account_set = {a.account_id for a in accounts}
        current_account = primary_account

        for line in lines:
            found_account = extract_account_id_from_text(line.text)
            if found_account:
                current_account = found_account
                if found_account not in account_set:
                    accounts.append(
                        ParsedAccount(
                            account_id=found_account,
                            institution=self.institution,
                            account_name=f"{self.institution} {found_account[-4:]}",
                            masked_number=found_account[-4:],
                        )
                    )
                    account_set.add(found_account)

            parsed_event, issue = parse_trade_like_line(
                current_account,
                line,
                self.institution,
                default_year=inferred_year,
            )
            if issue:
                issues.append(issue)
                continue
            if parsed_event is None:
                continue
            events.append(parsed_event)
            if earliest is None or parsed_event.trade_date < earliest:
                earliest = parsed_event.trade_date
            if latest is None or parsed_event.trade_date > latest:
                latest = parsed_event.trade_date

        message = None
        if not events:
            message = "No trade-like rows parsed; file may be non-activity statement or unsupported format."
        snapshots = extract_statement_snapshots(
            lines,
            default_account_id=primary_account,
            default_year=inferred_year,
        )

        return ParsedStatement(
            institution=self.institution,
            file_path=file_path,
            format_version=format_version,
            accounts=accounts,
            events=events,
            snapshots=snapshots,
            issues=issues,
            parse_message=message,
            period_start=earliest,
            period_end=latest,
        )

    def _fallback_account_id(self, file_path: Path) -> str:
        stem = file_path.stem.upper()
        alnum = re.sub(r"[^A-Z0-9]", "", stem)
        if len(alnum) >= 6:
            return alnum[-6:]
        return f"{self.institution[:4].upper()}0001"

    def _infer_statement_year(self, file_path: Path) -> int | None:
        name = file_path.name
        match = re.search(r"(20\d{2})", name)
        if match:
            return int(match.group(1))
        return None
