/* The "Plant floor" page: the live plan plus the panel on the right. */
(() => {
  const { esc, n, pill, label, STATES, duration, since } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const canvas = document.getElementById("floor-canvas");
  const side = document.getElementById("side-main");
  const tip = document.getElementById("floor-tip");
  const speedNote = document.getElementById("floor-speed");

  let overview = null;        // /api/v1/plant/overview
  let lineData = null;        // /api/v1/lines/<code> of the selected line
  let lineTimer = null;
  let selected = null;        // {line, station} | {person} | {piece, sn} | {building, pallet} | {truck} | {sn} | {robot}
  let view = null;
  const serials = new Map();  // serial number -> what the MES says about it
  let shipments = { at: 0, rows: [] };
  const asns = new Map();     // delivery note -> its pallets, from the MES

  const PHASE = {
    idle: ["empty", "prázdna"],
    in: ["a piece waits at the entrance (MES check-in, setup)", "kus čaká pri vstupe (check-in v MES, nastavenie)"],
    work: ["working on the piece", "pracuje na kuse"],
    verify: ["operator checks the machine's calls", "operátor overuje poplachy stroja"],
    pass: ["the piece rides through, nothing to do here", "kus prejde, tu nie je čo robiť"],
    out: ["done, waiting to move on", "hotovo, čaká na posun ďalej"],
  };

  // ------------------------------------------------------------------ legend
  function legend() {
    document.getElementById("legend").innerHTML = STATES.map((s) =>
      `<span class="st-${s}" title="${esc(MES.tr((window.MES_TEXT || {}).states?.[s]))}"><i></i>${label(s)}</span>`).join("");
    const L = Floor.layout;
    if (!L) return;
    document.getElementById("products-legend").innerHTML = Object.keys(L.products).map((p) => {
      const st = FloorArt.style(p);
      return `<span title="${esc(L.products[p].name)}"><i style="background:${st.pcb};border:1px solid ${st.edge}"></i>${esc(p)}</span>`;
    }).join("");
    document.getElementById("people-legend").innerHTML = Object.values(FloorPeople.ROLE).map((r) =>
      `<span><i style="background:${r.color};border-radius:50%"></i>${esc(T(...r.name))}</span>`).join("")
      + `<span><i style="background:#eef1f5;border:1px solid #7d8799;border-radius:3px;width:14px"></i>${T("AMR robot", "AMR robot")}</span>`
      + `<span><i style="background:#d64036;border-radius:2px"></i>${T("NOK tote", "NOK prepravka")}</span>`;
  }

  // ------------------------------------------------------------------ data
  function lineRows() {
    return overview ? overview.areas.flatMap((a) => a.lines.map((l) => ({ ...l, area: a }))) : [];
  }
  const lineOf = (code) => lineRows().find((l) => l.code === code);

  /* the people on site now, from the plant's own head count */
  function crewKpi(kpi) {
    const f = Floor.floor;
    if (!f || !f.crew) return "";
    const s = f.crew.shifts[f.crew.now];
    return kpi(T(`People on site · shift ${f.crew.now}`, `Ľudia v závode · zmena ${f.crew.now}`), n(s.on_site),
      `${T("of which on a break", "z toho na prestávke")} ${s.break}`);
  }

  function kpis() {
    if (!overview) return;
    const k = overview.kpis;
    const repair = overview.repair || {};
    const kpi = (labelText, value, unit = "", alert = false) => `<div class="kpi${alert ? " alert" : ""}"><div class="label">${esc(labelText)}</div>
      <div class="value">${value}${unit ? `<small>${esc(unit)}</small>` : ""}</div></div>`;
    document.getElementById("kpis").innerHTML = [
      kpi(T("Final assembly · this shift", "Finálna montáž · táto zmena"), n(k.fa_output_shift), T("units", "ks")),
      kpi(T("Packed · this shift", "Zabalené · táto zmena"), n(k.packed_shift), T("units", "ks")),
      kpi(T("Stations down", "Stanice s poruchou"), n(k.down_stations), "", k.down_stations > 0),
      kpi(T("Open work orders", "Otvorené zákazky"), n(k.open_orders)),
      kpi(T("In repair", "V oprave"), n((repair.queue || 0) + (repair.in_work || 0)), T("units", "ks")),
      crewKpi(kpi),
    ].join("");
  }

  // ------------------------------------------------------------------ serial numbers
  const KIND = { P: ["panel", "panel"], B: ["board", "doska"], U: ["product", "výrobok"], K: ["KLT box", "KLT box"], L: ["pallet", "paleta"], G: ["magazine of bare panels", "zásobník holých panelov"],
    N: ["bin of parts", "zásobník dielov"], S: ["stack of empty boxes", "stoh prázdnych boxov"] };
  const kindName = (d) => T(...(KIND[d.k] || ["piece", "kus"]));

  async function loadSerial(sn) {
    const hit = serials.get(sn);
    if (hit && Date.now() - hit.at < 4000) return;
    try {
      const r = await fetch(`/api/v1/trace/units/${encodeURIComponent(sn)}`, { cache: "no-store" });
      serials.set(sn, { at: Date.now(), data: r.ok ? await r.json() : null, missing: !r.ok });
    } catch (e) { serials.set(sn, { at: Date.now(), data: null, missing: true }); }
    if (selected && (selected.sn === sn || selected.pallet === sn)) renderSide();
  }

  const chipSn = (sn) => `<button class="chip" type="button" data-sn="${esc(sn)}">${esc(sn)}</button>`;

  /* the life of one serial number, and what is inside it: a KLT box lists its units, a pallet its boxes */
  function serialPanel(sn, extra = "") {
    loadSerial(sn);
    const got = serials.get(sn);
    const head = `<div class="what"><b class="mono">${esc(sn)}</b></div>`;
    if (!got) return head + `<p class="muted">${T("Asking the MES…", "Pýtam sa MES…")}</p>` + extra;
    if (got.missing || !got.data) return head + `<p class="muted">${T("The MES does not know this number yet.", "MES toto číslo ešte nepozná.")}</p>` + extra;
    const u = got.data.unit;
    const contents = u.kind === "PANEL" ? got.data.down.children : (u.kind === "KLT" || u.kind === "PALLET" ? got.data.up.children : got.data.up.children);
    const what = { PANEL: ["boards of this panel", "dosky tohto panela"], KLT: ["units in this box", "výrobky v tomto boxe"], PALLET: ["boxes on this pallet", "boxy na tejto palete"],
      UNIT: ["built from board", "postavený z dosky"], BOARD: ["cut from panel", "vyrezaná z panela"] }[u.kind] || ["", ""];
    const list = (contents || []).filter((c) => c.link !== "ASSEMBLY" || u.kind === "UNIT").slice(0, 60);
    const links = u.kind === "UNIT" || u.kind === "BOARD" ? (got.data.down.children || []).slice(0, 3) : [];
    return `${head}<div class="muted">${esc(u.kind)} · ${esc(u.material)} ${MES.upill(u.status)} ${u.location ? "· " + esc(u.location) : ""}</div>
      ${u.work_order ? `<div class="muted">${T("Work order", "Zákazka")} ${esc(u.work_order)}</div>` : ""}
      ${list.length && what[0] ? `<h3 style="margin-top:10px">${esc(T(...what))} (${list.length}${(contents || []).length > 60 ? "+" : ""})</h3>
        <div class="chips">${list.map((c) => chipSn(c.serial)).join("")}</div>` : ""}
      ${links.length ? `<h3 style="margin-top:10px">${T("Went into", "Išlo do")}</h3><div class="chips">${links.map((c) => chipSn(c.serial)).join("")}</div>` : ""}
      ${extra}
      <div class="links"><a class="btn small primary" href="/trace?q=${encodeURIComponent(sn)}">${T("Open the full history", "Otvoriť celú históriu")}</a></div>`;
  }

  /* where a piece is, in words */
  function whereIs(loc) {
    switch (loc.t) {
      case "slot": return `${T("in the machine", "v stroji")} ${loc.code}`;
      case "ride": return `${T("on the belt", "na páse")} ${loc.route}`;
      case "oven": return `${T("inside", "vnútri")} ${loc.code}`;
      case "box":
        if (loc.place.startsWith("gap:")) return `${T("in the NOK tote", "v NOK prepravke")} ${FloorRobots.placeName(loc.place)} · ${T("waits for a robot", "čaká na robota")}`;
        if (loc.place === "rpr:out") return T("repaired, in the outbound rack · waits for a robot", "opravený, vo výstupnom regáli · čaká na robota");
        if (loc.place === "gi") return T("in the dispatch rack of the goods-in warehouse · waits for a robot", "vo výdajnom regáli skladu materiálu · čaká na robota");
        if (loc.place.startsWith("drop:")) return T("empty, on the return shelf of the rack · a robot takes it back to the warehouse", "prázdny, na vratnej polici regálu · robot ho odvezie späť do skladu");
        return T("released from quarantine · waits for a robot at the hatch", "uvoľnený z karantény · čaká na robota pri okienku");
      case "amr": { const r = Floor.S.robots.get(loc.r); return `${T("on the robot", "na robotovi")} ${r ? r.rec.n : ""}${r && r.rec.to ? " → " + r.rec.to.map(FloorRobots.placeName).join(", ") : ""}`; }
      case "rack": return T("in the inbound rack of the repair centre · waits for a free bench", "vo vstupnom regáli opravovne · čaká na voľný stôl");
      case "cage": return T("in the quarantine cage · waits for a decision in the MES", "v karanténnej klietke · čaká na rozhodnutie v MES");
      case "hand": { const p = Floor.S.people.get(loc.p); return `${T("in the hands of", "v rukách")} ${p ? p.rec.n : ""}`; }
      default: return T("waiting in a queue", "čaká vo fronte");
    }
  }

  function piecePanel(id) {
    const sp = Floor.S.sprites.get(id);
    if (!sp) return `<h3>${T("This piece has left the picture.", "Tento kus už v obraze nie je.")}</h3><div class="links"><button class="btn small" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
    const d = sp.desc;
    const where = whereIs(sp.loc);
    const done = (d.d || []).length ? `<div class="chips">${d.d.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>` : "";
    const info = `<div class="kv"><span>${T("It is", "Je to")}</span><span>${esc(kindName(d))} ${esc(d.p || "")}</span>
      <span>${T("Where", "Kde")}</span><span>${esc(where)}</span>${d.n ? `<span>${T("Inside", "Vnútri")}</span><span>${d.n}${d.c ? " / " + d.c : ""}</span>` : ""}</div>
      ${done ? `<span class="muted">${T("Stations passed (this pass):", "Prejdené stanice (tento prechod):")}</span>${done}` : ""}
      <div class="links"><button class="btn small" type="button" data-follow="piece:${id}">${T("Follow it", "Sledovať")}</button>
      <button class="btn small" type="button" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
    if (!d.sn) return `<div class="what"><b>${esc(kindName(d))}</b> ${esc(d.p || "")}</div><p class="muted">${T("It has no serial number yet: the MES issues it at the PCB laser (panels, boards), at the loader of final assembly (products), when a box is opened (KLT, pallet).", "Zatiaľ nemá sériové číslo: MES ho vydá pri laseri DPS (panely, dosky), pri nakladači finálnej montáže (výrobky) a pri otvorení boxu (KLT, paleta).")}</p>` + info;
    return serialPanel(d.sn, info);
  }

  // ------------------------------------------------------------------ robots
  const stateOf = (rec) => T(...(FloorRobots.STATE[rec.st] || ["", ""]));
  const robotPill = (rec) => `<span class="pill" style="--st:${FloorRobots.LIGHT[rec.st] || "#4a8fd8"}">${esc(stateOf(rec))}</span>`;

  function fleetBlock() {
    const f = Floor.floor;
    if (!f || !f.amr) return "";
    const rows = f.amr.map((r) => `<tr data-robot="${r.i}" style="cursor:pointer"><td><b>${esc(r.n)}</b></td><td>${esc(stateOf(r))}</td>
      <td class="num">${Number(r.b).toFixed(1)} %</td><td class="num">${(r.ld || []).length}</td><td class="num">${r.o}</td></tr>`).join("");
    const log = ((f.fleet && f.fleet.log) || []).slice().reverse().map((e) => `<tr><td>${esc(e.robot)}</td><td>${esc(FloorRobots.placeName(e.from))} → ${esc(FloorRobots.placeName(e.to))}</td>
      <td class="mono">${esc(e.item)}</td><td class="num">${esc(duration(e.s))}</td></tr>`).join("");
    return `<table><tr><th>${T("Robot", "Robot")}</th><th>${T("Now", "Teraz")}</th><th class="num">${T("battery", "batéria")}</th><th class="num">${T("load", "náklad")}</th><th class="num">${T("trips", "jázd")}</th></tr>${rows}</table>
      <div class="muted" style="margin-top:6px">${T("Transports done", "Hotových prevozov")}: <b>${n(f.fleet.done)}</b> · ${T("totes waiting for a robot", "prepraviek čaká na robota")}: <b>${n(f.fleet.waiting)}</b></div>
      ${log ? `<h3 style="margin-top:12px">${T("Latest transports", "Posledné prevozy")}</h3><table>${log}</table>` : ""}`;
  }

  const AMR_HELP = () => T("Autonomous mobile robots carry the pieces of the repair loop, so nothing flies across the plant. A machine that rejects a piece puts it into its red NOK tote; the fleet manager sends the nearest free robot, which drives along the AMR lanes in the middle of the aisles (they share them with people), docks beside the tote and takes it to the pass-through rack of the repair centre. Repaired pieces go back the same way to the cell the MES names (boards to the supermarket); pieces the MES refuses go to the quarantine cage. Idle robots wait on their chargers in the depot; one with a low battery charges before its next trip.",
    "Autonómne mobilné roboty (AMR) vozia kusy medzi linkami a opravovňou, takže nič nepreletí cez halu. Stroj, ktorý kus vyradí, ho vloží do svojej červenej NOK prepravky; riadenie flotily pošle najbližšieho voľného robota, ten ide po pruhoch AMR v strede uličiek (delí sa o ne s ľuďmi), zaparkuje pri prepravke a odvezie ju k priechodnému regálu opravovne. Opravené kusy idú rovnako späť do bunky, ktorú určí MES (dosky do supermarketu); kusy, ktoré MES odmietne, idú do karanténnej klietky. Voľné roboty čakajú na nabíjačkách v depe; robot so slabou batériou sa pred ďalšou jazdou nabije.");

  function robotPanel(id) {
    const r = Floor.S.robots.get(id);
    if (!r) return `<h3>${T("This robot is not in the picture.", "Tento robot už v obraze nie je.")}</h3><div class="links"><button class="btn small" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
    const rec = r.rec;
    const load = (rec.ld || []).map((d) => d.sn ? chipSn(d.sn) : `<span class="chip">${esc(kindName(d))}</span>`).join("");
    return `<div class="what"><b>${esc(rec.n)}</b>${robotPill(rec)}</div>
      <div class="kv"><span>${T("Battery", "Batéria")}</span><span><b>${Number(rec.b).toFixed(1)} %</b> <span class="muted">${T("(drains while driving, more with a load, a little while standing; charges on its charger)", "(klesá pri jazde, s nákladom viac, trochu aj v stoji; nabíja sa na nabíjačke)")}</span></span>
        ${rec.tg ? `<span>${T("Drives to", "Ide do")}</span><span>${esc(FloorRobots.placeName(rec.tg))}</span>` : ""}
        ${rec.to ? `<span>${T("Delivers to", "Vezie do")}</span><span>${rec.to.map((t) => esc(FloorRobots.placeName(t))).join(", ")}</span>` : ""}
        <span>${T("Carries", "Vezie")}</span><span>${load ? `<span class="chips">${load}</span>` : T("nothing", "nič")}</span>
        <span>${T("Trips this run", "Jázd v tomto behu")}</span><span>${n(rec.o)}</span></div>
      <p class="muted">${esc(AMR_HELP())}</p>
      <h3 style="margin-top:10px">${T("The fleet", "Flotila")}</h3>${fleetBlock()}
      <div class="links"><button class="btn small primary" type="button" data-follow="robot:${id}">${T("Follow", "Sledovať")}</button>
        <button class="btn small" type="button" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
  }

  function personPanel(id) {
    const p = Floor.S.people.get(id);
    if (!p) return `<h3>${T("This person has left.", "Táto osoba už odišla.")}</h3><div class="links"><button class="btn small" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
    const rec = p.rec, role = FloorPeople.ROLE[rec.r] || FloorPeople.ROLE.OPE;
    const L = Floor.layout, seat = rec.seat && L.seats[rec.seat];
    const crew = Floor.floor.crew, sh = crew.shifts[rec.sh];
    const act = nowText(rec);
    const target = rec.s ? (rec.s.startsWith("dock:") ? T("dock bay", "rampa") + " " + (+rec.s.slice(5) + 1) : rec.s) : null;
    return `<div class="person-head">${FloorPeople.portrait(rec)}<div><div class="what"><b>${esc(rec.n)}</b><span class="pill" style="--st:${role.color}">${esc(T(...role.name))}</span></div>
      <div class="muted">${T("Shift", "Zmena")} ${esc(rec.sh)} · ${esc(sh.start)}–${esc(sh.end)}</div></div></div>
      <div class="kv"><span>${T("Now", "Teraz")}</span><span>${esc(act)}${target ? " · " + esc(target) : ""}</span>
        ${esdRow(rec)}${carryRow(rec)}
        <span>${T("Seat", "Miesto")}</span><span>${seat ? `<b>${esc(rec.seat)}</b> · ${T("looks after", "stará sa o")} ${seat.attends.slice(0, 6).map((c) => esc(c.split("-").pop())).join(", ")}${seat.attends.length > 6 ? "…" : ""}` : T("no seat of their own (technician, relief, foreman, logistics)", "bez vlastného miesta (technik, náhrada, majster, skladník)")}</span></div>
      <p class="muted">${esc(roleHelp(rec.r))}</p>
      <div class="links"><button class="btn small primary" type="button" data-follow="person:${id}">${T("Follow", "Sledovať")}</button>
        ${rec.s && L.stations[rec.s] ? `<button class="btn small" type="button" data-pick-station="${esc(rec.s)}">${T("Show the machine", "Ukázať stroj")}</button>` : ""}
        <button class="btn small" type="button" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
  }

  /* what a person does right now, in words */
  function nowText(rec) {
    if (rec.pc && (rec.a === "idle" || rec.a === "post")) {
      return ["MAI", "IT"].includes(rec.r) ? T("at the computer, waiting for a call", "pri počítači, čaká na volanie")
        : T("works at the computer", "pracuje na počítači");
    }
    if (rec.a === "idle" && rec.r === "LOG") return T("waits for the next truck", "čaká na ďalší kamión");
    return T(...(FloorPeople.ACT[rec.a] || ["", ""]));
  }

  /* the ESD protection of a person: the production is an ESD protected area */
  function esdRow(rec) {
    const text = rec.e ? T(...FloorPeople.ESD[rec.e])
      : rec.r === "LOG" ? T("not needed: works in the warehouse and at the dock, outside the ESD area", "netreba: pracuje v sklade a na rampe, mimo ESD zóny")
      : T("not yet: on the way through the entrance", "ešte nie: je na ceste cez vchod");
    return `<span>ESD</span><span>${esc(text)}</span>`;
  }

  /* what the person has in the hands (laptop of IT, measuring instruments, tool box) */
  function carryRow(rec) {
    const what = { laptop: T("a laptop (IT takes it off the desk to a call)", "notebook (IT si ho berie zo stola na zásah)"),
      meter: T("measuring instruments from the cabinet in the workshop (the fault needs measuring)", "meracie prístroje zo skrinky v dielni (porucha vyžaduje meranie)"),
      tools: T("a tool box", "kufor s náradím") }[rec.c];
    const piece = rec.h ? `${esc(kindName(rec.h))} ${esc(rec.h.p || "")} ${rec.h.sn ? chipSn(rec.h.sn) : ""}` : "";
    return (what ? `<span>${T("Carries", "Nesie")}</span><span>${esc(what)}</span>` : "") + (piece ? `<span>${T("In the hands", "V rukách")}</span><span>${piece}</span>` : "");
  }

  function roleHelp(r) {
    return ({
      OPE: T("Operators sit in a seat: at a machine (loader, final inspection, packing, repair bench) or at the team desk of the line. For a minor stop or an inspection check the one who looks after the machine walks there. During breaks a relief person sits in; the relief person waits at the team desk of the lines they cover. At the entrance operators pass the ESD gate (wrist strap and footwear test).", "Operátori majú svoje miesto: pri stroji (nakladač, finálna kontrola, balenie, opravárenský stôl) alebo pri tímovom stole linky. Pri krátkom výpadku a overení poplachu kontroly príde ten, kto sa o stroj stará. Počas prestávok na tomto mieste sedí náhradník, ktorý čaká pri tímovom stole svojich liniek. Pri vstupe prejde ESD bránou (test náramku a obuvi)."),
      MAI: T("Maintenance technicians repair breakdowns and do the planned maintenance. The nearest free one is sent; if all are busy the machine waits. They have a desk with a computer in the workshop, wear an ESD coat in the production and fetch measuring instruments from the cabinet when the fault needs them.", "Technici údržby opravujú poruchy a robia plánovanú údržbu. Pošle sa najbližší voľný; ak sú všetci zaneprázdnení, stroj čaká. V dielni majú stôl s počítačom, vo výrobe ESD plášť a keď porucha vyžaduje meranie, vezmú si meracie prístroje zo skrinky."),
      IT: T("The IT technician looks after the network, the computers, the printers and the programming adapters: they come for those faults, with the laptop from their desk.", "IT technik sa stará o sieť, počítače, tlačiarne a programátory: chodí na tieto poruchy s notebookom zo svojho stola."),
      FOR: T("The foreman leads the shift meeting ten minutes after the start of the shift, walks along the lines every hour and is the second level of the andon: a breakdown open for fifteen minutes brings him to the machine. When somebody calls the foreman from the MES, he comes as soon as he has finished what he is doing.", "Majster vedie poradu zmeny desať minút po jej začiatku, každú hodinu obchádza linky a je druhou úrovňou andonu: porucha otvorená pätnásť minút ho privedie k stroju. Keď ho niekto privolá z MES, príde hneď, ako dokončí, čo práve robí."),
      MAN: T("The production manager works the morning shift at his desk in the production office, is at the shift meeting and is the third level of the andon: a breakdown open for an hour brings him to the machine (on the other shifts he is called on the phone).", "Vedúci výroby pracuje na rannej zmene pri svojom stole v kancelárii výroby, je na porade zmeny a je treťou úrovňou andonu: porucha otvorená hodinu ho privedie k stroju (na ostatných zmenách mu zavolajú)."),
      LOG: T("Logistics workers load the trucks at the dock. Between two trucks they wait in their corner by the dock; after five minutes with nothing to do they sit down at the table: cards when a colleague is there, a book alone. The next truck calls them from there.",
        "Skladníci nakladajú kamióny na rampe. Medzi dvoma kamiónmi čakajú vo svojom kútiku pri rampe; po piatich minútach bez práce si sadnú k stolu: s kolegom hrajú karty, sami čítajú knihu. Ďalší kamión ich odtiaľ zavolá."),
    })[r] || "";
  }

  function staffPanel() {
    const f = Floor.floor;
    if (!f) return "";
    const crew = f.crew;
    const rows = ["A", "B", "C"].map((code) => {
      const s = crew.shifts[code];
      const now = crew.now === code;
      return `<tr style="${now ? "font-weight:600" : "color:var(--muted)"}"><td>${code}${now ? " ▶" : ""}</td><td>${esc(s.start)}–${esc(s.end)}</td>
        <td class="num">${s.roster}</td><td class="num">${s.on_site}</td><td class="num">${s.posts}</td><td class="num">${s.break}</td><td class="num">${s.walking}</td></tr>`;
    }).join("");
    const byRole = {};
    for (const p of f.people) byRole[p.r] = (byRole[p.r] || 0) + 1;
    const roleRows = Object.entries(byRole).map(([r, c]) => `<tr><td>${esc(T(...(FloorPeople.ROLE[r] || FloorPeople.ROLE.OPE).name))}</td><td class="num">${c}</td></tr>`).join("");
    const calls = crew.calls.map((c) => {
      const who = c.p && Floor.S.people.get(c.p);
      const req = c.kind === "request";
      const ph = req ? { wait: T("waiting for somebody free", "čaká na voľného"), walk: T("on the way", "ide"), fix: T("at the machine", "pri stroji") }[c.ph]
        : { wait: T("waiting for a technician", "čaká na technika"), walk: T("technician on the way", "technik ide"), fix: T("being repaired", "oprava prebieha") }[c.ph];
      return `<tr data-pick-station="${esc(c.st)}" style="cursor:pointer"><td><b>${esc(c.st)}</b></td><td>${req ? esc(T("called from the MES", "privolaný z MES")) + (c.by ? " · " + esc(c.by) : "") : esc(c.r)}</td><td>${esc(c.role)}</td><td>${esc(ph)}${who ? " · " + esc(who.rec.n) : ""}</td></tr>`;
    }).join("");
    const answered = (crew.answered || []).slice().reverse().map((a) => `<tr><td><b>${esc(a.st)}</b></td><td>${esc(a.role)} · ${esc(a.p)}</td>
      <td>${T("came in", "prišiel za")} ${esc(duration(a.walk_s))}, ${T("stayed", "zostal")} ${esc(duration(a.stay_s))}</td></tr>`).join("");
    return `<div class="what"><b>${T("People of the plant", "Ľudia v závode")}</b></div>
      <table><tr><th>${T("Shift", "Zmena")}</th><th></th><th class="num">${T("roster", "zoznam")}</th><th class="num">${T("on site", "v závode")}</th><th class="num">${T("at posts", "na miestach")}</th><th class="num">${T("break", "prestávka")}</th><th class="num">${T("walking", "idú")}</th></tr>${rows}</table>
      <p class="muted">${T("Breaks: a half hour lunch and, on top of it, ten minutes at the coffee machine for everybody (the walks are part of them); a relief person sits in for the operator, technicians go when they are idle. Now and then somebody walks to the toilet. At a shift change the new people come in at the entrance (operators through the ESD gate, technicians, IT and the foreman put on an ESD coat) and take over their seats; the old ones walk out.", "Prestávky: pre každého polhodina na obed a k tomu desať minút pri kávovare (cesty sa počítajú do nich); operátora zastúpi záskok, technici idú, keď nemajú prácu. Občas niekto zájde na toaletu. Pri striedaní zmien noví ľudia vojdú vchodom (operátori cez ESD bránu, technici, IT a majster si obliekajú ESD plášť) a prevezmú svoje miesta, starí odídu.")}</p>
      <h3>${T("On site now", "Teraz v závode")}</h3><table>${roleRows}</table>
      <h3 style="margin-top:12px">${T("Open calls for a technician", "Otvorené volania na technika")}</h3>
      ${calls ? `<table>${calls}</table>` : `<p class="muted">${T("None: every machine is looked after.", "Žiadne: o všetky stroje je postarané.")}</p>`}
      ${answered ? `<h3 style="margin-top:12px">${T("Answered calls from the MES", "Vybavené privolania z MES")}</h3><table>${answered}</table>` : ""}
      <div class="links"><button class="btn small" type="button" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
  }

  async function loadShipments() {
    if (Date.now() - shipments.at < 8000) return;
    shipments.at = Date.now();
    try {
      const r = await fetch("/api/v1/shipments?limit=8", { cache: "no-store" });
      if (r.ok) { shipments.rows = (await r.json()).shipments; if (selected && selected.building === "FGW") renderSide(); }
    } catch (e) { /* later */ }
  }

  function dockPanel(extra = "") {
    const f = Floor.floor;
    loadShipments();
    const trucks = (f.trucks || []).map((t) => `<tr data-truck="${t.i}" style="cursor:pointer"><td><b>${esc(t.c)}</b></td><td>${esc(t.plate)}</td><td>${esc({ in: T("arriving", "prichádza"), load: T("loading", "nakladá sa"), out: T("leaving", "odchádza") }[t.ph])}</td><td class="num">${t.load.length}/${t.cap}</td></tr>`).join("");
    const done = shipments.rows.map((s) => `<tr data-asn="${esc(s.asn)}" style="cursor:pointer"><td class="mono">${esc(s.asn)}</td><td>${esc(s.customer)}</td><td class="num">${s.pallets}</td><td class="num">${n(s.units)}</td></tr>`).join("");
    const stock = Object.entries(f.fg).map(([p, v]) => `<tr><td>${esc(p)}</td><td class="num">${n(v.pallets)}</td><td class="num">${n(v.units)}</td></tr>`).join("");
    return `<div class="what"><b>${T("Finished goods warehouse and dock", "Sklad hotových výrobkov a rampa")}</b></div>
      <table><tr><th>${T("Waiting for a truck", "Čaká na kamión")}</th><th class="num">${T("pallets", "palety")}</th><th class="num">${T("units", "ks")}</th></tr>${stock}</table>
      <p class="muted">${T("Customers send a truck on a timetable. A logistics worker loads the oldest pallets, one by one; the truck leaves with a delivery note (ASN) and the MES marks the pallets, their boxes and every unit in them as shipped.", "Zákazníci posielajú kamión podľa rozpisu. Skladník nakladá najstaršie palety jednu po druhej; kamión odíde s dodacím listom (ASN) a MES označí palety, ich boxy a každý výrobok v nich ako odoslané.")}</p>
      <h3>${T("At the dock now", "Na rampe teraz")}</h3>${trucks ? `<table>${trucks}</table>` : `<p class="muted">${T("No truck at the moment.", "Práve tam nie je žiadny kamión.")}</p>`}
      <h3 style="margin-top:12px">${T("Latest shipments", "Posledné zásielky")}</h3>
      ${done ? `<table><tr><th>ASN</th><th>${T("Customer", "Zákazník")}</th><th class="num">${T("pallets", "palety")}</th><th class="num">${T("units", "ks")}</th></tr>${done}</table>` : `<p class="muted">${T("None yet.", "Zatiaľ žiadne.")}</p>`}
      ${asnDetail()}${extra}`;
  }

  async function loadAsn(asn) {
    if (asns.has(asn)) return;
    asns.set(asn, null);
    try {
      const r = await fetch(`/api/v1/shipments/${encodeURIComponent(asn)}`, { cache: "no-store" });
      if (r.ok) asns.set(asn, await r.json());
    } catch (e) { asns.delete(asn); }
    renderSide();
  }

  /* what is on one delivery note: the pallets (each one opens its boxes and units) */
  function asnDetail() {
    const asn = selected && selected.asn;
    if (!asn) return "";
    loadAsn(asn);
    const d = asns.get(asn);
    if (!d) return `<p class="muted">${T("Asking the MES…", "Pýtam sa MES…")}</p>`;
    return `<h3 style="margin-top:12px">${esc(d.asn)} · ${esc(d.customer)} · ${esc(d.plate)}</h3>
      <div class="muted">${T("Left", "Odišiel")} ${esc(MES.dt(d.departed))} · ${d.pallets} ${T("pallets", "paliet")} · ${d.klt} ${T("boxes", "boxov")} · ${n(d.units)} ${T("units", "ks")}</div>
      <div class="chips">${d.pallet_serials.map((sn) => chipSn(sn)).join("")}</div>`;
  }

  function truckPanel(id) {
    const t = Floor.trucksNow().find((x) => x.i === id);
    if (!t) return dockPanel();
    const pallets = t.load.map((p) => `<button class="chip" type="button" data-sn="${esc(p.sn || "")}">${esc(p.sn || p.p)}</button>`).join("");
    return `<div class="what"><b>${esc(t.c)}</b> <span class="muted">${esc(t.plate)}</span></div>
      <div class="kv"><span>${T("State", "Stav")}</span><span>${esc({ in: T("driving in", "prichádza"), load: T("being loaded", "nakladá sa"), out: T("leaving with the delivery note", "odchádza s dodacím listom") }[t.ph])}</span>
        <span>${T("Bay", "Rampa")}</span><span>${t.bay + 1}</span><span>${T("Pallets on board", "Palety na kamióne")}</span><span>${t.load.length} / ${t.cap}</span></div>
      ${pallets ? `<div class="chips">${pallets}</div>` : ""}
      <div class="links"><button class="btn small" type="button" data-act="dock">${T("Dock overview", "Prehľad rampy")}</button></div>`;
  }

  // ------------------------------------------------------------------ side panel
  function plantPanel() {
    const rows = lineRows();
    const items = rows.map((l) => `<tr data-pick-line="${esc(l.code)}" style="cursor:pointer">
      <td><b>${esc(l.code)}</b></td><td>${pill(l.status)}</td>
      <td class="num">${n(l.output.this_hour)}</td></tr>`).join("");
    return `<h3>${T("Whole plant", "Celý závod")}</h3>
      <p class="muted">${T("Click a line in the plan or in this list to fly into it.", "Klikni na linku v pláne alebo v tomto zozname a vletíš do nej.")}</p>
      <table><tr><th>${T("Line", "Linka")}</th><th>${T("State", "Stav")}</th><th class="num">${T("this hour", "táto hodina")}</th></tr>${items || ""}</table>`;
  }

  function stationText(code) {
    const L = Floor.layout, st = L.stations[code];
    const info = Floor.stationInfo(code);
    const help = window.MES_TEXT || {};
    const row = lineOf(st.line);
    const ov = row && row.stations.find((s) => s.code === code);
    const shiftRow = lineData && lineData.cells.flatMap((c) => c.stations).find((s) => s.code === code);
    const state = info ? info.s : "UNKNOWN";
    const phase = info ? (PHASE[info.ph] || ["", ""]) : ["", ""];
    const prog = info && info.w ? `<div class="progress"><div style="width:${Math.round(info.p * 100)}%"></div></div>` : "";
    const piece = info && (info.it || (info.out && info.out[0]) || (info.ov && info.ov.length))
      ? describePiece(info) : T("nothing inside", "vnútri nič nie je");
    const counts = shiftRow ? `<span>${T("this shift", "táto zmena")}</span><span><b>${n(shiftRow.shift.processed)}</b> ${T("done", "hotových")}${shiftRow.shift.bad ? ` · <span style="color:var(--s-down)">${n(shiftRow.shift.bad)} NOK</span>` : ""}</span>` : "";
    return `<div class="what"><b>${esc(code)}</b>${pill(state)}</div>
      <div class="muted">${esc(st.name)}${ov && ov.since ? ` · ${esc(duration(since(ov.since)))}` : ""}${ov && ov.reason_name ? ` · ${esc(ov.reason_name)}` : ""}</div>
      <p>${esc(MES.tr(help.states?.[state]))}</p>
      <div class="kv">
        <span>${T("Now", "Teraz")}</span><span>${esc(T(...phase))}${prog}</span>
        <span>${T("Inside", "Vnútri")}</span><span>${piece}${ov && ov.item ? ` <span class="mono">${esc(ov.item)}</span>` : ""}</span>
        ${counts}
      </div>
      ${crewLine(info)}${serialsLine(info)}${nokLine(code)}
      <p class="muted">${esc(MES.tr(help.stations?.[st.type]))}</p>
      <div class="links"><button class="btn small primary" type="button" data-call="${esc(code)}">${T("Call a person here", "Privolať sem pracovníka")}</button>
        <a class="btn small" href="/lines/${encodeURIComponent(st.line)}">${T("Open line page", "Otvoriť stránku linky")}</a>
        <button class="btn small" type="button" data-act="line">${T("Back to the line", "Späť na linku")}</button></div>`;
  }

  /* who is coming to / working at the machine */
  function crewLine(info) {
    const cr = info && info.cr;
    if (!cr) return "";
    const who = cr.p && Floor.S.people.get(cr.p);
    const phase = cr.ph === "wait" ? T("waiting for", "čaká na") : cr.ph === "walk" ? T("on the way:", "ide:") : T("at the machine:", "pri stroji:");
    const what = cr.k === "call" ? (cr.role === "IT" ? T("the IT technician", "IT technika") : T("a maintenance technician", "technika údržby")) : T("the operator", "operátora");
    return `<p><b>${esc(phase)}</b> ${who ? `<a href="#" data-pick-person="${who.id}">${esc(who.rec.n)}</a>` : esc(what)}</p>`;
  }

  /* what lies in the red NOK tote of the machine, for a robot */
  function nokLine(code) {
    const st = Floor.layout.stations[code];
    const box = st && Floor.floor && Floor.floor.boxes && Floor.floor.boxes[`gap:${st.line}:${st.cell}`];
    if (!box || !box.length) return "";
    return `<p>${T("In the NOK tote, waiting for a robot:", "V NOK prepravke, čaká na robota:")} ${box.map((d) => d.sn ? chipSn(d.sn) : esc(kindName(d))).join(" ")}</p>`;
  }

  /* the serial numbers of what is in the machine and of the box being filled */
  function serialsLine(info) {
    if (!info) return "";
    const pieces = [info.it, ...(info.out || []), ...((info.ov || []).map((o) => o[0]))].filter((d) => d && d.sn);
    const box = info.fill && info.fill[2] ? `<p>${T("Open box / pallet", "Otvorený box / paleta")} (${info.fill[0]}/${info.fill[1]}): ${chipSn(info.fill[2])}</p>` : "";
    return `${pieces.length ? `<p>${T("Serial numbers inside:", "Sériové čísla vnútri:")} ${pieces.slice(0, 6).map((d) => chipSn(d.sn)).join("")}</p>` : ""}${box}`;
  }

  function describePiece(info) {
    const d = info.it || (info.out && info.out[0]) || (info.ov && info.ov[0] && info.ov[0][0]);
    if (!d) return "";
    const kinds = { P: ["panel", "panel"], B: ["board", "doska"], U: ["product", "výrobok"], K: ["KLT box", "KLT box"], L: ["pallet", "paleta"], G: ["magazine of bare panels", "zásobník holých panelov"] };
    const extra = info.ov ? ` ×${info.ov.length}` : info.out && info.out.length > 1 ? ` ×${info.out.length}` : "";
    return `${esc(T(...(kinds[d.k] || ["piece", "kus"])))}${d.p ? " " + esc(d.p) : ""}${extra}`;
  }

  function linePanel(code) {
    const row = lineOf(code);
    const info = Floor.layout.lines[code];
    if (!row) return `<h3>${esc(code)}</h3>`;
    const job = row.job;
    const o = row.output;
    const stations = row.stations.map((s) => `<i class="st-${esc(s.state)}" data-pick-station="${esc(s.code)}" title="${esc(s.tag)} · ${esc(label(s.state))}" style="flex:1;height:14px;border-radius:2px;background:var(--st);cursor:pointer"></i>`).join("");
    const jobHtml = job ? `<div class="kv"><span>${T("Making", "Vyrába")}</span><span><b>${esc(job.material)}</b> · ${esc(job.operation_name || job.operation)}</span>
        <span>${T("Order", "Zákazka")}</span><span class="mono">${esc(job.order_no)}</span>
        <span>${T("Progress", "Postup")}</span><span>${n(job.good)} / ${n(job.expected)} ${esc(job.piece)}</span></div>
        <div class="progress"><div class="released" style="width:${Math.min(100, job.qty ? 100 * job.released / job.qty : 0)}%"></div><div style="width:${Math.min(100, job.expected ? 100 * job.good / job.expected : 0)}%"></div></div>`
      : `<p class="muted">${esc(info.job_line ? T("No active job.", "Žiadna aktívna úloha.") : T("A flow line: it works on whatever arrives.", "Prietoková linka: spracuje všetko, čo príde."))}</p>`;
    return `<div class="what"><b>${esc(code)}</b>${pill(row.status)}</div>
      <div class="muted">${esc(row.name)}${row.detail ? " · " + esc(row.detail) : ""}</div>
      ${jobHtml}
      <div class="kv"><span>${T("This hour", "Táto hodina")}</span><span><b>${n(o.this_hour)}</b> ${esc(o.unit)}</span>
        <span>${T("Last hour", "Minulá hodina")}</span><span>${n(o.last_hour)}</span>
        <span>${T("This shift", "Táto zmena")}</span><span>${n(o.shift)}</span>
        ${row.wip != null ? `<span>${T("On the line", "Na linke")}</span><span>${n(row.wip)} ${T("pieces", "kusov")}</span>` : ""}</div>
      <div style="display:flex;gap:2px;margin:10px 0 4px">${stations}</div>
      <p class="muted">${T("Click a machine here or in the plan to see what it holds.", "Klikni na stroj tu alebo v pláne a uvidíš, čo drží.")}</p>
      <div class="links"><a class="btn small primary" href="/lines/${encodeURIComponent(code)}">${T("Open line page", "Otvoriť stránku linky")}</a>
        <button class="btn small" type="button" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
  }

  function buildingPanel(id) {
    const b = Floor.layout.buildings.find((x) => x.id === id);
    const f = Floor.floor;
    const lang = MES.lang();
    let body = "";
    if (id === "SM" && f) {
      body = `<table><tr><th>${T("Boards", "Dosky")}</th><th class="num">${T("in stock", "na sklade")}</th><th class="num">${T("safety", "poistná")}</th></tr>${b.racks.map((r) => {
        const stock = f.sm[r.pcba] || 0, safety = (Floor.layout.products[r.product] || {}).safety_stock || 0;
        return `<tr><td>${esc(r.product)}</td><td class="num" style="${stock < safety ? "color:var(--s-down);font-weight:600" : ""}">${n(stock)}</td><td class="num">${n(safety)}</td></tr>`;
      }).join("")}</table>
        <p class="muted">${T("SMT lines refill the shelf when it drops below the safety stock; final assembly takes boards from it by belt.", "Linky SMT dopĺňajú regál, keď klesne pod poistnú zásobu; finálna montáž z neho berie dosky po páse.")}</p>`;
    } else if (id === "FGW" && f) {
      return dockPanel();
    } else if (id === "STAFF" && f) {
      return staffPanel();
    } else if (id === "GI") {
      const racks = f && f.panels ? Object.entries(f.panels).map(([l, v]) => `<tr><td>${esc(l)}</td><td class="num">${n(v)}</td></tr>`).join("") : "";
      body = `<p>${T("Magazines of bare panels leave here on belts: whenever the rack at the loader of an SMT line has room for a magazine (10 panels), the next one is sent.", "Zásobníky holých panelov odchádzajú odtiaľto po pásoch: keď má regál pri nakladači linky SMT miesto na zásobník (10 panelov), pošle sa ďalší.")}</p>
        <table><tr><th>${T("Line", "Linka")}</th><th class="num">${T("panels in the rack", "panelov v regáli")}</th></tr>${racks}</table>`;
    } else if (id === "RPR" && f) {
      const out = (f.boxes && f.boxes["rpr:out"]) || [];
      body = `<p>${T("A robot brings pieces with a defect to the inbound rack in the wall. The repair person of a free bench takes the next one out, scans it, repairs it at the bench and carries it to the outbound rack, where a robot takes it back to the cell the MES names (boards to the supermarket), or into the scrap bin. Pieces the MES refuses wait in the locked quarantine cage until somebody decides about them on the Quality page.", "Robot privezie kusy s chybou do vstupného regálu v stene. Opravár voľného stola si ďalší kus vyberie, naskenuje, opraví ho pri stole a odnesie do výstupného regálu, odkiaľ ho robot vráti do bunky, ktorú určí MES (dosky do supermarketu), alebo ho hodí do šrotu. Kusy, ktoré MES odmietne, čakajú v zamknutej karanténnej klietke, kým o nich niekto nerozhodne na stránke Kvalita.")}</p>
        <div class="kv"><span>${T("In the inbound rack", "Vo vstupnom regáli")}</span><span>${n(f.rack_n ?? 0)}</span>
        <span>${T("Repaired, for a robot", "Opravené, pre robota")}</span><span>${n(out.length)}</span>
        <span>${T("In the quarantine cage", "V karanténnej klietke")}</span><span>${n(f.cage_n ?? 0)} ${f.quar ? `(${T("the MES holds", "MES drží")} ${n(f.quar)})` : ""}</span>
        <span>${T("Washing (SPI)", "Umývanie (SPI)")}</span><span>${n(f.washing)}</span></div>
        <h3 style="margin-top:10px">${T("The robots", "Roboty")}</h3>${fleetBlock()}`;
    } else if (id === "DEPOT" && f) {
      return `<div class="what"><b>${T("AMR depot", "Depo AMR")}</b></div><p class="muted">${esc(AMR_HELP())}</p>${fleetBlock()}
        <div class="links"><button class="btn small" type="button" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
    }
    return `<div class="what"><b>${esc(lang === "sk" ? b.name_sk : b.name)}</b></div>${body}
      <div class="links"><button class="btn small" type="button" data-act="plant">${T("Whole plant", "Celý závod")}</button></div>`;
  }

  /* a short card in the corner of the plan, so the facts are in sight without scrolling */
  function renderCard() {
    const card = document.getElementById("floor-card");
    if (!selected) { card.classList.remove("show"); return; }
    const L = Floor.layout;
    let html = "";
    if (selected.station) {
      const code = selected.station, st = L.stations[code], info = Floor.stationInfo(code);
      const ph = info ? (PHASE[info.ph] || ["", ""]) : ["", ""];
      const prog = info && info.w ? `<div class="progress"><div style="width:${Math.round(info.p * 100)}%"></div></div>` : "";
      html = `<div class="head"><b>${esc(code)}</b>${pill(info ? info.s : "UNKNOWN")}<button class="x" type="button" data-act="plant" aria-label="Close">×</button></div>
        <div class="muted">${esc(st.name)}</div><div>${esc(T(...ph))}${info && info.w ? ` · ${Math.round(info.p * 100)} %` : ""}</div>${prog}
        <div class="muted">${info && (info.it || info.out || info.ov) ? describePiece(info) : T("nothing inside", "vnútri nič nie je")}</div>`;
    } else if (selected.line) {
      const row = lineOf(selected.line);
      html = `<div class="head"><b>${esc(selected.line)}</b>${row ? pill(row.status) : ""}<button class="x" type="button" data-act="plant" aria-label="Close">×</button></div>
        ${row ? `<div class="muted">${esc(row.name)}${row.detail ? " · " + esc(row.detail) : ""}</div>
        <div>${row.job ? `<b>${esc(row.job.material)}</b> · ${esc(row.job.operation_name || row.job.operation)} · ${n(row.job.good)}/${n(row.job.expected)} ${esc(row.job.piece)}` : esc(T("no active job", "bez aktívnej úlohy"))}</div>
        <div class="muted">${T("this hour", "táto hodina")}: <b>${n(row.output.this_hour)}</b> ${esc(row.output.unit)} · ${T("details below ↓", "podrobnosti nižšie ↓")}</div>` : ""}`;
    } else if (selected.person) {
      const p = Floor.S.people.get(selected.person);
      if (p) {
        const rec = p.rec, role = FloorPeople.ROLE[rec.r] || FloorPeople.ROLE.OPE;
        html = `<div class="head"><b>${esc(rec.n)}</b><span class="pill" style="--st:${role.color}">${esc(T(...role.name))}</span><button class="x" type="button" data-act="plant" aria-label="Close">×</button></div>
          <div class="person-card">${FloorPeople.portrait(rec)}<div class="muted">${T("Shift", "Zmena")} ${esc(rec.sh)} · ${esc(nowText(rec))}${rec.s ? " · " + esc(rec.s) : ""}</div></div>`;
      }
    } else if (selected.piece || selected.sn) {
      const sp = selected.piece && Floor.S.sprites.get(selected.piece);
      html = `<div class="head"><b>${sp ? esc(kindName(sp.desc)) + " " + esc(sp.desc.p || "") : esc(selected.sn)}</b><button class="x" type="button" data-act="plant" aria-label="Close">×</button></div>
        <div class="muted mono">${sp && sp.desc.sn ? esc(sp.desc.sn) : esc(selected.sn || T("no serial number yet", "ešte bez sériového čísla"))}</div>`;
    } else if (selected.truck) {
      const t = Floor.trucksNow().find((x) => x.i === selected.truck);
      html = `<div class="head"><b>${t ? esc(t.c) : T("Truck", "Kamión")}</b>${t ? `<span class="muted">${esc(t.plate)}</span>` : ""}<button class="x" type="button" data-act="plant" aria-label="Close">×</button></div>
        <div class="muted">${t ? `${t.load.length}/${t.cap} ${T("pallets", "paliet")}` : ""}</div>`;
    } else if (selected.robot) {
      const r = Floor.S.robots.get(selected.robot);
      if (r) html = `<div class="head"><b>${esc(r.rec.n)}</b>${robotPill(r.rec)}<button class="x" type="button" data-act="plant" aria-label="Close">×</button></div>
        <div class="muted">${T("battery", "batéria")} ${Number(r.rec.b).toFixed(1)} %${r.rec.to ? " · → " + r.rec.to.map((t) => esc(FloorRobots.placeName(t))).join(", ") : ""} · ${T("details below ↓", "podrobnosti nižšie ↓")}</div>`;
    } else if (selected.building) {
      const b = selected.building === "DEPOT" ? { name: "AMR depot", name_sk: "Depo AMR" } : L.buildings.find((x) => x.id === selected.building);
      html = `<div class="head"><b>${esc(MES.lang() === "sk" ? b.name_sk : b.name)}</b><button class="x" type="button" data-act="plant" aria-label="Close">×</button></div>
        <div class="muted">${selected.pallet ? esc(selected.pallet) + " · " : ""}${T("details below ↓", "podrobnosti nižšie ↓")}</div>`;
    }
    card.innerHTML = html;
    card.classList.add("show");
  }

  function renderSide() {
    if (!Floor.layout) return;
    renderCard();
    let html;
    if (selected && selected.robot) html = robotPanel(selected.robot);
    else if (selected && selected.person) html = personPanel(selected.person);
    else if (selected && selected.piece) html = piecePanel(selected.piece);
    else if (selected && selected.truck) html = truckPanel(selected.truck);
    else if (selected && selected.pallet) html = serialPanel(selected.pallet, `<div class="links"><button class="btn small" type="button" data-act="dock">${T("Dock overview", "Prehľad rampy")}</button></div>`);
    else if (selected && selected.sn) html = serialPanel(selected.sn);
    else if (selected && selected.station) html = stationText(selected.station);
    else if (selected && selected.line) html = linePanel(selected.line);
    else if (selected && selected.building) html = buildingPanel(selected.building);
    else html = plantPanel();
    side.innerHTML = html;
  }

  function select(hit, fly = true) {
    if (hit && hit.line && Floor.layout && !Floor.layout.lines[hit.line] && !hit.person && !hit.robot) {
      hit = { ...hit, line: null, station: null };            // a line the live plant does not have (tuning)
      if (!hit.building && !hit.piece && !hit.sn && !hit.truck) hit = null;
    }
    selected = hit;
    if (hit && hit.station) CallPanel.setStation(hit.station);
    clearTimeout(lineTimer);
    lineData = null;
    if (hit && hit.line) pollLine();
    renderSide();
    if (view && hit && (hit.person || hit.piece) === undefined) view.following = null;
    if (!view || !fly) return;
    if (hit && (hit.person || hit.piece || hit.sn || hit.robot)) return;    // the camera stays; "follow" is a button
    if (hit && hit.truck) {
      const bay = Floor.layout.bays[0];
      view.focusOn({ x: bay.x - 60, y: bay.rear_y - 40, w: 220, h: 260 }, true);
      return;
    }
    if (hit && hit.pallet) {
      const b = Floor.layout.buildings.find((x) => x.id === "FGW");
      view.focusOn({ x: b.x - 20, y: b.y + b.h - 260, w: b.w + 40, h: 280 }, true);
      return;
    }
    if (!hit) view.focusOn({ x: 0, y: 0, w: Floor.layout.world.w, h: Floor.layout.world.h }, true);
    else if (hit.station) { if (view.cam.k < 2) view.focusOn(zoomRect(hit.station), true); }
    else if (hit.line && !hit.station) view.focusOn(view.lineRect(hit.line), true);
    else if (hit.building) {
      const b = hit.building === "DEPOT" ? Floor.layout.depot : Floor.layout.buildings.find((x) => x.id === hit.building);
      view.focusOn({ x: b.x - 30, y: b.y - 10, w: b.w + 60, h: b.h + 10 }, true);
    }
  }

  function zoomRect(code) {
    const st = Floor.layout.stations[code];
    return { x: st.x - 60, y: st.y - 28, w: st.w + 120, h: st.h + 56 };
  }

  async function pollLine() {
    if (!selected || !selected.line) return;
    const code = selected.line;
    try {
      const r = await fetch(`/api/v1/lines/${encodeURIComponent(code)}`, { cache: "no-store" });
      if (r.ok && selected && selected.line === code) { lineData = await r.json(); if (selected.station) renderSide(); }
    } catch (e) { /* the next try follows */ }
    lineTimer = setTimeout(pollLine, 2500);
  }

  // ------------------------------------------------------------------ hover tip
  function hover(hit, x, y) {
    if (!hit) { tip.style.display = "none"; return; }
    const L = Floor.layout;
    let html = "";
    if (hit.board) {
      html = FloorBoards.tip(hit.line, esc);
    } else if (hit.station && !hit.piece) {
      const info = Floor.stationInfo(hit.station), st = L.stations[hit.station];
      const state = info ? info.s : "UNKNOWN";
      html = `<b>${esc(hit.station)}</b> · ${esc(st.name)}<br>${pill(state)}${info ? " " + esc(T(...(PHASE[info.ph] || ["", ""]))) : ""}`;
    } else if (hit.robot) {
      const r = Floor.S.robots.get(hit.robot);
      if (r) html = `<b>${esc(r.rec.n)}</b> · ${T("robot (AMR)", "robot (AMR)")}<br>${esc(stateOf(r.rec))} · ${T("battery", "batéria")} ${Number(r.rec.b).toFixed(1)} %${r.rec.ld ? ` · ${r.rec.ld.length} ${T("pieces", "ks")}` : ""}`;
    } else if (hit.person) {
      const p = Floor.S.people.get(hit.person);
      if (p) {
        const role = FloorPeople.ROLE[p.rec.r] || FloorPeople.ROLE.OPE;
        html = `<b>${esc(p.rec.n)}</b> · ${esc(T(...role.name))}<br>${T("shift", "zmena")} ${esc(p.rec.sh)} · ${esc(nowText(p.rec))}${p.rec.s ? " · " + esc(p.rec.s) : ""}`;
      }
    } else if (hit.piece) {
      const sp = Floor.S.sprites.get(hit.piece);
      if (sp) html = `<b>${esc(kindName(sp.desc))} ${esc(sp.desc.p || "")}</b><br>${sp.desc.sn ? `<span class="mono">${esc(sp.desc.sn)}</span>` : `<span class="muted">${T("no serial number yet", "ešte bez sériového čísla")}</span>`}`;
    } else if (hit.pallet) {
      html = `<b>${T("Pallet", "Paleta")} ${esc(hit.product)}</b><br><span class="mono">${esc(hit.pallet)}</span>`;
    } else if (hit.truck) {
      const t = Floor.trucksNow().find((x) => x.i === hit.truck);
      if (t) html = `<b>${esc(t.c)}</b> · ${esc(t.plate)}<br>${t.load.length}/${t.cap} ${T("pallets", "paliet")}`;
    } else if (hit.line) {
      const row = lineOf(hit.line);
      html = `<b>${esc(hit.line)}</b>${row ? " · " + esc(row.name) + "<br>" + pill(row.status) : ""}<br><span class="muted">${T("click to fly in", "klikni a vletíš")}</span>`;
    } else if (hit.thing !== undefined) {
      const th = FloorPeople.thingTip(hit.thing, MES.lang());
      if (th) html = `<b>${esc(th.name)}</b>${th.extra ? ` · ${esc(th.extra)}` : ""}${th.what ? `<br><span class="muted">${esc(th.what)}</span>` : ""}`;
    } else if (hit.building) {
      const b = hit.building === "DEPOT" ? { name: "AMR depot · chargers", name_sk: "Depo AMR · nabíjačky" } : L.buildings.find((q) => q.id === hit.building);
      html = `<b>${esc(MES.lang() === "sk" ? b.name_sk : b.name)}</b>`;
    }
    tip.innerHTML = html;
    tip.style.display = "block";
    tip.style.left = Math.min(x + 14, window.innerWidth - 320) + "px";
    tip.style.top = (y + 16) + "px";
  }

  // ------------------------------------------------------------------ speed note
  function speedHint() {
    const f = Floor.floor;
    const speed = f && f.speed;
    if (speed && speed > 20) {
      speedNote.innerHTML = T(`Simulation runs at ${speed}×: pieces fly by. Slow it down to 10× (header, after signing in) to follow the work.`,
        `Simulácia beží ${speed}×: kusy len preletia. Spomaľ ju na 10× (hlavička, po prihlásení) a prácu uvidíš.`);
      speedNote.classList.add("show");
    } else {
      speedNote.classList.remove("show");
    }
  }

  // ------------------------------------------------------------------ wiring
  document.addEventListener("click", (e) => {
    const line = e.target.closest("[data-pick-line]");
    if (line) { select({ line: line.dataset.pickLine }); return; }
    const st = e.target.closest("[data-pick-station]");
    if (st) { const code = st.dataset.pickStation; select({ line: Floor.layout.stations[code].line, station: code }); return; }
    const sn = e.target.closest("[data-sn]");
    if (sn && sn.dataset.sn) { e.preventDefault(); select({ sn: sn.dataset.sn }); return; }
    const pr = e.target.closest("[data-pick-person]");
    if (pr) { e.preventDefault(); select({ person: +pr.dataset.pickPerson }); return; }
    const as = e.target.closest("[data-asn]");
    if (as) { selected = { building: "FGW", asn: as.dataset.asn }; renderSide(); return; }
    const tr = e.target.closest("[data-truck]");
    if (tr) { select({ truck: +tr.dataset.truck, building: "FGW" }); return; }
    const rb = e.target.closest("[data-robot]");
    if (rb) { select({ robot: +rb.dataset.robot }); return; }
    const cl = e.target.closest("[data-call]");
    if (cl) { CallPanel.open(cl.dataset.call); return; }
    const fol = e.target.closest("[data-follow]");
    if (fol) { const [kind, id] = fol.dataset.follow.split(":"); view.follow(kind, +id); return; }
    const act = e.target.closest("[data-act]");
    if (act) {
      if (act.dataset.act === "plant") select(null);
      if (act.dataset.act === "line" && selected) select({ line: selected.line });
      if (act.dataset.act === "dock") select({ building: "FGW" });
    }
  });
  document.addEventListener("mes-lang", () => { legend(); renderSide(); kpis(); });
  document.getElementById("fl-in").addEventListener("click", () => view.flyTo({ ...view.cam, k: Math.min(16, view.cam.k * 1.6) }, 250));
  document.getElementById("fl-out").addEventListener("click", () => view.flyTo({ ...view.cam, k: Math.max(view.minK(), view.cam.k / 1.6) }, 250));
  document.getElementById("fl-fit").addEventListener("click", () => select(null));

  function sizeStage() {
    const L = Floor.layout;
    if (!L) return;
    const stage = document.getElementById("floor-stage");
    const w = stage.clientWidth;
    const h = Math.round(Math.min(Math.max(w * L.world.h / L.world.w + 36, 360), Math.max(420, window.innerHeight - 190)));
    stage.style.setProperty("--floor-h", `${h}px`);
  }

  Floor.start().then(() => {
    FloorBoards.start();
    sizeStage();
    window.addEventListener("resize", sizeStage);
    view = new FloorView(canvas, {
      onSelect: (hit) => {
        if (!hit) { select(null); return; }
        if (hit.piece) { const sp = Floor.S.sprites.get(hit.piece); select({ ...hit, sn: sp && sp.desc.sn }); return; }
        // far out a click picks the line; close up it picks the machine
        if (hit.station && view.cam.k < 1.1) select({ line: hit.line });
        else select(hit);
      },
      onHover: hover,
    });
    window.floorView = view;
    legend();
    renderSide();
    Floor.listeners.add(speedHint);
    Floor.layoutListeners.add(() => {                       // a new run with another plan (a tuning scenario)
      sizeStage();
      if (selected && selected.line && !Floor.layout.lines[selected.line]) selected = null;
      if (!view.userMoved) view.fitted = false;
      renderSide();
    });
  });

  MES.poll("/api/v1/plant/overview", (d) => { overview = d; kpis(); if (Floor.layout) renderSide(); }, 2000);
})();
