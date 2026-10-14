"""Plain-language explanations shown on the web (guide page, help boxes,
station tooltips). Every text exists in English and Slovak: (en, sk)."""

STATIONS: dict[str, tuple[str, str]] = {
    "LOADER": (
        "Pushes bare circuit-board panels from a magazine onto the line conveyor.",
        "Zo zásobníka posúva holé panely dosiek plošných spojov na dopravník linky."),
    "LASER_PCB": (
        "Burns a 2D code (DMC) into the panel and into every board on it. From now on each board has its own serial number, issued by the MES.",
        "Laserom vypáli do panela aj do každej dosky 2D kód (DMC). Odteraz má každá doska vlastné sériové číslo, ktoré vydal MES."),
    "PASTE_PRINTER": (
        "Prints solder paste through a metal stencil onto the pads where components will sit.",
        "Cez kovovú šablónu natlačí spájkovaciu pastu na plôšky, kam prídu súčiastky."),
    "SPI": (
        "Solder Paste Inspection: a 3D camera measures every paste deposit. A misprinted panel is washed and printed again.",
        "Kontrola pasty (SPI): 3D kamera zmeria každú kôpku pasty. Zle natlačený panel sa umyje a tlačí znova."),
    "PNP_CHIP": (
        "Pick-and-place machine for small parts (resistors, capacitors): thousands of components per hour.",
        "Osádzací automat na drobné súčiastky (rezistory, kondenzátory): tisíce kusov za hodinu."),
    "PNP_IC": (
        "Pick-and-place machine for larger and fine-pitch parts (chips, connectors).",
        "Osádzací automat na väčšie súčiastky s jemnými vývodmi (čipy, konektory)."),
    "REFLOW_OVEN": (
        "Long oven with temperature zones: the paste melts and solders the components. Several panels are inside at once.",
        "Dlhá pec s teplotnými zónami: pasta sa roztaví a prispájkuje súčiastky. Vnútri je naraz viac panelov."),
    "AOI": (
        "Automated Optical Inspection: cameras look for missing, shifted or badly soldered parts. An operator checks every alarm, many are false calls. A bad board gets a bad mark and is repaired later.",
        "Automatická optická kontrola (AOI): kamery hľadajú chýbajúce, posunuté či zle spájkované súčiastky. Každý poplach overí operátor, veľa z nich je falošných. Zlá doska dostane značku a opraví sa neskôr."),
    "UNLOADER": (
        "Takes finished panels off the conveyor into a magazine.",
        "Hotové panely zoberie z dopravníka do zásobníka."),
    "DEPANELER": (
        "A router cuts the panel into single boards. Good boards go to the supermarket shelf, marked ones to repair.",
        "Fréza rozreže panel na jednotlivé dosky. Dobré idú do regálu (supermarketu), označené na opravu."),
    "FA_LOADER": (
        "An operator takes a board from the supermarket and starts a product: the MES checks the board and gives the product its serial number.",
        "Operátor vezme dosku zo supermarketu a začne výrobok: MES dosku skontroluje a pridelí výrobku sériové číslo."),
    "PRESS_FIT": (
        "Presses a connector into the board. The press force is measured; a bad curve means a bent pin.",
        "Zalisuje konektor do dosky. Meria sa lisovacia sila; zlý priebeh znamená ohnutý kolík."),
    "COATER": (
        "Sprays protective lacquer (conformal coating) against moisture and dirt.",
        "Nastrieka ochranný lak proti vlhkosti a nečistotám."),
    "UV_CURE": (
        "UV tunnel that hardens the lacquer in about a minute and a half.",
        "UV tunel, v ktorom lak stvrdne asi za minútu a pol."),
    "COATING_AOI": (
        "Camera under UV light checks that the lacquer is everywhere it should be, and nowhere else.",
        "Kamera pod UV svetlom skontroluje, či je lak všade, kde má byť, a nikde inde."),
    "POTTING": (
        "Fills the housing with two-component resin that protects the electronics (LED drivers).",
        "Zaleje teleso dvojzložkovou živicou, ktorá chráni elektroniku (budiče LED)."),
    "CURING_OVEN": (
        "Oven where the resin hardens for about 40 minutes; up to 90 units inside.",
        "Pec, v ktorej živica tvrdne asi 40 minút; vnútri je až 90 kusov."),
    "ICT": (
        "In-Circuit Test: spring needles touch test points and measure every component electrically.",
        "Test v obvode (ICT): pružné ihly sa dotknú testovacích bodov a elektricky zmerajú každú súčiastku."),
    "SCREWDRIVER": (
        "Puts the board into its housing and drives the screws; torque and angle of every screw are logged.",
        "Vloží dosku do krytu a zaskrutkuje skrutky; zaznamená moment a uhol každej skrutky."),
    "FLASHER": (
        "Downloads the software into the unit's microcontroller.",
        "Nahrá do mikroprocesora výrobku softvér."),
    "EOL_TESTER": (
        "End-of-Line test: the unit is switched on and tested as the car will use it.",
        "Záverečný test (EOL): výrobok sa zapne a otestuje tak, ako ho bude používať auto."),
    "LASER_UNIT": (
        "Marks the product's serial number and 2D code on the housing.",
        "Vyznačí na kryt výrobku jeho sériové číslo a 2D kód."),
    "FINAL_INSPECTION": (
        "Last look by a person: scratches, resin overflow, damaged edges.",
        "Posledná kontrola človekom: škrabance, pretečená živica, poškodené hrany."),
    "PACK_SCAN": (
        "Scans every unit into a KLT box (a standard returnable plastic box). The MES decides which box and when it is full.",
        "Naskenuje každý výrobok do KLT boxu (štandardná vratná plastová prepravka). MES určí, do ktorého boxu a kedy je plný."),
    "BOX_LABEL": (
        "Prints the box label (VDA 4902, the German automotive standard). It works only when a box is full, about once in two minutes, so most of the time it waits.",
        "Vytlačí štítok na box (VDA 4902, nemecký automobilový štandard). Pracuje, len keď je box plný, zhruba raz za dve minúty, preto väčšinu času čaká."),
    "PALLETIZER": (
        "Stacks full boxes on a pallet; a full pallet gets an SSCC number and goes to the warehouse. Like the label printer it gets one box every few minutes and waits in between.",
        "Ukladá plné boxy na paletu; plná paleta dostane číslo SSCC a ide do skladu. Podobne ako tlačiareň štítkov dostane box raz za pár minút a medzitým čaká."),
    "REPAIR_BENCH": (
        "A technician analyses the defect and repairs it, or decides the unit is scrap. The MES then says where the unit goes back.",
        "Technik chybu analyzuje a opraví, alebo rozhodne, že kus je šrot. MES potom určí, kam sa kus vráti."),
}

