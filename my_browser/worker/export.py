"""Render a Markdown report to DOCX and PDF."""

import os
import subprocess
import sys
from pathlib import Path

import mistune

ROOT = Path(__file__).resolve().parents[2]
HOMEBREW_LIB = "/opt/homebrew/lib"

FONTS = ROOT / "static" / "fonts"

# Print-friendly take on the AVEN brandbook: light page, night text, bordo links and rules,
# an amber line under the title, Onest / Unbounded / JetBrains Mono.
PDF_CSS = f"""
@font-face {{ font-family: 'Onest'; src: url('{(FONTS / "Onest.ttf").as_uri()}'); }}
@font-face {{ font-family: 'Unbounded'; src: url('{(FONTS / "Unbounded.ttf").as_uri()}'); }}
@font-face {{ font-family: 'JetBrains Mono'; src: url('{(FONTS / "JetBrainsMono.ttf").as_uri()}'); }}
@page {{
  size: A4;
  margin: 2cm 2cm 2.2cm;
  @bottom-left {{ content: "DEEPER · made by aven"; font-family: 'JetBrains Mono'; font-size: 8pt; color: #9a918a; }}
  @bottom-right {{ content: counter(page); font-family: 'JetBrains Mono'; font-size: 8pt; color: #9a918a; }}
}}
body {{ font-family: 'Onest', sans-serif; font-size: 10.5pt; line-height: 1.55; color: #14100E; }}
h1 {{ font-size: 20pt; font-weight: 700; line-height: 1.2; margin: 0 0 14pt; padding-bottom: 10pt; border-bottom: 2.5pt solid #E8A24A; }}
h2 {{ font-size: 14pt; font-weight: 600; margin: 20pt 0 6pt; }}
h3 {{ font-size: 11.5pt; font-weight: 600; margin: 14pt 0 4pt; }}
a {{ color: #7A2A35; text-decoration: none; overflow-wrap: anywhere; }}
strong {{ font-weight: 700; }}
table {{ width: 100%; margin: 10pt 0; border-collapse: collapse; font-size: 9pt; }}
th, td {{ padding: 4pt 6pt; border: 0.6pt solid #d9d0c4; vertical-align: top; }}
th {{ background: #F2EBE1; font-weight: 600; }}
code {{ font-family: 'JetBrains Mono'; font-size: 8.5pt; }}
blockquote {{ margin: 8pt 0; padding-left: 10pt; border-left: 2pt solid #7A2A35; color: #4a403a; }}
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
        [sys.executable, "-m", "my_browser.worker.export", str(md_path), str(pdf_path)],
        env=env,
        cwd=ROOT,
        check=True,
    )


def _render_pdf(md_path: Path, pdf_path: Path) -> None:
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration  # @font-face is ignored without it

    html = f'<!doctype html><html><head><meta charset="utf-8"></head><body>{md_to_html(md_path.read_text("utf-8"))}</body></html>'
    fonts = FontConfiguration()
    HTML(string=html, base_url=str(md_path.parent)).write_pdf(
        pdf_path, stylesheets=[CSS(string=PDF_CSS, font_config=fonts)], font_config=fonts
    )


if __name__ == "__main__":
    _render_pdf(Path(sys.argv[1]), Path(sys.argv[2]))
