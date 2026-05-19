#!/usr/bin/env python3
"""Build doc/index.html from all Markdown files in spec/.

Usage:
    uv run python scripts/build_docs.py [--version TAG]

Output: doc/index.html  (standalone — no external assets except Mermaid CDN)

Dependencies (already in project dev deps):
    markdown          — CommonMark + tables + fenced-code
    pymdown-extensions — superfences for ```mermaid blocks

If those packages are absent the script falls back to a plain-text render
(good enough for CI diff checks but without proper HTML formatting).
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT / "spec"
DOC_DIR = ROOT / "doc"

# ---------------------------------------------------------------------------
# Ordered list of spec files → rendered in this sequence in the HTML
# ---------------------------------------------------------------------------
SPEC_ORDER = [
    "ARCHITECTURE.md",
    "USER-GUIDE.md",
    "EXTRACTION-CORNER-CASES.md",
]

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Ledger — Documentation{version_suffix}</title>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
  <script>mermaid.initialize({{ startOnLoad: true, theme: 'neutral' }});</script>
  <style>
    :root {{
      --bg: #ffffff; --fg: #1a1a1a; --accent: #0969da;
      --code-bg: #f6f8fa; --border: #d0d7de; --nav-bg: #f6f8fa;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0d1117; --fg: #e6edf3; --accent: #58a6ff;
        --code-bg: #161b22; --border: #30363d; --nav-bg: #161b22;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 16px; line-height: 1.6;
      background: var(--bg); color: var(--fg);
      margin: 0; padding: 0;
    }}
    nav {{
      position: sticky; top: 0; z-index: 100;
      background: var(--nav-bg); border-bottom: 1px solid var(--border);
      padding: 0.5rem 2rem; display: flex; gap: 1.5rem; align-items: center;
      font-size: 0.9rem;
    }}
    nav a {{ color: var(--accent); text-decoration: none; }}
    nav a:hover {{ text-decoration: underline; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 2rem; }}
    h1, h2, h3, h4 {{ color: var(--fg); margin-top: 2rem; }}
    h1 {{ font-size: 2rem; border-bottom: 2px solid var(--border); padding-bottom: 0.4rem; }}
    h2 {{ font-size: 1.5rem; border-bottom: 1px solid var(--border); padding-bottom: 0.3rem; }}
    a {{ color: var(--accent); }}
    code {{
      background: var(--code-bg); border: 1px solid var(--border);
      border-radius: 4px; padding: 0.1em 0.4em; font-size: 0.9em;
    }}
    pre {{
      background: var(--code-bg); border: 1px solid var(--border);
      border-radius: 6px; padding: 1rem; overflow-x: auto;
    }}
    pre code {{ background: none; border: none; padding: 0; font-size: 0.85em; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid var(--border); padding: 0.5rem 0.75rem; text-align: left; }}
    th {{ background: var(--code-bg); font-weight: 600; }}
    blockquote {{
      border-left: 4px solid var(--accent); margin: 0;
      padding: 0.5rem 1rem; background: var(--code-bg);
    }}
    .mermaid {{ overflow-x: auto; }}
    hr {{ border: none; border-top: 1px solid var(--border); margin: 2rem 0; }}
    .doc-section {{ margin-bottom: 4rem; }}
    .section-title {{
      background: var(--code-bg); border: 1px solid var(--border);
      border-radius: 6px; padding: 0.5rem 1rem; margin: 3rem 0 1.5rem;
      font-size: 0.8rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.08em; color: var(--accent);
    }}
    footer {{
      text-align: center; font-size: 0.8rem; color: #888;
      padding: 2rem; border-top: 1px solid var(--border);
    }}
  </style>
</head>
<body>
  <nav>
    <strong>Ledger docs</strong>
    <a href="#architecture">Architecture</a>
    <a href="#user-guide">User Guide</a>
    <a href="#extraction-corner-cases">Parser Notes</a>
    {version_nav}
  </nav>
  <main>
    <h1>Ledger — Documentation{version_suffix}</h1>
    <p>Generated {today} from <code>spec/*.md</code>.
       Source: <a href="https://github.com/chanmainvest/trade_history">chanmainvest/trade_history</a>.</p>
{body}
  </main>
  <footer>Built by <code>scripts/build_docs.py</code>{version_suffix} &mdash; {today}</footer>
</body>
</html>
"""