STATES: dict[str, tuple[str, str]] = {
    "RUNNING": ("Working on a part.", "Pracuje na kuse."),
    "STARVED": ("Waiting for a part: the station before it is slower or stopped. Normal for every station except the slowest one, so it is shown in light grey, not as a problem.",
                "Čaká na kus: stanica pred ňou je pomalšia alebo stojí. Normálne pre každú stanicu okrem najpomalšej, preto je svetlosivá, nie ako problém."),
    "BLOCKED": ("Finished but cannot pass the part on: the conveyor to the next station is full, because the next station is slower, stopped or changing over. Normal in front of the slowest station; dark grey.",
                "Má hotovo, ale nemá kam kus posunúť: dopravník k ďalšej stanici je plný, lebo ďalšia stanica je pomalšia, stojí alebo sa prestavuje. Pred najpomalšou stanicou normálne; tmavosivá."),
    "DOWN": ("Broken down; a technician is repairing it. The reason is shown.",
             "Porucha; technik ju opravuje. Zobrazí sa dôvod."),
    "MINOR_STOP": ("A short hiccup (part not picked up, small jam) cleared by the operator.",
                   "Krátky výpadok (súčiastka sa nenabrala, malé zaseknutie), ktorý odstráni operátor."),
    "CHANGEOVER": ("Switching to another product, or on SMT to the other side of the same board (double-sided boards: bottom side first, then top side, still the same order). Either way: new stencil, programs and parts.",
                   "Prestavba na iný výrobok, alebo na SMT na druhú stranu tej istej dosky (obojstranné dosky: najprv spodná, potom vrchná strana, stále tá istá zákazka). V oboch prípadoch nová šablóna, programy a súčiastky."),
    "MAINTENANCE": ("Planned service after a number of operating hours.", "Plánovaná údržba po určitom počte prevádzkových hodín."),
    "NO_ORDER": ("Nothing to make: the line has no job.", "Nie je čo vyrábať: linka nemá prácu."),
    "NO_MATERIAL": ("The line waits for boards from SMT.", "Linka čaká na dosky zo SMT."),
    "OFF": ("Out of operation: the live plant runs a scenario of the tuning game without this line (Tuning page).",
            "Mimo prevádzky: živý závod beží podľa scenára z ladenia bez tejto linky (stránka Tuning)."),
}

