#!/usr/bin/env python3
"""Build docs/index.html from all Markdown files in spec/.

Usage:
    uv run python scripts/build_docs.py [--version TAG]
    uv run python scripts/build_docs.py --check

Output: docs/index.html  (standalone — no external assets except Mermaid CDN)

Zero external dependencies: uses the built-in Markdown renderer below.
If the optional 'markdown' + 'pymdownx' packages are installed they are
used instead for more accurate CommonMark compliance.
"""
from __future__ import annotations

import argparse
import importlib.util
import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT / "spec"
DOC_DIR = ROOT / "docs"

# ---------------------------------------------------------------------------
# Ordered focused specs. User Guide remains first for the human landing page.
# ---------------------------------------------------------------------------
DOC_SECTIONS = [
    ("USER-GUIDE.md", "user-guide", "User Guide"),
    ("INDEX.md", "spec-index", "Specification Index"),
    ("CURRENT-STATE.md", "current-state", "Current State"),
    ("ARCHITECTURE.md", "architecture", "Architecture"),
    ("DATA-MODEL.md", "data-model", "Data Model"),
    ("INGESTION.md", "ingestion", "Ingestion"),
    ("PARSER-CONTRACT.md", "parser-contract", "Parser Contract"),
    ("RECONCILIATION.md", "reconciliation", "Reconciliation"),
    ("API-UI.md", "api-ui", "API and UI"),
    ("OPERATIONS.md", "operations", "Operations"),
    ("EXTRACTION-CORNER-CASES.md", "extraction-corner-cases", "Cross-parser Notes"),
    ("parsers/CIBC.md", "parser-cibc", "Parser: CIBC"),
    ("parsers/HSBC.md", "parser-hsbc", "Parser: HSBC"),
    ("parsers/RBC.md", "parser-rbc", "Parser: RBC"),
    ("parsers/TD.md", "parser-td", "Parser: TD"),
]

