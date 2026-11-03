"""Training scenarios: a real fault in the live plant, and the trainee has to find it and contain it with the
MES (phase 6). The plant (sim) makes the fault happen and the defects it causes stay latent in the pieces,
as they would; the MES knows which pieces are suspect (the truth) and grades the containment at the end:
what the holds caught, what reached the customer, what was stopped for nothing, how long it took.

    BAD_LOT       a supplier lot of a chip capacitor is faulty: the boards that got it fail at ICT and EOL (some
                  slip through). Find the lot, put it on hold (the MES has its reels taken off the machines),
                  contain what was made with it.
    AOI_STORM     the AOI of an SMT line calls almost every board after a program change; the operators drown in
                  verifications and wave real defects through. Get the AOI fixed (call maintenance or IT to it),
                  contain what it passed during the storm.
    OVEN_FAILURE  a heater zone of a reflow oven fails with panels inside: they got a wrong profile (cold
                  joints). Contain the panels that were in the oven.
    COMPLAINT     a customer reports a unit that failed in the field (a loose screw). The calibration lab finds
                  the screwdriver read the torque too high in a time window. Contain what is still in the plant,
                  name the shipments to report to the customer.
"""

from __future__ import annotations

BAD_LOT_PARTS = ("CAP-0402-100N", "RES-0402-10K")        # on every board: the lot reaches many of them

FAULTS = {
    "BAD_LOT": {"rate": 0.25, "defect": "COMPONENT_FAULT"},             # share of the boards a bad reel spoils
    "AOI_STORM": {"false_calls": 0.45, "miss": 0.35},                   # false call rate; real calls waved through
    "OVEN_FAILURE": {"reason": "HEATER_ZONE_FAULT", "defect": "COLD_JOINT", "rate": 0.8},
}
MAX_HOURS = 8.0              # a fault nobody fixes ends by itself after this many plant hours (the live plant goes on)