PRODUCTS: dict[str, tuple[str, str]] = {
    "BCM-200": ("Body control module: switches lights, wipers, locks. Double-sided board, housing with screws, software, end-of-line test.",
                "Riadiaca jednotka karosérie: zapína svetlá, stierače, zámky. Obojstranná doska, kryt so skrutkami, softvér, záverečný test."),
    "LED-DRV-40": ("Driver for an LED headlamp. The electronics are sealed in resin (potting).",
                   "Budič LED svetlometu. Elektronika je zaliata živicou."),
    "BMS-12S": ("Battery management board for 12 cells: watches voltages and temperatures of a battery pack.",
                "Doska správy batérie pre 12 článkov: stráži napätia a teploty batérie."),
    "PDC-8": ("Park distance control unit for 8 ultrasonic sensors.", "Jednotka parkovacích senzorov pre 8 ultrazvukových snímačov."),
}

UNIT_STATUS: dict[str, tuple[str, str]] = {
    "IN_PROCESS": ("On its way through a line.", "Je na ceste linkou."),
    "OK": ("Good and waiting for the next step (board on the shelf, unit before packing).",
           "Dobrý a čaká na ďalší krok (doska v regáli, výrobok pred balením)."),
    "NOK": ("Has an open defect; waits for repair.", "Má otvorenú chybu; čaká na opravu."),
    "QUARANTINE": ("Stopped by the MES and put aside until somebody decides.", "MES ho zastavil a odložil, kým niekto nerozhodne."),
    "SCRAPPED": ("Thrown away.", "Vyhodený (šrot)."),
    "CONSUMED": ("A board built into a product.", "Doska zabudovaná do výrobku."),
    "PACKED": ("In a KLT box.", "V KLT boxe."),
    "SHIPPED": ("Left the plant on a truck (delivery note ASN).", "Odišlo z továrne na kamióne (dodací list ASN)."),
    "PALLETIZED": ("A box on a pallet.", "Box na palete."),
    "OPEN": ("A box or pallet still being filled.", "Box alebo paleta, ktorá sa ešte plní."),
    "CLOSED": ("A full box or pallet.", "Plný box alebo paleta."),
    "DONE": ("A panel after cutting into boards.", "Panel po rozrezaní na dosky."),
    "LOST": ("Was on the shop floor when the simulation restarted.", "Bol v dielni, keď sa simulácia reštartovala."),
}

