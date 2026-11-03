"""Linear bar codes for the labels: Code 39 (the box label, VDA 4902) and Code 128 / GS1-128 (the SSCC of a
pallet). A code is a list of module widths, bar first: [bar, space, bar, space, ...] in narrow modules."""

from __future__ import annotations

# Code 39: 9 elements per character (bar, space, ... bar), n = narrow, w = wide
CODE39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn", "4": "nnnwwnnnw",
    "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw", "8": "wnnwnnwnn", "9": "nnwwnnwnn",
    "A": "wnnnnwnnw", "B": "nnwnnwnnw", "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn",
    "F": "nnwnwwnnn", "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn",
    "K": "wnnnnnnww", "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww", "O": "wnnnwnnwn",
    "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn", "S": "nnwnnnwwn", "T": "nnnnwnwwn",
    "U": "wwnnnnnnw", "V": "nwwnnnnnw", "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn",
    "Z": "nwwnwnnnn", "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "$": "nwnwnwnnn",
    "/": "nwnwnnnwn", "+": "nwnnnwnwn", "%": "nnnwnwnwn", "*": "nwnnwnwnn",
}
WIDE = 3                      # wide : narrow


def code39(data: str) -> list[int]:
    """Start and stop `*`, a narrow gap between characters. Lower case is printed as upper case."""
    data = data.upper()
    bad = sorted({c for c in data if c not in CODE39 or c == "*"})
    if bad:
        raise ValueError(f"Code 39 cannot carry {''.join(bad)!r}")
    modules: list[int] = []
    for i, ch in enumerate(f"*{data}*"):
        if i:
            modules.append(1)                                     # the gap between characters (a space)
        modules += [WIDE if e == "w" else 1 for e in CODE39[ch]]
    return modules


# Code 128: the 107 symbols as widths of bar, space, bar, space, bar, space (the stop has a 7th bar)
CODE128 = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 "
    "122231 113222 123122 123221 223211 221132 221231 213212 223112 312131 311222 321122 321221 312212 "
    "322112 322211 212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 231113 231311 "
    "112133 112331 132131 113123 113321 133121 313121 211331 231131 213113 213311 213131 311123 311321 "
    "331121 312113 312311 332111 314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 111242 121142 121241 114212 "
    "124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 114311 411113 "
    "411311 113141 114131 311141 411131 211412 211214 211232 2331112"
).split()
START_B, START_C, FNC1, STOP = 104, 105, 102, 106


def _values128(data: str, gs1: bool) -> list[int]:
    """Symbol values: code set C (two digits a symbol) when the data are an even number of digits, B otherwise."""
    if data.isdigit() and len(data) % 2 == 0:
        values = [START_C] + [int(data[k:k + 2]) for k in range(0, len(data), 2)]
    else:
        bad = sorted({c for c in data if not 32 <= ord(c) <= 126})
        if bad:
            raise ValueError(f"Code 128 cannot carry {''.join(bad)!r}")
        values = [START_B] + [ord(c) - 32 for c in data]
    if gs1:                                          # GS1-128: FNC1 right after the start
        values.insert(1, FNC1)
    return values


def code128(data: str, gs1: bool = False) -> list[int]:
    """Code 128 (with gs1=True: GS1-128, the data are application identifiers and their values, e.g.
    "00" + SSCC). Adds the check symbol and the stop."""
    if not data:
        raise ValueError("Code 128 needs data")
    values = _values128(data, gs1)
    check = (values[0] + sum(i * v for i, v in enumerate(values[1:], start=1))) % 103
    modules: list[int] = []
    for v in values + [check, STOP]:
        modules += [int(c) for c in CODE128[v]]
    return modules


def encode(kind: str, data: str, gs1: bool = False) -> list[int]:
    if kind == "code39":
        return code39(data)
    if kind == "code128":
        return code128(data, gs1=gs1)
    raise ValueError(f"unknown bar code {kind}")


def width(modules: list[int]) -> int:
    return sum(modules)
