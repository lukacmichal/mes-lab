"""Labels of the plant: the KLT box label (VDA 4902) and the pallet label with the SSCC (GS1-128).

A template (config/labels.yaml, changed on the settings page) is a set of boxes in millimetres; the value
of a box is text with {placeholders}, filled from what the MES knows of the box or pallet when it prints
the label. The MES keeps the filled values and the layout of every label it printed, so a label can be shown
and printed again exactly as it was.
"""

from __future__ import annotations

import re
import string

# placeholder -> (English, Slovak); what a template may print
PLACEHOLDERS: dict[str, tuple[str, str]] = {
    "customer_name": ("the customer", "zákazník"),
    "customer_city": ("the customer's town and country", "mesto a krajina zákazníka"),
    "dock": ("where the customer unloads (unloading point)", "kde zákazník vykladá (miesto vykládky)"),
    "supplier_name": ("our company", "naša firma"),
    "supplier_address": ("our address", "naša adresa"),
    "supplier_no": ("our number at the customer", "naše číslo u zákazníka"),
    "delivery_note": ("the delivery note (known at dispatch)", "dodací list (známy pri expedícii)"),
    "customer_part": ("the customer's part number", "číslo dielu zákazníka"),
    "supplier_part": ("our part number", "naše číslo dielu"),
    "description": ("the product", "výrobok"),
    "change_level": ("the drawing index", "index výkresu"),
    "qty": ("pieces in the box / on the pallet", "kusov v boxe / na palete"),
    "boxes": ("boxes (on the pallet: KLT boxes)", "balení (na palete: boxov KLT)"),
    "net_kg": ("net weight, kg", "hmotnosť netto, kg"),
    "gross_kg": ("gross weight, kg", "hmotnosť brutto, kg"),
    "date": ("packing date", "dátum balenia"),
    "package_id": ("the serial number of the box or pallet", "sériové číslo boxu alebo palety"),
    "batch": ("the work order(s) of the pieces (batch)", "výrobná zákazka kusov (šarža)"),
    "sscc": ("the SSCC of the pallet (18 digits)", "SSCC palety (18 číslic)"),
    "sscc_text": ("the SSCC with spaces, for people", "SSCC s medzerami, pre ľudí"),
    "line": ("the packing line", "baliaca linka"),
}

_FIELD = re.compile(r"\{([a-z_]+)\}")


def placeholders_of(text: str) -> list[str]:
    return [name for _, name, _, _ in string.Formatter().parse(text) if name]


def unknown(text: str) -> list[str]:
    """Placeholders a template uses that the MES does not know."""
    try:
        names = placeholders_of(text)
    except ValueError:
        return ["{ … } not closed"]
    return [n for n in names if n not in PLACEHOLDERS]


def fill(text: str, data: dict) -> str:
    return _FIELD.sub(lambda m: str(data.get(m.group(1), "")), text)
