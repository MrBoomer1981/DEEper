"""Render a Markdown report to DOCX and PDF."""

import os
import subprocess
import sys
from pathlib import Path

import mistune

ROOT = Path(__file__).resolve().parent.parent
HOMEBREW_LIB = "/opt/homebrew/lib"

PDF_CSS = """
@page {
  size: A4;
  margin: 2cm 2cm 2.2cm;
  @bottom-center { content: counter(page); font-size: 9pt; color: #888; }
}
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 11pt; line-height: 1.5; color: #1a1a1a; }
h1 { font-size: 20pt; margin: 0 0 12pt; }
h2 { font-size: 15pt; margin: 18pt 0 8pt; padding-bottom: 3pt; border-bottom: 1px solid #ddd; }
h3 { font-size: 12.5pt; margin: 14pt 0 6pt; }
a { color: #1a5fb4; text-decoration: none; overflow-wrap: anywhere; }
table { width: 100%; margin: 10pt 0; border-collapse: collapse; font-size: 9.5pt; }
th, td { padding: 4pt 6pt; border: 1px solid #ccc; vertical-align: top; }
th { background: #f3f3f3; }
code { font-family: Menlo, monospace; font-size: 9.5pt; }
blockquote { margin: 8pt 0; padding-left: 10pt; border-left: 3px solid #ccc; color: #555; }
"""

_markdown = mistune.create_markdown(plugins=["table", "strikethrough", "url"])


def md_to_html(markdown_text: str) -> str:
    return _markdown(markdown_text)


def write_docx(markdown_text: str, path: Path) -> None:
    from docx import Document
    from htmldocx import HtmlToDocx

    document = Document()
    HtmlToDocx().add_html_to_document(md_to_html(markdown_text), document)
    document.save(path)


def write_pdf(md_path: Path, pdf_path: Path) -> None:
    """Render in a child process: WeasyPrint loads Homebrew's pango via dyld,
    which reads DYLD_FALLBACK_LIBRARY_PATH only at process start."""
    env = os.environ.copy()
    if sys.platform == "darwin" and os.path.isdir(HOMEBREW_LIB):
        env["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(
            filter(None, [env.get("DYLD_FALLBACK_LIBRARY_PATH"), HOMEBREW_LIB])
        )
    subprocess.run(
        [sys.executable, "-m", "my_browser.export", str(md_path), str(pdf_path)],
        env=env,
        cwd=ROOT,
        check=True,
    )


def _render_pdf(md_path: Path, pdf_path: Path) -> None:
    from weasyprint import CSS, HTML

    html = f'<!doctype html><html><head><meta charset="utf-8"></head><body>{md_to_html(md_path.read_text("utf-8"))}</body></html>'
    HTML(string=html, base_url=str(md_path.parent)).write_pdf(pdf_path, stylesheets=[CSS(string=PDF_CSS)])


if __name__ == "__main__":
    _render_pdf(Path(sys.argv[1]), Path(sys.argv[2]))
