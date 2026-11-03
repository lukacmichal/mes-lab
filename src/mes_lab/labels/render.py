"""A label as SVG in millimetres: every box with its small title in the corner, its value and, when the box
has one, the bar code of the value (after its data identifier). Black on white, as a thermal printer prints."""

from __future__ import annotations

from html import escape

from . import barcode, fill

QUIET = 10                   # modules of white before and after a bar code
# the widest narrow bar a label uses (mm); a long code in a narrow box gets narrower bars. GS1 logistic labels
# ask for 0.495 mm and more for the SSCC; Code 39 on box labels is usually printed with 0.25 to 0.38 mm.
MODULE_MM = {"code39": 0.38, "code128": 0.38, "gs1": 0.5}


def _bars(kind: str, data: str, x: float, y: float, w: float, h: float, gs1: bool) -> str:
    modules = barcode.encode(kind, data, gs1=gs1)
    total = barcode.width(modules) + 2 * QUIET
    m = min(w / total, MODULE_MM["gs1" if gs1 else kind])   # the narrow bar in mm: as printers do, not wider
    out, pos, bar = [], x + QUIET * m, True
    for width in modules:
        if bar:
            out.append(f'<rect x="{pos:.3f}" y="{y:.3f}" width="{width * m:.3f}" height="{h:.3f}"/>')
        pos += width * m
        bar = not bar
    return "".join(out)


def svg(layout: dict, data: dict, title: str | None = None) -> str:
    """`layout` = {"size_mm": [w, h], "fields": {code: field as in LabelField}} (a template, or the snapshot
    a printed label keeps)."""
    w, h = layout["size_mm"]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}mm" height="{h}mm" '
             f'font-family="Arial, Helvetica, sans-serif">']
    if title:
        parts.append(f"<title>{escape(title)}</title>")
    parts.append(f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fff"/>')
    parts.append('<g fill="#000">')
    for f in layout["fields"].values():
        if not f.get("show", True):
            continue
        x, y, fw, fh = f["x"], f["y"], f["w"], f["h"]
        parts.append(f'<rect x="{x}" y="{y}" width="{fw}" height="{fh}" fill="none" stroke="#000" stroke-width="0.25"/>')
        top = y + 2.2
        if f.get("title"):
            parts.append(f'<text x="{x + 1}" y="{top}" font-size="1.9">{escape(f["title"])}</text>')
        value = fill(f.get("value", ""), data)
        size = f.get("size", 3.0)
        weight = ' font-weight="bold"' if f.get("bold") else ""
        text_y = top + 0.6 + size * 0.85
        parts.append(f'<text x="{x + 1}" y="{text_y:.2f}" font-size="{size}"{weight}>{escape(value)}</text>')
        kind = f.get("barcode")
        if kind and value:
            gs1 = kind == "code128" and f.get("di") == "(00)"          # GS1-128: the SSCC after its AI (00)
            code_data = "00" + value.replace(" ", "") if gs1 else f.get("di", "") + value
            bar_y = text_y + 1.0
            bar_h = y + fh - bar_y - 1.0
            if bar_h > 2:
                try:
                    parts.append(_bars(kind, code_data, x + 1, bar_y, fw - 2, bar_h, gs1))
                except ValueError as e:              # a value the code cannot carry: say so on the label
                    parts.append(f'<text x="{x + 1}" y="{bar_y + 2.5:.2f}" font-size="2">{escape(str(e))}</text>')
    parts.append("</g></svg>")
    return "".join(parts)
