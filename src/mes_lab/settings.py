"""Settings: what people change on the settings page of the MES, applied over the config files.

The YAML files in config/ are the plant as it was built (they come with the code). What people change on
the web (a cycle time, the name of a defect code, the start of a shift...) the MES keeps as *overrides*:
a path into the config and the new value. The MES and the simulation build the plant from the same files
with the same overrides, so they always agree on it.

A path names things by their codes, never by their place in a list:

    materials/PCBA-BCM-200/routing/SMT_BOT/steps/PNP_CHIP     the cycle time of a step of a routing
    shifts/B/start                                             the start of the afternoon shift
    downtime_reasons/BARCODE_FAULT                             a new reason: {name, category}

A value None takes the key away (a component out of a pass of the BOM, a reason added on the web).

FIELDS says what may be changed, of what kind, within which limits, and whether the machines need it:
plant=True means the simulation builds the plant anew with the change (a new run, as after a restart),
plant=False only the MES (names, categories, limits of SPC, planning numbers).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

from mes_lab.model.loader import ModelError, _read, build_model
from mes_lab.model.schema import PlantModel


class SettingError(ValueError):
    """A change that may not be made, said in English and in Slovak."""

    def __init__(self, en: str, sk: str | None = None):
        super().__init__(en)
        self.en = en
        self.sk = sk or en

    def as_dict(self) -> dict:
        return {"en": self.en, "sk": self.sk}


@dataclass(frozen=True)
class Field:
    pattern: str                      # segments, "*" = any code
    kind: str                         # text, int, float, time, bool, choice, customer, entry, qty
    lo: float | None = None
    hi: float | None = None
    plant: bool = False               # the machines need it: the plant builds itself anew
    choices: tuple = ()

    def matches(self, segs: list[str]) -> bool:
        pat = self.pattern.split("/")
        return len(pat) == len(segs) and all(p in ("*", s) for p, s in zip(pat, segs))


# the loss buckets of the OEE: a downtime reason belongs to one of them
DOWNTIME_CATEGORIES = ("BREAKDOWN", "MINOR_STOP", "CHANGEOVER", "PLANNED", "MATERIAL", "PEOPLE", "IT", "NOT_SCHEDULED")

FIELDS: tuple[Field, ...] = (
    # the plant and its shifts
    Field("plant/plan_efficiency", "float", 0.5, 1.0),
    Field("shifts/*/name", "text"),
    Field("shifts/*/start", "time", plant=True),
    Field("crew/breaks/*/start", "int", 30, 450, plant=True),
    Field("crew/breaks/*/length", "int", 5, 60, plant=True),
    # products, their packing and planning
    Field("customers/*/name", "text"),
    Field("materials/*/name", "text"),
    Field("materials/*/customer", "customer", plant=True),
    Field("materials/*/packing/klt_qty", "int", 1, 100, plant=True),
    Field("materials/*/packing/klt_per_pallet", "int", 1, 80, plant=True),
    Field("materials/*/kit/per_bin", "int", 5, 500, plant=True),
    Field("materials/*/planning/safety_stock", "int", 0, 20000),
    Field("materials/*/planning/min_lot", "int", 10, 20000),
    Field("materials/*/planning/max_lot", "int", 10, 20000),
    # routings and the bill of materials of a board
    Field("materials/*/routing/*/name", "text"),
    Field("materials/*/routing/*/steps/*", "float", 1, 900, plant=True),
    Field("materials/*/routing/*/components/*", "qty", 1, 500, plant=True),
    # equipment
    Field("areas/*/lines/*/name", "text"),
    Field("areas/*/lines/*/station_names/*", "text"),
    Field("station_types/*/name", "text"),
    Field("station_types/*/changeover_min", "float", 0, 240, plant=True),
    Field("station_types/*/pm/interval_h", "float", 4, 2000, plant=True),
    Field("station_types/*/pm/duration_min", "float", 5, 480, plant=True),
    Field("station_types/*/measurements/*/lsl", "float"),
    Field("station_types/*/measurements/*/usl", "float"),
    Field("station_types/*/measurements/*/spc", "bool"),
    # reason codes
    Field("defect_codes/*", "entry"),
    Field("defect_codes/*/name", "text"),
    Field("defect_codes/*/category", "category", plant=True),       # the plant washes PRINT defects off
    Field("downtime_reasons/*", "entry"),
    Field("downtime_reasons/*/name", "text"),
    Field("downtime_reasons/*/category", "choice", plant=True, choices=DOWNTIME_CATEGORIES),
    Field("downtime_reasons/*/crew", "choice", plant=True, choices=("MAINTENANCE", "IT", None)),
    Field("downtime_reasons/*/needs", "choice", plant=True, choices=("meter", None)),
    # what goes on the labels, and the label templates (only the MES prints them: no new run)
    Field("customers/*/city", "text"),
    Field("customers/*/supplier_no", "ltext"),
    Field("customers/*/dock", "ltext"),
    Field("materials/*/customer_part", "ltext"),
    Field("materials/*/change_level", "ltext"),
    Field("materials/*/packing/unit_g", "float", 1, 20000),
    Field("materials/*/packing/klt_g", "float", 0, 30000),
    Field("labels/supplier/name", "text"),
    Field("labels/supplier/address", "ltext"),
    Field("labels/templates/*/name", "text"),
    Field("labels/templates/*/fields/*/title", "ltext"),
    Field("labels/templates/*/fields/*/value", "ltext"),
    Field("labels/templates/*/fields/*/x", "float", 0, 300),
    Field("labels/templates/*/fields/*/y", "float", 0, 300),
    Field("labels/templates/*/fields/*/w", "float", 1, 300),
    Field("labels/templates/*/fields/*/h", "float", 1, 300),
    Field("labels/templates/*/fields/*/size", "float", 1, 20),
    Field("labels/templates/*/fields/*/bold", "bool"),
    Field("labels/templates/*/fields/*/show", "bool"),
)

CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,29}$")
CATEGORY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,19}$")
SHIFT_H = (4, 12)                  # a shift lasts at least / at most this many hours


def field_for(path: str) -> Field | None:
    segs = path.split("/")
    return next((f for f in FIELDS if f.matches(segs)), None)


# ---------------------------------------------------------------------- the config with overrides
def _child(node: Any, seg: str) -> Any:
    if isinstance(node, dict):
        return node.get(seg)
    if isinstance(node, list):
        for el in node:
            if isinstance(el, dict) and seg in (el.get("code"), el.get("op"), el.get("name")):
                return el
    return None


def get(raw: dict, path: str) -> Any:
    """The value at a path (None when it is not there)."""
    node: Any = raw
    for seg in path.split("/"):
        node = _child(node, seg)
        if node is None:
            return None
    return node


def _set(raw: dict, path: str, value: Any) -> bool:
    segs = path.split("/")
    node: Any = raw
    for seg in segs[:-1]:
        child = _child(node, seg)
        if child is None and seg == "station_names" and isinstance(node, dict):
            child = node[seg] = {}
        if child is None:
            return False
        node = child
    if not isinstance(node, dict):
        return False
    if value is None:
        node.pop(segs[-1], None)
    else:
        node[segs[-1]] = copy.deepcopy(value)
    return True


def apply(raw: dict, overrides: dict[str, Any]) -> list[str]:
    """Put the overrides into the raw config (in place). Returns the paths that point at nothing any more
    (the config files changed under them): those are left out."""
    stale = []
    for path in sorted(overrides, key=lambda p: (p.count("/"), p)):   # a new entry before its fields
        if field_for(path) is None or not _set(raw, path, overrides[path]):
            stale.append(path)
    shifts = raw.get("shifts") or []
    for i, shift in enumerate(shifts):                 # a shift ends where the next one starts
        shift["end"] = shifts[(i + 1) % len(shifts)]["start"]
    return stale


def base_raw(config_dir: str | Path | None = None) -> dict:
    from mes_lab import DEFAULT_CONFIG_DIR

    return _read(Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR)


def effective_raw(overrides: dict[str, Any] | None, config_dir: str | Path | None = None,
                  base: dict | None = None) -> dict:
    raw = copy.deepcopy(base if base is not None else base_raw(config_dir))
    if overrides:
        apply(raw, overrides)
    return raw


def build(overrides: dict[str, Any] | None, config_dir: str | Path | None = None,
          base: dict | None = None) -> PlantModel:
    """The plant model of the config files with the overrides."""
    return build_model(effective_raw(overrides, config_dir, base))


# ---------------------------------------------------------------------- checking a change
def _minutes(value: Any) -> int:
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    hh, mm = str(value).split(":")
    return int(hh) * 60 + int(mm)


def normalize(path: str, value: Any, raw: dict) -> Any:
    """The value as it is stored, or SettingError. `raw` is the config the change goes into (with the
    other changes so far), for the checks that need it (a known customer, a new code that is new)."""
    field = field_for(path)
    if field is None:
        raise SettingError(f"{path} cannot be changed here", f"{path} sa tu meniť nedá")
    segs = path.split("/")
    if field.kind != "entry" and get(raw, "/".join(segs[:-1])) is None and "station_names" not in segs:
        raise SettingError(f"{path}: there is no such thing", f"{path}: také niečo neexistuje")
    kind = field.kind
    if kind == "text":
        text = " ".join(str(value or "").split())
        if not 1 <= len(text) <= 80:
            raise SettingError("A name needs 1 to 80 characters", "Názov potrebuje 1 až 80 znakov")
        return text
    if kind == "ltext":                                # a text on a label: may be empty
        text = " ".join(str(value or "").split())
        if len(text) > 120:
            raise SettingError("At most 120 characters", "Najviac 120 znakov")
        return text
    if kind == "category":
        text = str(value or "").strip().upper().replace(" ", "_")
        if not CATEGORY_RE.match(text):
            raise SettingError("A category is a word in capitals (letters, digits, _), up to 20 characters",
                               "Kategória je slovo veľkými písmenami (písmená, číslice, _), najviac 20 znakov")
        return text
    if kind in ("int", "float", "qty"):
        if kind == "qty" and value in (None, "", 0, "0"):
            return None                                # out of the BOM
        try:
            number = float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            raise SettingError("Not a number", "Nie je to číslo") from None
        if kind in ("int", "qty"):
            if number != int(number):
                raise SettingError("A whole number, please", "Treba celé číslo")
            number = int(number)
        if field.lo is not None and number < field.lo or field.hi is not None and number > field.hi:
            lo, hi = _fmt(field.lo), _fmt(field.hi)
            raise SettingError(f"Allowed from {lo} to {hi}", f"Povolené od {lo} do {hi}")
        return number if kind != "float" else round(number, 3)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "1", "yes", "on"):
            return True
        if str(value).lower() in ("false", "0", "no", "off"):
            return False
        raise SettingError("Yes or no", "Áno alebo nie")
    if kind == "time":
        text = str(value or "").strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", text)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59 or int(m.group(2)) % 15:
            raise SettingError("A time HH:MM on the quarter hour (06:00, 13:45)",
                               "Čas HH:MM na štvrťhodinu (06:00, 13:45)")
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if kind == "choice":
        value = value or None
        if value not in field.choices:
            raise SettingError("Not one of the choices", "Nie je to jedna z možností")
        return value
    if kind == "customer":
        if not any(c.get("code") == value for c in raw.get("customers", [])):
            raise SettingError("Unknown customer", "Neznámy zákazník")
        return value
    if kind == "entry":                                # a new reason code: {name, category}; None takes it back
        section, code = segs
        if value is None:
            return None
        if not CODE_RE.match(code):
            raise SettingError("A code is 3 to 30 capitals, digits or _ and starts with a letter",
                               "Kód je 3 až 30 veľkých písmen, číslic alebo _ a začína písmenom")
        if not isinstance(value, dict):
            raise SettingError("A new code needs a name and a category", "Nový kód potrebuje názov a kategóriu")
        if get(raw, path) is not None and not get(raw, path).get("added"):
            raise SettingError(f"{code} exists already", f"{code} už existuje")
        here = {section: {code: {}}}
        entry = {"name": normalize(f"{section}/{code}/name", value.get("name"), here),
                 "category": normalize(f"{section}/{code}/category", value.get("category"), here),
                 "added": True}                        # made on the web: it may be taken back
        if section == "downtime_reasons" and value.get("crew"):
            entry["crew"] = normalize(f"{section}/{code}/crew", value.get("crew"), here)
        return entry
    raise SettingError(f"{path}: unknown kind {kind}")


def _fmt(x: float | None) -> str:
    if x is None:
        return "?"
    return str(int(x)) if x == int(x) else str(x)


def problems(model: PlantModel) -> list[SettingError]:
    """What does not fit together in a plant with changes (the loader checks the references)."""
    out: list[SettingError] = []
    for mat in model.pcbas():
        pl = mat.planning
        if pl.min_lot and pl.max_lot and pl.min_lot > pl.max_lot:
            out.append(SettingError(f"{mat.code}: the smallest lot ({pl.min_lot}) is larger than the largest ({pl.max_lot})",
                                    f"{mat.code}: najmenšia dávka ({pl.min_lot}) je väčšia ako najväčšia ({pl.max_lot})"))
    starts = [_minutes(s.start) for s in model.shifts]
    shortest, total = 24 * 60, 0
    for i, s in enumerate(model.shifts):
        length = (starts[(i + 1) % len(starts)] - starts[i]) % (24 * 60) or 24 * 60
        shortest, total = min(shortest, length), total + length
        if not SHIFT_H[0] * 60 <= length <= SHIFT_H[1] * 60:
            out.append(SettingError(f"Shift {s.code} would last {length / 60:g} h; a shift lasts {SHIFT_H[0]} to {SHIFT_H[1]} h",
                                    f"Zmena {s.code} by trvala {length / 60:g} h; zmena trvá {SHIFT_H[0]} až {SHIFT_H[1]} h"))
    if total != 24 * 60:
        out.append(SettingError("The shifts must follow each other in their order (A, B, C) round the day",
                                "Zmeny musia ísť po sebe v poradí (A, B, C) dookola cez deň"))
    taken = []
    for b in model.crew.breaks:
        if b.start + b.length > shortest - 30:
            out.append(SettingError(f"The {b.name} break ends {b.start + b.length} min after the start, too late for a shift of {shortest} min",
                                    f"Prestávka {b.name} končí {b.start + b.length} min po začiatku, neskoro pre zmenu dlhú {shortest} min"))
        for other in taken:
            if b.start < other.start + other.length and other.start < b.start + b.length:
                out.append(SettingError(f"The breaks {other.name} and {b.name} overlap",
                                        f"Prestávky {other.name} a {b.name} sa prekrývajú"))
        taken.append(b)
    for t in model.station_types.values():
        for name, m in t.measurements.items():
            if not m.lsl < m.mean < m.usl:
                out.append(SettingError(f"{t.code} {name}: the limits {m.lsl} to {m.usl} must lie around the process mean {m.mean}",
                                        f"{t.code} {name}: hranice {m.lsl} až {m.usl} musia ležať okolo strednej hodnoty procesu {m.mean}"))
    return out


def check(overrides: dict[str, Any], config_dir: str | Path | None = None, base: dict | None = None) -> PlantModel:
    """The plant with the overrides, or SettingError with everything that does not fit."""
    raw = effective_raw(overrides, config_dir, base)
    for area in raw.get("areas", []):              # a station name for a station that is not on the line
        for line in area.get("lines", []):
            for code in (line.get("station_names") or {}):
                if not code.startswith(line["code"] + "-"):
                    raise SettingError(f"{code} is not a station of {line['code']}", f"{code} nie je stanica linky {line['code']}")
    try:
        model = build_model(raw)
    except ModelError as exc:
        raise SettingError("; ".join(exc.problems), "; ".join(exc.problems)) from None
    except ValueError as exc:                     # pydantic
        raise SettingError(str(exc).splitlines()[0]) from None
    found = problems(model)
    if found:
        raise SettingError("; ".join(e.en for e in found), "; ".join(e.sk for e in found))
    for area in raw.get("areas", []):
        for line in area.get("lines", []):
            for code in (line.get("station_names") or {}):
                if code not in model.stations:
                    raise SettingError(f"{code} is not a station of {line['code']}", f"{code} nie je stanica linky {line['code']}")
    return model


def needs_plant(paths) -> bool:
    """Do the machines need any of these changes (the plant builds itself anew)?"""
    return any((f := field_for(p)) is not None and f.plant for p in paths)