# code -> (name EN, name SK, what the trainee is told EN, SK, hints EN, SK)
SCENARIOS: dict[str, dict] = {
    "BAD_LOT": {
        "name": ("A bad lot of a supplier", "Zlá šarža dodávateľa"),
        "brief": ("At {start} the supplier of {part} calls: one of the lots delivered lately has cracked parts, they do "
                  "not know which one yet. The boards wait in the supermarket before final assembly, so its testers "
                  "will show it (COMPONENT_FAULT) only later. Find the lot, stop it and contain everything that may "
                  "carry it.",
                  "O {start} volá dodávateľ súčiastky {part}: jedna z nedávno dodaných šarží má prasknuté kusy, zatiaľ "
                  "nevedia ktorá. Dosky pred finálnou montážou čakajú v supermarkete, takže jej testery to ukážu "
                  "(COMPONENT_FAULT) až neskôr. Nájdi šaržu, zastav ju a ohranič všetko, čo ju môže niesť."),
        "hints": (["Material: which lots of the part are on the machines now; Quality → Pareto: which defect grows.",
                   "Open a failed board on Trace: the panel Components lists its reels and supplier lots.",
                   "Material → Where did a supplier lot go? shows everything made with a lot.",
                   "Holds → kind MATERIAL_LOT: the MES stops the pieces, refuses the reels at goods-in and has the "
                   "reels of the lot taken off the machines."],
                  ["Material: ktoré šarže súčiastky sú teraz na strojoch; Quality → Pareto: ktorá chyba rastie.",
                   "Otvor zlyhanú dosku na Trace: panel Components ukáže jej cievky a šarže dodávateľa.",
                   "Material → Kam išla šarža dodávateľa? ukáže všetko, čo sa zo šarže vyrobilo.",
                   "Holds → druh MATERIAL_LOT: MES zastaví kusy, odmietne cievky pri príjme a dá cievky šarže "
                   "zložiť zo strojov."]),
    },
    "AOI_STORM": {
        "name": ("False call storm at an AOI", "Búrka falošných poplachov AOI"),
        "brief": ("Since {start} the operators of {line} complain that the AOI flags almost every board. They are "
                  "verifying calls all the time, the line is slow, and in the hurry real defects may be waved "
                  "through. Get the AOI fixed and contain what it passed in the meantime.",
                  "Od {start} sa operátori linky {line} sťažujú, že AOI hlási takmer každú dosku. Neustále "
                  "overujú hlásenia, linka je pomalá a v zhone môžu pustiť aj skutočné chyby. Zariaď opravu AOI "
                  "a ohranič, čo medzitým prepustila."),
        "hints": (["KPI or the line page: false calls of the AOI, and its state (verifying).",
                   "Floor → click the AOI → Call a person: maintenance or IT re-teaches the program.",
                   "Holds → kind STATION_WINDOW on the AOI from the start of the storm to the fix: those boards are "
                   "inspected again."],
                  ["KPI alebo stránka linky: falošné hlásenia AOI a jej stav (overovanie).",
                   "Floor → klik na AOI → Privolať človeka: údržba alebo IT program nanovo naučí.",
                   "Holds → druh STATION_WINDOW na AOI od začiatku búrky po opravu: tie dosky sa skontrolujú znova."]),
    },
    "OVEN_FAILURE": {
        "name": ("A reflow oven fails with panels inside", "Porucha pece s panelmi vo vnútri"),
        "brief": ("At {start} a heater zone of the reflow oven of {line} failed (HEATER_ZONE_FAULT). The belt stood "
                  "with panels inside: they did not get the right profile. Maintenance repairs the oven; your job "
                  "is the panels.",
                  "O {start} zlyhala ohrevná zóna pretavovacej pece linky {line} (HEATER_ZONE_FAULT). Pás stál a "
                  "vo vnútri boli panely: nedostali správny teplotný profil. Pec opraví údržba; tvoja úloha sú "
                  "panely."),
        "hints": (["Andon or the line page: when exactly the oven went down.",
                   "The oven holds several panels at a time; they leave it only after the repair.",
                   "Holds → kind STATION_WINDOW on the oven, a window around the failure: the preview shows how many."],
                  ["Andon alebo stránka linky: kedy presne pec stála.",
                   "Pec drží naraz niekoľko panelov; vyjdú z nej až po oprave.",
                   "Holds → druh STATION_WINDOW na peci, okno okolo poruchy: náhľad ukáže koľko."]),
    },
    "COMPLAINT": {
        "name": ("A customer complaint", "Reklamácia zákazníka"),
        "brief": ("{customer} reports unit {serial} (delivered with {asn}): failed in the vehicle, a screw of the "
                  "housing was loose. The calibration lab checked the screwdriver it passed: {station} read the "
                  "torque about 8 % too high from {window_from} to {window_to}, when it was recalibrated. Contain "
                  "what is still in the plant and name the shipments (ASN) the customer must be told about.",
                  "{customer} hlási kus {serial} (dodaný s {asn}): zlyhal vo vozidle, skrutka krytu bola voľná. "
                  "Kalibračné laboratórium preverilo skrutkovačku, cez ktorú prešiel: {station} ukazovala moment "
                  "asi o 8 % vyšší od {window_from} do {window_to}, keď ju prekalibrovali. Ohranič, čo je ešte "
                  "v závode, a vymenuj zásielky (ASN), o ktorých treba zákazníkovi dať vedieť."),
        "hints": (["Trace → the serial: where it was screwed and when.",
                   "Holds → kind STATION_WINDOW on that screwdriver with the window of the calibration finding.",
                   "The units of the window that already left: Trace shows the delivery note (ASN) of each."],
                  ["Trace → sériové číslo: kde a kedy ho skrutkovali.",
                   "Holds → druh STATION_WINDOW na tej skrutkovačke s oknom z nálezu kalibrácie.",
                   "Kusy z okna, ktoré už odišli: Trace pri každom ukáže dodací list (ASN)."]),
    },
}