SECTION_IDS = {filename: section_id for filename, section_id, _ in DOC_SECTIONS}

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Trade History — Documentation{version_suffix}</title>
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
      font-size: 0.9rem; flex-wrap: wrap;
    }}
    nav a {{ color: var(--accent); text-decoration: none; font-weight: 500; }}
    nav a:hover {{ text-decoration: underline; }}
    nav .brand {{ font-weight: 700; margin-right: 0.5rem; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 2rem; }}
    h1 {{ font-size: 2rem; border-bottom: 2px solid var(--border); padding-bottom: 0.4rem; margin-top: 2rem; }}
    h2 {{ font-size: 1.5rem; border-bottom: 1px solid var(--border); padding-bottom: 0.3rem; margin-top: 2rem; }}
    h3 {{ font-size: 1.2rem; margin-top: 1.5rem; }}
    h4 {{ font-size: 1rem; margin-top: 1.2rem; }}
    a {{ color: var(--accent); }}
    code {{
      background: var(--code-bg); border: 1px solid var(--border);
      border-radius: 4px; padding: 0.1em 0.4em; font-size: 0.88em;
      font-family: "SFMono-Regular", Consolas, monospace;
    }}
    pre {{
      background: var(--code-bg); border: 1px solid var(--border);
      border-radius: 6px; padding: 1rem; overflow-x: auto; margin: 1rem 0;
    }}
    pre code {{ background: none; border: none; padding: 0; font-size: 0.85em; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.95em; }}
    th, td {{ border: 1px solid var(--border); padding: 0.5rem 0.75rem; text-align: left; }}
    th {{ background: var(--code-bg); font-weight: 600; }}
    tr:nth-child(even) td {{ background: color-mix(in srgb, var(--code-bg) 40%, transparent); }}
    blockquote {{
      border-left: 4px solid var(--accent); margin: 1rem 0;
      padding: 0.5rem 1rem; background: var(--code-bg);
      border-radius: 0 4px 4px 0;
    }}
    blockquote p {{ margin: 0.25rem 0; }}
    .mermaid {{ overflow-x: auto; text-align: center; margin: 1.5rem 0; }}
    hr {{ border: none; border-top: 1px solid var(--border); margin: 2rem 0; }}
    ul, ol {{ padding-left: 1.75rem; }}
    li {{ margin: 0.25rem 0; }}
    p {{ margin: 0.75rem 0; }}
    .doc-section {{ margin-bottom: 4rem; }}
    .section-banner {{
      background: linear-gradient(135deg, var(--accent) 0%, color-mix(in srgb, var(--accent) 60%, transparent) 100%);
      color: #fff; border-radius: 8px; padding: 1rem 1.5rem;
      margin: 3rem 0 1.5rem; font-size: 0.85rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.1em;
    }}
    footer {{
      text-align: center; font-size: 0.8rem; color: #888;
      padding: 2rem; border-top: 1px solid var(--border); margin-top: 2rem;
    }}
    .toc {{ background: var(--code-bg); border: 1px solid var(--border);
      border-radius: 6px; padding: 1rem 1.5rem; margin: 1.5rem 0; }}
    .toc ul {{ margin: 0; padding-left: 1.25rem; }}
    .toc li {{ margin: 0.15rem 0; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <nav>
    <span class="brand">Trade History</span>
    <a href="#user-guide">User Guide</a>
    <a href="#current-state">Current State</a>
    <a href="#architecture">Architecture</a>
    <a href="#reconciliation">Reconciliation</a>
    <a href="#operations">Operations</a>
    {version_nav}
  </nav>
  <main>
    <h1>Trade History — Documentation{version_suffix}</h1>
    <p>Generated from the focused Markdown sources under <code>spec/</code> &mdash;
       <a href="https://github.com/chanmainvest/trade_history">chanmainvest/trade_history</a>.</p>
{body}
  </main>
  <footer>Built reproducibly by <code>scripts/build_docs.py</code>{version_suffix}</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Built-in Markdown → HTML renderer (no external dependencies)
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """HTML-escape plain text (not for code, which escapes its own content)."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def _inline(text: str) -> str:
    """Process inline Markdown: links, bold, italic, inline code."""
    # Inline code (must come first to avoid processing its contents)
    parts: list[str] = []
    pos = 0
    for m in re.finditer(r"`([^`]+)`", text):
        parts.append(_inline_no_code(text[pos:m.start()]))
        parts.append(f"<code>{_esc(m.group(1))}</code>")
        pos = m.end()
    parts.append(_inline_no_code(text[pos:]))
    return "".join(parts)


def _inline_no_code(text: str) -> str:
    """Bold, italic, links — applied to text that has no inline code."""
    # Links: [text](url) and bare <url>
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{_esc(m.group(2))}">{_esc(m.group(1))}</a>',
        text,
    )
    text = re.sub(r"<(https?://[^>]+)>", lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', text)
    # Bold+italic
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"_(.+?)_", r"<em>\1</em>", text)
    # HTML entities for bare < >
    text = text.replace(" -> ", " &rarr; ").replace("→", "&rarr;").replace("←", "&larr;")
    return text


def _convert_md_builtin(text: str) -> str:
    """Convert Markdown to HTML using only the Python standard library."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    in_list: list[str] = []   # stack: 'ul' or 'ol'
    in_blockquote = False
    in_para: list[str] = []

    def flush_para() -> None:
        nonlocal in_para
        if in_para:
            out.append(f"<p>{''.join(_inline(line_part) for line_part in in_para)}</p>")
            in_para = []

    def close_lists(target_depth: int = 0) -> None:
        while len(in_list) > target_depth:
            tag = in_list.pop()
            out.append(f"</{tag}>")

    def close_blockquote() -> None:
        nonlocal in_blockquote
        if in_blockquote:
            out.append("</blockquote>")
            in_blockquote = False

    while i < len(lines):
        line = lines[i]

        # ── Fenced code block ────────────────────────────────────────────
        m_fence = re.match(r"^(`{3,}|~{3,})(\w*)", line)
        if m_fence:
            flush_para()
            close_lists()
            close_blockquote()
            fence_char = m_fence.group(1)
            lang = m_fence.group(2).lower()
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].startswith(fence_char[:3]):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            code_body = "\n".join(code_lines)
            if lang == "mermaid":
                out.append(f'<div class="mermaid">\n{code_body}\n</div>')
            else:
                out.append(f"<pre><code>{_esc(code_body)}</code></pre>")
            continue

        # ── Heading ──────────────────────────────────────────────────────
        m_h = re.match(r"^(#{1,6})\s+(.*)", line)
        if m_h:
            flush_para()
            close_lists()
            close_blockquote()
            level = len(m_h.group(1))
            content = _inline(m_h.group(2).strip())
            slug = re.sub(r"[^\w\s-]", "", m_h.group(2).lower())
            slug = re.sub(r"[\s]+", "-", slug.strip())
            out.append(f"<h{level} id=\"{slug}\">{content}</h{level}>")
            i += 1
            continue

        # ── Horizontal rule ──────────────────────────────────────────────
        if re.match(r"^(-{3,}|\*{3,}|_{3,})\s*$", line):
            flush_para()
            close_lists()
            close_blockquote()
            out.append("<hr>")
            i += 1
            continue

        # ── Table ────────────────────────────────────────────────────────
        if "|" in line and i + 1 < len(lines) and re.match(r"^\|?[\s\-:|]+\|", lines[i + 1]):
            flush_para()
            close_lists()
            close_blockquote()
            header_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 1  # skip separator row
            i += 1
            out.append('<table><thead><tr>')
            for c in header_cells:
                out.append(f"<th>{_inline(c)}</th>")
            out.append("</tr></thead><tbody>")
            while i < len(lines) and "|" in lines[i]:
                row_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out.append("<tr>")
                for c in row_cells:
                    out.append(f"<td>{_inline(c)}</td>")
                out.append("</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        # ── Blockquote ───────────────────────────────────────────────────
        m_bq = re.match(r"^>\s?(.*)", line)
        if m_bq:
            flush_para()
            close_lists()
            if not in_blockquote:
                out.append("<blockquote>")
                in_blockquote = True
            out.append(f"<p>{_inline(m_bq.group(1))}</p>")
            i += 1
            continue
        elif in_blockquote and line.strip() == "":
            close_blockquote()

        # ── List item ────────────────────────────────────────────────────
        m_ul = re.match(r"^(\s*)[-*+]\s+(.*)", line)
        m_ol = re.match(r"^(\s*)\d+\.\s+(.*)", line)
        if m_ul or m_ol:
            flush_para()
            close_blockquote()
            m_li = m_ul or m_ol
            indent = len(m_li.group(1))
            tag = "ul" if m_ul else "ol"
            depth = indent // 2 + 1
            while len(in_list) < depth:
                out.append(f"<{tag}>")
                in_list.append(tag)
            while len(in_list) > depth:
                t = in_list.pop()
                out.append(f"</{t}>")
            out.append(f"<li>{_inline(m_li.group(2))}</li>")
            i += 1
            continue
        elif in_list and line.strip() == "":
            close_lists()

        # ── Blank line ───────────────────────────────────────────────────
        if line.strip() == "":
            flush_para()
            i += 1
            continue

        # ── Paragraph / continuation ─────────────────────────────────────
        close_blockquote()
        in_para.append(line + " ")
        i += 1

    flush_para()
    close_lists()
    close_blockquote()
    return "\n".join(out)


def _convert_md(text: str) -> str:
    """Convert Markdown to HTML, preferring installed 'markdown' package."""
    if importlib.util.find_spec("markdown") is not None:
        try:
            import markdown

            def mermaid_fence(source, language, css_class, options, md, **kwargs):  # type: ignore[override]
                return f'<div class="mermaid">\n{source}\n</div>'

            extra_kwargs: dict = {}
            if importlib.util.find_spec("pymdownx") is not None:
                extra_kwargs = {
                    "extensions": ["tables", "fenced_code", "toc", "pymdownx.superfences"],
                    "extension_configs": {
                        "pymdownx.superfences": {
                            "custom_fences": [
                                {"name": "mermaid", "class": "mermaid", "format": mermaid_fence}
                            ]
                        }
                    },
                }
            else:
                extra_kwargs = {"extensions": ["tables", "fenced_code", "toc"]}
            return markdown.markdown(text, **extra_kwargs)
        except Exception as exc:
            print(f"  WARNING: markdown package failed ({exc}), using built-in renderer", file=sys.stderr)

    return _convert_md_builtin(text)


def _rewrite_spec_links(text: str, source_name: str) -> str:
    """Point bundled-spec links at their section in the generated page."""
    source_dir = posixpath.dirname(source_name)

    def replace(match: re.Match[str]) -> str:
        label, destination = match.group(1), match.group(2)
        path, _, _fragment = destination.partition("#")
        if not path.lower().endswith(".md"):
            return match.group(0)
        normalized = posixpath.normpath(posixpath.join(source_dir, path))
        section_id = SECTION_IDS.get(normalized)
        if section_id is None:
            return match.group(0)
        return f"[{label}](#{section_id})"

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace, text)


def render(version: str | None = None) -> str:
    """Render deterministic HTML for the focused specification set."""
    version_suffix = f" \u2014 {version}" if version else ""
    version_nav = (
        f'<span style="margin-left:auto;opacity:0.6;font-size:0.85rem">{version}</span>'
        if version else ""
    )

    sections_html: list[str] = []
    for filename, section_id, label in DOC_SECTIONS:
        path = SPEC_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"required documentation source is missing: {path}")
        raw = path.read_text(encoding="utf-8")
        raw = _rewrite_spec_links(raw, filename)
        body_html = _convert_md(raw)
        sections_html.append(
            f'<div class="doc-section" id="{section_id}">\n'
            f'  <div class="section-banner">{label}</div>\n'
            f"  {body_html}\n"
            f"</div>\n"
        )

    return HTML_TEMPLATE.format(
        version_suffix=version_suffix,
        version_nav=version_nav,
        body="\n".join(sections_html),
    )


def build(version: str | None = None, *, check: bool = False) -> bool:
    """Write docs, or return whether the committed output is current."""
    html = render(version=version)
    out = DOC_DIR / "index.html"
    if check:
        current = out.read_text(encoding="utf-8") if out.exists() else None
        if current != html:
            print(
                "docs/index.html is stale; run "
                "uv run python scripts/build_docs.py and commit the result.",
                file=sys.stderr,
            )
            return False
        print("docs/index.html is current")
        return True

    DOC_DIR.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    size_kb = out.stat().st_size // 1024
    print(f"Written {out} ({size_kb} KB)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Build docs/index.html from spec/*.md")
    parser.add_argument("--version", metavar="TAG", help="Release tag, e.g. v1.2.0")
    parser.add_argument("--check", action="store_true", help="Fail when docs/index.html is stale")
    args = parser.parse_args()
    if args.check and args.version:
        parser.error("--check and --version cannot be combined")
    if not build(version=args.version, check=args.check):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
