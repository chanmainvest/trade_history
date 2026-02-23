from __future__ import annotations

import argparse
import json
from pathlib import Path

from trade_history.parsers.common import extract_text_lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Export statement PDF text lines with stable line references.")
    parser.add_argument("--pdf", required=True, type=Path, help="Path to statement PDF")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON path")
    parser.add_argument(
        "--max-lines",
        type=int,
        default=1500,
        help="Maximum number of lines to emit (default: 1500)",
    )
    args = parser.parse_args()

    lines = extract_text_lines(args.pdf)
    payload = {
        "file_path": str(args.pdf),
        "line_count": len(lines),
        "lines": [
            {
                "source_line_ref": f"p{line.page_number}:l{line.line_number}",
                "text": line.text,
            }
            for line in lines[: args.max_lines]
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
