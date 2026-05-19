#!/usr/bin/env bash
# gemini_extract_samples.sh
# Uses Google Gemini CLI to extract and correct ambiguous transaction rows.
# Outputs override JSON files to data/gemini_overrides/<Institution>/<StatementStem>.json
#
# Prerequisites:
#   - gemini CLI authenticated (gcloud auth application-default login)
#   - pdftotext (poppler-utils) installed
#   - jq installed

set -euo pipefail

STATEMENTS_DIR="${STATEMENTS_DIR:-G:/My Drive/Investment}"
OVERRIDES_DIR="data/gemini_overrides"
MODEL="gemini-1.5-pro"

usage() {
  echo "Usage: $0 [--institution INST] [--statement FILE.pdf]"
  echo ""
  echo "  --institution   Only process PDFs from this institution folder"
  echo "  --statement     Process a single PDF file"
  echo "  --dry-run       Print what would be processed without calling Gemini"
  exit 1
}

INSTITUTION=""
SINGLE_FILE=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --institution) INSTITUTION="$2"; shift 2 ;;
    --statement)   SINGLE_FILE="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage ;;
    *) echo "Unknown arg: $1"; usage ;;
  esac
done

extract_and_prompt() {
  local pdf="$1"
  local institution="$2"
  local stem
  stem=$(basename "$pdf" .pdf)
  local out_dir="$OVERRIDES_DIR/$institution"
  local out_file="$out_dir/${stem}.json"

  mkdir -p "$out_dir"

  if [[ -f "$out_file" ]]; then
    echo "  [SKIP] Already has override: $out_file"
    return 0
  fi

  echo "  Processing: $pdf"

  # Extract text
  local text
  text=$(pdftotext "$pdf" - 2>/dev/null | head -n 200)

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [DRY RUN] Would call Gemini for: $pdf"
    return 0
  fi

  # Build prompt
  local prompt
  prompt=$(cat <<PROMPT
You are a financial data extraction assistant. Below is the raw text extracted from a brokerage statement PDF.

Your task:
1. Identify any transaction rows that appear malformed, truncated, or ambiguous.
2. For each such row, return a corrected version as a JSON object.
3. Return a JSON array of correction objects with this schema:
   {
     "original_raw_text": "...",
     "corrected": {
       "date": "YYYY-MM-DD",
       "activity": "bought|sold|dividend|interest|fee|...",
       "symbol": "TICKER or null",
       "quantity": number or null,
       "price": number or null,
       "amount": number,
       "currency": "CAD|USD",
       "notes": "why this was corrected"
     }
   }

If all rows look clean, return an empty array: []

Statement text:
---
$text
---

Return ONLY valid JSON. No markdown, no explanation outside the JSON.
PROMPT
)

  # Call Gemini (requires gemini CLI or curl to Vertex AI)
  # Using gemini CLI if available, else curl
  if command -v gemini &>/dev/null; then
    echo "$prompt" | gemini --model="$MODEL" --format=json > "$out_file" 2>/dev/null || echo "[]" > "$out_file"
  else
    # Fallback: write empty array (manual review required)
    echo "  [WARN] gemini CLI not found; writing empty override"
    echo "[]" > "$out_file"
  fi

  echo "  Written: $out_file"
}

# Main loop
if [[ -n "$SINGLE_FILE" ]]; then
  institution=$(basename "$(dirname "$SINGLE_FILE")")
  extract_and_prompt "$SINGLE_FILE" "$institution"
else
  find "$STATEMENTS_DIR" -name "*.pdf" | while read -r pdf; do
    institution=$(basename "$(dirname "$pdf")")
    if [[ -n "$INSTITUTION" && "$institution" != "$INSTITUTION" ]]; then
      continue
    fi
    extract_and_prompt "$pdf" "$institution"
  done
fi

echo ""
echo "Done. Override files written to: $OVERRIDES_DIR"