GLOSSARY: list[tuple[str, str, str]] = [
    ("MES", "Manufacturing Execution System: the software between the office (orders, ERP) and the machines. It tells lines what to make, checks every step and remembers the history of every product.",
     "Systém riadenia výroby: softvér medzi kanceláriou (zákazky, ERP) a strojmi. Hovorí linkám, čo vyrábať, kontroluje každý krok a pamätá si históriu každého výrobku."),
    ("ISA-95", "International standard that describes how a plant and its systems are structured (enterprise > site > area > line > station) and what an MES does.",
     "Medzinárodná norma, ktorá popisuje štruktúru závodu a jeho systémov (podnik > závod > oblasť > linka > stanica) a čo robí MES."),
    ("SMT", "Surface Mount Technology: soldering components onto the surface of a circuit board.", "Povrchová montáž: spájkovanie súčiastok na povrch dosky plošných spojov."),
    ("PCB / PCBA", "Printed Circuit Board (bare) / PCB Assembly (with components).", "Doska plošných spojov (holá) / osadená doska (so súčiastkami)."),
    ("Panel", "Several boards made together on one sheet; cut apart at the end of SMT.", "Viac dosiek vyrobených spolu na jednom plaku; na konci SMT sa rozrežú."),
    ("Final assembly (FA)", "The second part of the plant: the board gets a connector, lacquer, a housing, software and is tested.",
     "Druhá časť závodu: doska dostane konektor, lak, kryt, softvér a otestuje sa."),
    ("Supermarket", "A shelf of finished boards between SMT and final assembly. SMT refills it, final assembly takes from it.",
     "Regál hotových dosiek medzi SMT a finálnou montážou. SMT ho dopĺňa, montáž z neho berie."),
    ("Safety stock", "The minimum the supermarket should hold. Below it, the planner orders more boards from SMT.",
     "Minimum, ktoré má byť v supermarkete. Pod ním plánovač objedná ďalšie dosky zo SMT."),
    ("Work order", "Instruction to make a quantity of one material, e.g. 720 × BCM-200.", "Pokyn vyrobiť určité množstvo jedného materiálu, napr. 720 × BCM-200."),
    ("Job", "One step of a work order on one line, e.g. the bottom side of a board on SMT-02.", "Jeden krok zákazky na jednej linke, napr. spodná strana dosky na SMT-02."),
    ("Routing", "The list of steps a product must go through, in order.", "Zoznam krokov, ktorými musí výrobok prejsť, v poradí."),
    ("Cycle time", "How long a station needs for one piece.", "Ako dlho potrebuje stanica na jeden kus."),
    ("Bottleneck", "The slowest station of a line; it decides how much the whole line makes. It is the one that is almost always green.",
     "Najpomalšia stanica linky; určuje, koľko vyrobí celá linka. Je to tá, ktorá svieti skoro stále na zeleno."),
    ("Buffer", "Conveyor places between stations; small squares on the line page.", "Miesta na dopravníku medzi stanicami; malé štvorčeky na stránke linky."),
    ("WIP", "Work in progress: pieces that are on the line right now.", "Rozpracovaná výroba: kusy, ktoré sú práve na linke."),
    ("Changeover", "Switching a line from one product to another.", "Prestavba linky z jedného výrobku na iný."),
    ("MTBF / MTTR", "Mean time between failures / mean time to repair.", "Priemerný čas medzi poruchami / priemerný čas opravy."),
    ("Serial number / DMC", "Unique number of every piece; DMC is the 2D code that carries it.", "Jedinečné číslo každého kusa; DMC je 2D kód, v ktorom je zapísané."),
    ("Check-in / check-out", "Before working on a piece a station asks the MES whether it may (check-in); afterwards it reports the result (check-out).",
     "Pred prácou na kuse sa stanica opýta MES, či smie (check-in); potom nahlási výsledok (check-out)."),
    ("Interlock", "A station that must get the MES's permission before working on a piece.", "Stanica, ktorá musí dostať od MES povolenie, kým začne pracovať na kuse."),
    ("Genealogy", "Family tree of a product: which panel and board it came from, which box and pallet it went into.",
     "Rodokmeň výrobku: z ktorého panela a dosky vznikol, do ktorého boxu a na ktorú paletu išiel."),
    ("Traceability", "Being able to find out, for any serial number, everything that happened to it. Automotive customers (IATF 16949) require it.",
     "Schopnosť zistiť o každom sériovom čísle všetko, čo sa s ním dialo. Vyžadujú to automobilky (IATF 16949)."),
    ("False call", "An inspection machine raises an alarm, the operator checks it and the part is fine.",
     "Kontrolný stroj vyhlási poplach, operátor ho overí a kus je v poriadku."),
    ("Bad mark", "A board found bad on a panel is marked; the following machines skip it.", "Doska, ktorá je na paneli zlá, dostane značku; ďalšie stroje ju preskočia."),
    ("Hold", "A block put on a serial, a work order or a time window on a station. Everything affected is stopped at its next check-in.",
     "Blokácia na sériové číslo, zákazku alebo časové okno na stanici. Všetko, čoho sa týka, sa zastaví pri najbližšom check-ine."),
    ("Quarantine", "The shelf where stopped pieces wait until somebody releases or scraps them.", "Regál, kde zastavené kusy čakajú, kým ich niekto uvoľní alebo zošrotuje."),
    ("Containment", "Finding and stopping every piece that might have the same problem.", "Vyhľadanie a zastavenie všetkých kusov, ktoré môžu mať rovnaký problém."),
    ("FPY", "First Pass Yield: share of pieces that pass without any repair.", "Výťažnosť na prvý pokus: podiel kusov, ktoré prejdú bez opravy."),
    ("RTY", "Rolled Throughput Yield: the FPY of every step of a product multiplied together; the share that goes from the bare board to the finished unit without a single repair.",
     "Súhrnná výťažnosť: FPY všetkých krokov výrobku vynásobené spolu; podiel kusov, ktoré prejdú od holej dosky po hotový výrobok bez jedinej opravy."),
    ("OEE", "Overall Equipment Effectiveness = availability × performance × quality: how much of the planned time a line made good pieces at its ideal speed. Around 85 % is world class.",
     "Celková efektívnosť zariadení = dostupnosť × výkon × kvalita: koľko z plánovaného času linka vyrábala dobré kusy ideálnou rýchlosťou. Okolo 85 % je svetová trieda."),
    ("MTBF / MTTR", "Mean time between failures (running hours per breakdown) / mean time to repair (minutes per breakdown, waiting for the technician included).",
     "Priemerný čas medzi poruchami (hodiny behu na poruchu) / priemerný čas opravy (minúty na poruchu vrátane čakania na technika)."),
    ("Automation degree", "The share of the work content of the products that machines do; the rest is done by hand (loading, visual inspection, packing, repair, checking the calls of an inspection).",
     "Podiel obsahu práce na výrobkoch, ktorý robia stroje; zvyšok sa robí ručne (nakladanie, vizuálna kontrola, balenie, opravy, overovanie poplachov kontrol)."),
    ("Kanban", "Material pulled by consumption: when a bin at the line runs empty, its card orders a full one. Two-bin kanban: one bin in use, one full in reserve (here the robots bring them).",
     "Materiál ťahaný spotrebou: keď sa zásobník pri linke vyprázdni, jeho karta objedná plný. Dvojzásobníkový kanban: jeden zásobník sa používa, plný čaká v zálohe (tu ich vozia roboty)."),
    ("Constraint", "The step of a line with the most work (the bottleneck): the line cannot make more than it.",
     "Krok linky s najviac prácou (úzke hrdlo): linka nevyrobí viac ako on."),
    ("KLT", "Standard returnable plastic box used by German car makers.", "Štandardná vratná plastová prepravka nemeckých automobiliek."),
    ("SSCC", "Serial Shipping Container Code: 18-digit number of a pallet (GS1 standard).", "Sériové číslo prepravnej jednotky: 18-ciferné číslo palety (norma GS1)."),
    ("Edge gateway", "A small computer next to the machines that collects their signals and sends summaries to the MES.",
     "Malý počítač pri strojoch, ktorý zbiera ich signály a posiela súhrny do MES."),
    ("AMR", "Autonomous mobile robot: a driverless cart that finds its way along the aisles with laser scanners. Here they carry the pieces between the machines, the repair centre and the quarantine cage, and the bins of parts and the empty boxes to the lines.",
     "Autonómny mobilný robot: vozík bez vodiča, ktorý si laserovými skenermi nájde cestu uličkami. Tu vozia kusy medzi strojmi, opravovňou a karanténnou klietkou a zásobníky dielov a prázdne boxy k linkám."),
    ("Fleet manager", "The software that gives the robots their transports: the nearest free robot takes the oldest waiting tote, and one with a low battery charges first.",
     "Softvér, ktorý robotom prideľuje prevozy: najbližší voľný robot vezme najstaršiu čakajúcu prepravku a robot so slabou batériou sa najprv nabije."),
    ("NOK tote", "The red box at the exit of a cell for rejected pieces (NOK = not OK). Nothing leaves a line for repair any other way.",
     "Červená prepravka na výstupe bunky na vyradené kusy (NOK = nie v poriadku). Na opravu neodchádza z linky nič inak."),
    ("Pass-through rack", "A rack built into a wall: filled from one side (here by a robot from the aisle), emptied from the other (by the repair people inside).",
     "Regál zabudovaný v stene: plní sa z jednej strany (tu robot z uličky) a vyberá z druhej (opravári vnútri)."),
    ("Andon", "A signal that asks for help at a machine; the andon board lists the open calls. Here a technician is called at every breakdown, and anybody signed in can call a person from the Floor page.",
     "Signál, ktorým stroj volá o pomoc; tabuľa andon ukazuje otvorené volania. Tu sa technik volá pri každej poruche a ktokoľvek prihlásený môže privolať pracovníka zo stránky Floor."),
]
