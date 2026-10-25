"""Cut NiceGUI's Material Icons font down to the icons DEEper shows: static/fonts/icons.woff2.

    uv run dev/icons.py           # after the code names an icon it did not use before
    uv run dev/icons.py --check   # exits 1, naming them, when the code uses an icon the cut font lacks

The full font is 129 KB, the biggest file of a first visit, for a few dozen of its 2 229 icons. An icon is a
ligature: the browser draws the word «open_in_new» as one glyph, so a name missing from the cut font shows up as
that word. The names come from the web app's code (every quoted string in my_browser/core and static/app.js that
names an icon, and `icon=` in props) and from Quasar's own icon set, which NiceGUI's components use (the select's
arrow, the input's clear button, notifications). core/web.py declares the font as «DEEper Icons» and preloads it;
app.css points Quasar's .material-icons class at it.
"""

import argparse
import re
import sys
from pathlib import Path

import nicegui
from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "static" / "fonts" / "icons.woff2"
NICEGUI_STATIC = Path(nicegui.__file__).parent / "static"
SOURCES = [*sorted((ROOT / "my_browser" / "core").rglob("*.py")), ROOT / "static" / "app.js"]


def full_font() -> Path:
    css = (NICEGUI_STATIC / "fonts.css").read_text()
    found = re.search(r'font-family: "Material Icons";[^}]*?src: url\((fonts/[\w.-]+\.woff2)\)', css)
    return NICEGUI_STATIC / found.group(1)


def ligatures(font: TTFont) -> dict[str, str]:
    """Icon name -> its glyph, from the font's ligature substitutions."""
    char = {glyph: chr(code) for code, glyph in font.getBestCmap().items()}
    names = {}
    for lookup in font["GSUB"].table.LookupList.Lookup:
        for table in lookup.SubTable:
            table = getattr(table, "ExtSubTable", table)
            for first, ligs in getattr(table, "ligatures", {}).items():
                for lig in ligs:
                    letters = [first, *lig.Component]
                    if all(g in char for g in letters):
                        names["".join(char[g] for g in letters)] = lig.LigGlyph
    return names


def quasar_icons() -> set[str]:
    """Every name in Quasar's material-icons icon set, as its bundle carries it."""
    js = (NICEGUI_STATIC / "quasar.umd.prod.js").read_text()
    start = js.index('name:"material-icons"')
    depth, end = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(js[end], 0)
        end += 1
    return set(re.findall(r'"([a-z0-9_]+)"', js[start:end])) - {"material-icons"}


def used_icons(known: dict[str, str]) -> set[str]:
    found = set()
    for path in SOURCES:
        text = path.read_text("utf-8")
        found |= set(re.findall(r"""["']([a-z0-9_]+)["']""", text)) | set(re.findall(r"icon=([a-z0-9_]+)", text))
    return {name for name in found | quasar_icons() if name in known}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="only check that static/fonts/icons.woff2 has every icon used")
    args = parser.parse_args()
    source = TTFont(full_font())
    needed = used_icons(ligatures(source))
    if args.check:
        missing = sorted(needed - set(ligatures(TTFont(OUT)))) if OUT.exists() else sorted(needed)
        if missing:
            sys.exit(f"static/fonts/icons.woff2 lacks {', '.join(missing)}: run uv run dev/icons.py")
        print(f"icons ok: {len(needed)} names")
        return
    names = ligatures(source)
    options = subset.Options()
    options.layout_features = ["*"]
    options.layout_closure = False  # keep only the ligatures of the glyphs kept, not every icon the letters spell
    options.flavor = "woff2"
    subsetter = subset.Subsetter(options)
    subsetter.populate(glyphs=[names[n] for n in needed], unicodes=sorted({ord(c) for n in needed for c in n}))
    subsetter.subset(source)
    source.save(OUT)
    print(f"{OUT.relative_to(ROOT)}: {len(needed)} icons, {OUT.stat().st_size} bytes "
          f"(full font {full_font().stat().st_size}): {', '.join(sorted(needed))}")


if __name__ == "__main__":
    main()
