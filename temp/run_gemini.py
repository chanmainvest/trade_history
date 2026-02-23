"""Feed statement line extracts to Gemini-2.5-flash for format analysis."""
from __future__ import annotations
import subprocess, json, pathlib

INSTITUTIONS = [
    "CIBC_InvestDirect",
    "CIBC_Imperial",
    "CIBC_TSFA",
    "HSBC",
    "RBC",
    "TD",
]

ANALYSIS_PROMPT = """You are a financial statement parsing expert.  
I'm giving you the raw text lines extracted (via pdfplumber) from a brokerage PDF statement.

For each transaction / activity line you find, output a structured JSON array where each element has:
- "raw_line": the original text
- "date": extracted date (YYYY-MM-DD)
- "settlement_date": if present (YYYY-MM-DD)  
- "action": buy/sell/dividend/interest/fee/deposit/withdrawal/transfer/option_expiry/option_assignment/other
- "symbol": ticker symbol if present
- "description": full description text
- "quantity": number of shares/contracts (signed: negative for sells)
- "price": per-unit price
- "amount": total dollar amount (signed)
- "currency": CAD/USD
- "account_id": account number if visible on the line or in the page header
- "is_option": true/false
- "option_details": {root, expiry, strike, put_call} if option

Also note:
1. What date format does this institution use? (DD/MM/YYYY, MM/DD/YYYY, Mon DD YYYY, etc.)
2. Are there multi-line transactions that span 2+ lines?
3. What column separators are used (fixed-width, tabs, etc.)?
4. What section headers appear before transaction blocks?
5. How are account numbers formatted?
6. Any special formatting quirks?

Return your analysis as a markdown document with:
- A "Date Format" section
- A "Transaction Format" section with regex suggestions
- A "Sample Extractions" section with the JSON array
- A "Parsing Notes" section with quirks and edge cases"""

temp = pathlib.Path("temp")
node = r"C:\Program Files\nodejs\node.exe"
gemini_js = r"C:\Users\hevan\AppData\Roaming\npm\node_modules\@google\gemini-cli\dist\index.js"

for inst in INSTITUTIONS:
    json_file = temp / f"{inst}_lines.json"
    output = temp / f"{inst}_analysis.md"
    if not json_file.exists():
        print(f"SKIP {inst}: no lines JSON")
        continue

    # Read the extracted lines
    raw_data = json.loads(json_file.read_text(encoding="utf-8"))
    # JSON format: {"file_path": ..., "line_count": ..., "lines": [{"source_line_ref": ..., "text": ...}, ...]}
    lines_list = raw_data.get("lines", raw_data) if isinstance(raw_data, dict) else raw_data
    # Extract text from each entry
    text_lines = []
    for entry in lines_list:
        if isinstance(entry, dict):
            ref = entry.get("source_line_ref", "")
            text = entry.get("text", "")
            text_lines.append(f"[{ref}] {text}")
        else:
            text_lines.append(str(entry))
    
    # Take first 300 lines to keep prompt manageable
    sample_lines = text_lines[:300] if len(text_lines) > 300 else text_lines
    
    stdin_text = "\n".join(sample_lines)
    
    print(f"Running Gemini-2.5-flash for {inst} ({len(stdin_text)} chars, {len(sample_lines)} lines)...")
    try:
        result = subprocess.run(
            [
                node, gemini_js,
                "-m", "gemini-2.5-flash",
                "-p", ANALYSIS_PROMPT,
            ],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="replace",
        )
        # Gemini CLI outputs actual response to stdout; stderr has warnings
        content = result.stdout.strip()
        if content:
            # Strip WARN lines from beginning
            clean_lines = []
            past_warnings = False
            for line in content.split("\n"):
                if not past_warnings and (line.startswith("[WARN]") or line.startswith("Loaded cached") or line.startswith("Error getting")):
                    continue
                past_warnings = True
                clean_lines.append(line)
            clean_content = "\n".join(clean_lines).strip()
            if clean_content:
                output.write_text(clean_content, encoding="utf-8")
                print(f"  OK: {output} ({len(clean_content)} chars)")
            else:
                print(f"  EMPTY after stripping warnings. returncode={result.returncode}")
                # Save raw for debugging
                (temp / f"{inst}_raw_output.txt").write_text(content, encoding="utf-8")
        else:
            stderr_snippet = result.stderr[:500] if result.stderr else "(empty)"
            print(f"  EMPTY stdout. returncode={result.returncode}")
            print(f"  stderr: {stderr_snippet}")
            # Save stderr for debugging
            if result.stderr:
                (temp / f"{inst}_stderr.txt").write_text(result.stderr, encoding="utf-8")
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT for {inst}")
    except Exception as e:
        print(f"  ERROR: {e}")

print("All done.")