SECTION_IDS = {
    "ARCHITECTURE.md": "architecture",
    "USER-GUIDE.md": "user-guide",
    "EXTRACTION-CORNER-CASES.md": "extraction-corner-cases",
}

SECTION_LABELS = {
    "ARCHITECTURE.md": "Architecture Reference",
    "USER-GUIDE.md": "User Guide",
    "EXTRACTION-CORNER-CASES.md": "Parser Notes & Corner Cases",
}


def _convert_md(text: str) -> str:
    """Convert Markdown to HTML, with Mermaid fenced-code passthrough."""
    # Try markdown + pymdownx first
    if importlib.util.find_spec("markdown") and importlib.util.find_spec("pymdownx"):
        import markdown
        from pymdownx.superfences import SuperFencesExtension  # noqa: F401

        def mermaid_fence(source, language, css_class, options, md, **kwargs):
            return f'<div class="mermaid">\n{source}\n</div>'

        ext = SuperFencesExtension(
            custom_fences=[
                {
                    "name": "mermaid",
                    "class": "mermaid",
                    "format": mermaid_fence,
                }
            ]
        )
        return markdown.markdown(
            text,
            extensions=[
                "tables",
                "fenced_code",
                "codehilite",
                "toc",
                "pymdownx.superfences",
            ],
            extension_configs={"pymdownx.superfences": {"custom_fences": [
                {"name": "mermaid", "class": "mermaid", "format": mermaid_fence}
            ]}},
        )

    if importlib.util.find_spec("markdown"):
        import markdown

        # Manually extract mermaid blocks before rendering
        mermaid_placeholder = {}
        counter = [0]

        def extract_mermaid(m: re.Match) -> str:
            key = f"MERMAID_PLACEHOLDER_{counter[0]}"
            mermaid_placeholder[key] = f'<div class="mermaid">\n{m.group(1)}\n</div>'
            counter[0] += 1
            return key

        text2 = re.sub(r"```mermaid\n(.*?)```", extract_mermaid, text, flags=re.DOTALL)
        html = markdown.markdown(text2, extensions=["tables", "fenced_code", "toc"])
        for key, val in mermaid_placeholder.items():
            html = html.replace(f"<p>{key}</p>", val).replace(key, val)
        return html

    # Absolute fallback: wrap in <pre>
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<pre>{escaped}</pre>"


def build(version: str | None = None) -> None:
    DOC_DIR.mkdir(exist_ok=True)

    version_suffix = f" — {version}" if version else ""
    version_nav = f'<span style="margin-left:auto;opacity:0.6">{version}</span>' if version else ""

    sections_html: list[str] = []
    for filename in SPEC_ORDER:
        path = SPEC_DIR / filename
        if not path.exists():
            print(f"  WARNING: {path} not found — skipping", file=sys.stderr)
            continue
        section_id = SECTION_IDS[filename]
        label = SECTION_LABELS[filename]
        raw = path.read_text(encoding="utf-8")
        body_html = _convert_md(raw)
        sections_html.append(
            f'<div class="doc-section" id="{section_id}">\n'
            f'  <div class="section-title">{label}</div>\n'
            f"  {body_html}\n"
            f"</div>\n"
        )

    html = HTML_TEMPLATE.format(
        version_suffix=version_suffix,
        version_nav=version_nav,
        today=date.today().isoformat(),
        body="\n".join(sections_html),
    )

    out = DOC_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    size_kb = out.stat().st_size // 1024
    print(f"Written {out} ({size_kb} KB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build doc/index.html from spec/*.md")
    parser.add_argument("--version", metavar="TAG", help="Release tag, e.g. v1.2.0")
    args = parser.parse_args()
    build(version=args.version)


if __name__ == "__main__":
    main()
