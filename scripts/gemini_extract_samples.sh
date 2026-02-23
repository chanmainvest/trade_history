#!/usr/bin/env bash
set -euo pipefail

STATEMENTS_ROOT="${1:-Statements}"
OUTPUT_ROOT="${2:-data/gemini_overrides}"
MODEL="${GEMINI_MODEL:-gemini-3-pro}"
SAMPLES_PER_INST="${SAMPLES_PER_INST:-2}"
MAX_LINES="${MAX_LINES:-220}"
GEMINI_TIMEOUT_SEC="${GEMINI_TIMEOUT_SEC:-300}"

declare -a INSTITUTIONS=(
  "CIBC Invest Direct"
  "HSBC direct invest"
  "RBC Invest Direct"
  "TD Webbroker"
)

if [[ -n "${INSTITUTIONS_CSV:-}" ]]; then
  IFS=',' read -r -a INSTITUTIONS <<< "${INSTITUTIONS_CSV}"
fi

if ! command -v gemini >/dev/null 2>&1; then
  echo "ERROR: gemini CLI not found in PATH."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required (used to run project Python scripts)."
  exit 1
fi

if [[ -z "${GEMINI_API_KEY:-}" && -z "${GOOGLE_API_KEY:-}" && -z "${GOOGLE_GENAI_USE_VERTEXAI:-}" ]]; then
  echo "WARNING: GEMINI_API_KEY/GOOGLE_API_KEY not set; gemini CLI may prompt for interactive auth."
fi

mkdir -p "${OUTPUT_ROOT}"

run_gemini() {
  local prompt_file="$1"
  local raw_output_file="$2"
  if command -v timeout >/dev/null 2>&1; then
    timeout "${GEMINI_TIMEOUT_SEC}" gemini -m "${MODEL}" -p "$(cat "${prompt_file}")" --output-format json > "${raw_output_file}"
  else
    gemini -m "${MODEL}" -p "$(cat "${prompt_file}")" --output-format json > "${raw_output_file}"
  fi
}

echo "Using model: ${MODEL}"
echo "Statements root: ${STATEMENTS_ROOT}"
echo "Output root: ${OUTPUT_ROOT}"

for institution in "${INSTITUTIONS[@]}"; do
  inst_dir="${STATEMENTS_ROOT}/${institution}"
  if [[ ! -d "${inst_dir}" ]]; then
    echo "Skipping missing folder: ${inst_dir}"
    continue
  fi

  mapfile -t pdfs < <(find "${inst_dir}" -maxdepth 1 -type f -name '*.pdf' | sort | head -n "${SAMPLES_PER_INST}")
  if [[ "${#pdfs[@]}" -eq 0 ]]; then
    echo "No PDFs found in: ${inst_dir}"
    continue
  fi

  out_dir="${OUTPUT_ROOT}/${institution}"
  mkdir -p "${out_dir}"
  echo "Processing ${institution}: ${#pdfs[@]} sample PDFs"

  for pdf in "${pdfs[@]}"; do
    base_name="$(basename "${pdf}" .pdf)"
    lines_file="${out_dir}/${base_name}.lines.json"
    prompt_file="${out_dir}/${base_name}.prompt.txt"
    raw_output_file="${out_dir}/${base_name}.raw.json"
    output_file="${out_dir}/${base_name}.json"

    echo "  - Exporting lines: ${pdf}"
    uv run python scripts/export_statement_lines.py --pdf "${pdf}" --out "${lines_file}" --max-lines "${MAX_LINES}"

    cat > "${prompt_file}" <<'PROMPT'
Extract brokerage transactions from the provided statement lines.

Return STRICT JSON with exactly this top-level shape:
{
  "transactions": [
    {
      "source_line_ref": "p#:l#",
      "event_type": "trade|transfer|dividend|interest|fee",
      "side": "BUY|SELL|BUY_TO_OPEN|SELL_TO_OPEN|BUY_TO_CLOSE|SELL_TO_CLOSE|TRANSFER_IN|TRANSFER_OUT|DIVIDEND|INTEREST|FEE|COMMISSION|null",
      "symbol_norm": "ticker symbol only, not company/institution words",
      "asset_type": "equity|option",
      "option_root": "root ticker for options or null",
      "put_call": "P|C|null",
      "strike": 0.0,
      "expiry": "YYYY-MM-DD or null",
      "quantity": 0.0,
      "price": 0.0,
      "gross_amount": 0.0,
      "currency": "CAD|USD|null",
      "commission": 0.0,
      "fees": 0.0
    }
  ]
}

Rules:
- Use ticker symbols (examples: MSFT, NVDA, TD, TECK.B), never company names (examples: MICROSOFT, CANADIAN, BANK, CIBC).
- Include only rows that are actual transactions/events.
- If unsure, set the field to null instead of guessing.
- For options, normalize put_call to P/C and expiry to ISO date.

Statement lines JSON follows.
PROMPT
    cat "${lines_file}" >> "${prompt_file}"

    echo "    Calling gemini for ${base_name} ..."
    if run_gemini "${prompt_file}" "${raw_output_file}"; then
      uv run python scripts/normalize_gemini_override.py \
        --input "${raw_output_file}" \
        --output "${output_file}" \
        --source-pdf "${pdf}"
      echo "    Wrote overrides: ${output_file}"
    else
      echo "    WARNING: gemini call failed or timed out for ${pdf}" >&2
    fi
  done
done

echo "Done. Override files are under: ${OUTPUT_ROOT}"
