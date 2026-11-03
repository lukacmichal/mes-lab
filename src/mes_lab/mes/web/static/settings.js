/* The settings page: master data by tab, every value an input bound to its path in the config (see
   mes_lab/settings.py); a change goes into the draft at once (the MES checks it against the whole plant),
   the draft is released as a revision with a reason; the change log and the users. */
(() => {
  const { esc, n, dt } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const L = (pair) => (pair ? (MES.lang() === "sk" ? pair.sk : pair.en) : "");
  const perms = new Set(window.MES_PERMS || []);
  const canEdit = perms.has("settings"), canUsers = perms.has("users");
  let page = null, log = null, users = null, tab = "products", password = null, logQuery = "";
  try { tab = MES.store.get("mes-settings-tab") || "products"; } catch (e) { tab = "products"; }

  const ROLE_TEXT = {
    plant: ["run the plant (speed, pause, time, calls, scenarios)", "ovládať závod (rýchlosť, pauza, čas, privolanie, scenáre)"],
    quality: ["holds and the quarantine", "blokácie a karanténa"],
    settings: ["change the settings", "meniť nastavenia"],
    users: ["manage the users", "spravovať používateľov"],
    interface: ["the error queue of the ERP interface", "chybová fronta rozhrania ERP"],
    people: ["the qualifications of the operators", "kvalifikácie operátorov"],
  };
  // "1 change", "3 zmeny", "5 zmien"
  const changes = (k) => (MES.lang() === "sk" ? `${k} ${k === 1 ? "zmena" : k >= 2 && k <= 4 ? "zmeny" : "zmien"}` : `${k} ${k === 1 ? "change" : "changes"}`);
  const KIND = { PCBA: ["PCB assembly", "osadená doska"], FG: ["finished good", "hotový výrobok"] };

  // ------------------------------------------------------------------ talking to the MES
  function message(err) {
    try { const d = JSON.parse(err.message); if (d && d.en) return L(d); } catch (e) { /* plain text */ }
    return err.message;
  }
  function showError(text) {
    const box = document.getElementById("set-error");
    box.hidden = !text;
    box.textContent = text || "";
    if (text) box.scrollIntoView({ block: "nearest" });
  }
  async function call(method, url, body) {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) {
      const d = data && data.detail;
      throw new Error(d && typeof d === "object" ? JSON.stringify(d) : (d || r.statusText));
    }
    return data;
  }
  async function load() {
    page = await call("GET", "/api/v1/settings");
    render();
  }
  async function change(body) {
    try {
      page = await call("POST", "/api/v1/settings/draft", body);
      showError(null);
    } catch (e) {
      showError(message(e));
    }
    render();
  }

  // ------------------------------------------------------------------ one value
  const show = (v) => (v === null || v === undefined || v === "" ? "–" : v === true ? T("yes", "áno") : v === false ? T("no", "nie") : String(v));
  function tip(f) {
    const parts = [];
    if (f.d) parts.push(T(`in the draft · in force now: ${show(f.f)}`, `v návrhu · teraz platí: ${show(f.f)}`));
    if (f.o || f.d) parts.push(T(`as built: ${show(f.c)}`, `pri postavení: ${show(f.c)}`));
    if (f.h) parts.push(T(`changed in revision ${f.h.rev} by ${f.h.by}`, `zmenené v revízii ${f.h.rev}, ${f.h.by}`));
    if (f.lo !== undefined) parts.push(T(`allowed ${f.lo} to ${f.hi}`, `povolené ${f.lo} až ${f.hi}`));
    if (f.pl) parts.push(T("the machines need it: the plant starts a new run", "potrebujú to stroje: závod začne nový beh"));
    return parts.join("\n");
  }
  function inp(f, o = {}) {
    if (!f) return `<span class="faint">–</span>`;
    const dis = canEdit ? "" : " disabled";
    const data = `data-p="${esc(f.p)}" data-k="${esc(f.k)}"`;
    let el;
    const v = f.v ?? (f.k === "qty" ? 0 : "");
    if (f.k === "bool") {
      el = `<input type="checkbox" ${data}${f.v ? " checked" : ""}${dis}>`;
    } else if (f.k === "choice" || f.k === "customer") {
      const ch = f.ch || [];
      el = `<select ${data}${dis}>${ch.map((c) => `<option value="${esc(c ?? "")}"${(c ?? null) === (f.v ?? null) ? " selected" : ""}>${esc(c ?? "–")}</option>`).join("")}</select>`;
    } else if (f.k === "time") {
      el = `<input type="time" step="900" ${data} value="${esc(v)}"${dis}>`;
    } else if (f.k === "int" || f.k === "float" || f.k === "qty") {
      const step = f.k === "float" ? (o.step || "any") : 1;
      el = `<input type="number" ${data} value="${esc(v)}" step="${step}"${f.lo !== undefined ? ` min="${f.k === "qty" ? 0 : f.lo}" max="${f.hi}"` : ""} style="width:calc(${o.w || 7}ch + 26px)"${dis}>`;
    } else {
      el = `<input type="text" ${data} value="${esc(v)}" maxlength="80"${o.list ? ` list="${o.list}"` : ""} style="width:${o.w || 22}ch"${dis}>`;
    }
    const cls = ["set-in"];
    if (f.d) cls.push("d");
    if (f.o) cls.push("o");
    if (f.k === "qty" && !f.v) cls.push("zero");
    const reset = (f.o || (f.d && f.c !== null && f.c !== undefined)) && canEdit && f.k !== "entry"
      ? `<button type="button" class="set-reset" data-reset="${esc(f.p)}" title="${esc(T(`back to the plant as built: ${show(f.c)}`, `späť na stav pri postavení: ${show(f.c)}`))}">↺</button>` : "";
    return `<span class="${cls.join(" ")}" title="${esc(tip(f))}">${el}${f.o ? `<i class="set-dot" aria-hidden="true"></i>` : ""}${reset}</span>`;
  }
  function valueOf(el) {
    if (el.type === "checkbox") return el.checked;
    if (el.tagName === "SELECT") return el.value || null;
    return el.value;
  }

  // ------------------------------------------------------------------ the bar: in force, waiting, draft
  function renderBar() {
    const bar = document.getElementById("set-bar");
    const a = page.active, s = page.scheduled, d = page.draft;
    const parts = [];
    parts.push(`<div class="set-state"><span class="label">${T("In force", "Platí")}</span>${a
      ? `<b>${T("Revision", "Revízia")} ${a.number}</b> <span class="muted">· ${T("since", "od")} ${esc(dt(a.activated_at))} · ${esc(a.released_by)}</span>`
      : `<b>${T("the plant as built", "závod, ako bol postavený")}</b> <span class="muted">· ${T("no revision yet", "zatiaľ žiadna revízia")}</span>`}</div>`);
    if (s) {
      parts.push(`<div class="set-state wait"><span class="label">${T("Waiting", "Čaká")}</span><b>${T("Revision", "Revízia")} ${s.number}</b>
        <span class="muted">· ${T("in force at", "platí od")} ${esc(dt(s.effective_at))} (${T("plant time", "čas závodu")}) · ${changes(s.count)}</span>
        ${canEdit ? `<button type="button" class="btn small" data-sched="now">${T("Into force now", "Uviesť do platnosti hneď")}</button>
        <button type="button" class="btn small" data-sched="withdraw">${T("Withdraw", "Stiahnuť")}</button>` : ""}</div>`);
    }
    if (d) {
      parts.push(`<div class="set-state draft"><span class="label">${T("Draft", "Návrh")}</span><b>${changes(d.count)}</b>
        <span class="muted">· ${d.plant ? T("the machines need some: the plant will start a new run", "niektoré potrebujú stroje: závod začne nový beh") : T("only the MES: the plant keeps running", "len MES: závod beží ďalej")}</span>
        ${canEdit ? `<button type="button" class="btn small primary" data-act="review">${T("Review and release…", "Skontrolovať a vydať…")}</button>
        <button type="button" class="btn small danger" data-act="discard">${T("Discard the draft", "Zahodiť návrh")}</button>` : ""}</div>`);
    } else {
      parts.push(`<div class="set-state"><span class="muted">${canEdit
        ? T("No draft. Change a value below to start one.", "Žiadny návrh. Zmeň hodnotu nižšie a návrh sa začne.")
        : T("Sign in as a process engineer or an administrator to change the settings.", "Na zmenu nastavení sa prihlás ako technológ alebo administrátor.")}</span></div>`);
    }
    bar.innerHTML = parts.join("");
  }

  // ------------------------------------------------------------------ tabs
  function products() {
    const fg = page.products.filter((p) => p.kind === "FG"), pcba = page.products.filter((p) => p.kind === "PCBA");
    return `<div class="panel"><h3>${T("Finished goods", "Hotové výrobky")}</h3><div class="table-wrap"><table>
      <tr><th>${T("Code", "Kód")}</th><th>${T("Name", "Názov")}</th><th>${T("Customer", "Zákazník")}</th><th>${T("Board inside", "Doska vo vnútri")}</th>
        <th class="num">${T("Pieces in a KLT box", "Kusov v boxe KLT")}</th><th class="num">${T("Boxes on a pallet", "Boxov na palete")}</th>
        <th>${T("Parts kit · sets in a bin", "Sada dielov · sád v prepravke")}</th><th>${T("Lines", "Linky")}</th></tr>
      ${fg.map((p) => `<tr><td class="mono">${esc(p.code)}</td><td>${inp(p.name, { w: 34 })}</td><td>${inp(p.customer)}</td><td class="mono">${esc(p.pcba || "")}</td>
        <td class="num">${inp(p.klt_qty, { w: 6 })}</td><td class="num">${inp(p.klt_per_pallet, { w: 6 })}</td>
        <td>${p.kit ? `${esc(p.kit)} · ${inp(p.per_bin, { w: 6 })}` : "–"}</td><td class="mono">${esc(p.lines.join(", "))}</td></tr>`).join("")}
      </table></div></div>
      <div class="panel"><h3>${T("PCB assemblies (made on the SMT lines)", "Osadené dosky (vyrábajú sa na linkách SMT)")}</h3><div class="table-wrap"><table>
      <tr><th>${T("Code", "Kód")}</th><th>${T("Name", "Názov")}</th><th class="num">${T("Boards on a panel", "Dosiek na paneli")}</th>
        <th class="num">${T("Safety stock", "Poistná zásoba")}</th><th class="num">${T("Smallest lot", "Najmenšia dávka")}</th><th class="num">${T("Largest lot", "Najväčšia dávka")}</th><th>${T("Goes into", "Ide do")}</th></tr>
      ${pcba.map((p) => `<tr><td class="mono">${esc(p.code)}</td><td>${inp(p.name, { w: 30 })}</td><td class="num">${p.boards_per_panel}</td>
        <td class="num">${inp(p.safety_stock, { w: 9 })}</td><td class="num">${inp(p.min_lot, { w: 9 })}</td><td class="num">${inp(p.max_lot, { w: 9 })}</td><td class="mono">${esc(p.used_in.join(", "))}</td></tr>`).join("")}
      </table></div>
      <p class="muted small">${T("The planner keeps the boards in the SMT supermarket above the safety stock and plans SMT lots between the smallest and the largest (boards).",
        "Plánovač drží dosky v supermarkete SMT nad poistnou zásobou a plánuje dávky SMT medzi najmenšou a najväčšou (dosky).")}</p></div>
      <div class="panel"><h3>${T("Customers", "Zákazníci")}</h3><div class="table-wrap"><table>
      <tr><th>${T("Code", "Kód")}</th><th>${T("Name", "Názov")}</th><th>${T("Country", "Krajina")}</th></tr>
      ${page.customers.map((c) => `<tr><td class="mono">${esc(c.code)}</td><td>${inp(c.name)}</td><td>${esc(c.country || "")}</td></tr>`).join("")}
      </table></div></div>`;
  }

  function bom() {
    const fg = page.bom.filter((b) => b.pcba !== undefined), boards = page.bom.filter((b) => b.passes);
    const tree = fg.map((b) => `<div class="set-tree"><b class="mono">${esc(b.code)}</b> <span class="muted">${esc(b.name)}</span>
      <ul><li>1 × <span class="mono">${esc(b.pcba)}</span> <span class="muted">(${T("the board, from SMT", "doska zo SMT")})</span></li>
      ${b.kit ? `<li>1 × ${esc(b.kit.name)} <span class="muted">(${T("the parts kit of final assembly, in bins of", "sada dielov finálnej montáže, v prepravkách po")} ${inp(b.kit.per_bin, { w: 6 })})</span></li>` : ""}</ul></div>`).join("");
    const boardsHtml = boards.map((b) => {
      const prod = page.products.find((p) => p.code === b.code);
      return `<div class="panel"><h3><span class="mono">${esc(b.code)}</span> <span class="muted">· ${esc(b.name)} · ${T("goes into", "ide do")} ${esc(b.used_in.join(", "))}</span></h3>
      <div class="set-grid">${b.passes.map((ps) => {
        const total = ps.parts.reduce((a, p) => a + (p.qty.v || 0), 0);
        return `<div><h4>${esc(ps.op)} · ${esc(ps.name)}</h4><div class="table-wrap"><table>
          <tr><th>${T("Part", "Súčiastka")}</th><th>${T("What", "Čo")}</th><th>${T("Placer", "Osádzač")}</th><th>MSL</th><th class="num">${T("Per board", "Na dosku")}</th></tr>
          ${ps.parts.map((p) => `<tr${p.qty.v ? "" : ' class="faint"'}><td class="mono">${esc(p.part)}</td><td>${esc(p.name)}</td><td class="mono">${esc(p.machine)}</td><td>${esc(p.msl || "")}</td>
            <td class="num">${inp(p.qty, { w: 5 })}</td></tr>`).join("")}
          <tr><td colspan="4" class="muted">${T("Placements per board / per panel", "Osadení na dosku / na panel")}</td><td class="num"><b>${n(total)}</b> / ${n(total * (prod ? prod.boards_per_panel : 1))}</td></tr>
          </table></div>
          ${canEdit && ps.addable.length ? `<div class="set-add"><select data-add-part="${esc(b.code)}|${esc(ps.op)}"><option value="">${T("Add a part…", "Pridať súčiastku…")}</option>
            ${ps.addable.map((code) => `<option value="${esc(code)}">${esc(code)}</option>`).join("")}</select>
            <input type="number" min="1" max="500" value="1" style="width:6ch" data-add-qty="${esc(b.code)}|${esc(ps.op)}" aria-label="${esc(T("pieces per board", "kusov na dosku"))}">
            <button type="button" class="btn small" data-add="${esc(b.code)}|${esc(ps.op)}">${T("Add", "Pridať")}</button></div>` : ""}</div>`;
      }).join("")}</div></div>`;
    }).join("");
    return `<div class="panel"><h3>${T("Finished goods: what goes into one unit", "Hotové výrobky: čo ide do jedného kusu")}</h3><div class="set-trees">${tree}</div></div>${boardsHtml}
      <p class="muted small">${T("A part can only be placed by the placer it is made for (chips on PNP_CHIP, ICs on PNP_IC) and only in a pass that uses that placer. 0 takes it out of the board. The placers consume the reels by these numbers, and the genealogy of every board lists them.",
        "Súčiastku môže osadiť len osádzač, pre ktorý je určená (čipy PNP_CHIP, obvody PNP_IC), a len v prechode, ktorý ten osádzač používa. 0 ju z dosky vyberie. Osádzače podľa týchto čísel míňajú cievky a genealógia každej dosky ich vypíše.")}</p>`;
  }

  function routings() {
    return `<div class="set-grid wide">${page.routings.map((r) => `<div class="panel"><h3><span class="mono">${esc(r.code)}</span> <span class="muted">· ${esc(T(...(KIND[r.kind] || ["", ""])))}</span></h3>
      ${r.ops.map((o) => {
        const cycles = o.steps.map((s) => s.cycle.v || 0);
        const neck = Math.max(...cycles);
        const perH = neck ? 3600 / neck * (r.kind === "PCBA" ? r.boards_per_panel : 1) : 0;
        return `<div class="set-op"><div class="set-op-head"><span class="mono">${esc(o.op)}</span> ${inp(o.name, { w: 24 })}
          <span class="muted small">${T("constraint", "úzke miesto")} ${neck} s · ${T("at most", "najviac")} ${n(Math.round(perH))} ${r.kind === "PCBA" ? T("boards/h", "dosiek/h") : T("units/h", "ks/h")}</span></div>
          <div class="table-wrap"><table><tr><th>${T("Step (machine)", "Krok (stroj)")}</th><th class="num">${T("Ideal cycle (s)", "Ideálny takt (s)")} · ${r.per === "panel" ? T("per panel", "na panel") : T("per unit", "na kus")}</th></tr>
          ${o.steps.map((s) => `<tr${s.cycle.v === neck ? ' class="set-neck"' : ""}><td><span class="mono">${esc(s.type)}</span> <span class="muted">${esc(s.type_name)}</span></td><td class="num">${inp(s.cycle, { w: 6, step: 0.5 })}</td></tr>`).join("")}
          </table></div></div>`;
      }).join("")}</div>`).join("")}</div>
      <p class="muted small">${T("The ideal cycle is the time a machine needs for one panel (SMT) or one unit (final assembly) without any loss. The slowest step is the constraint of the line: it sets the most the line can make an hour, the plan of the hour-by-hour board and the performance part of the OEE. The machines run by these times (with their own small variation), so a faster step only helps when it is the constraint.",
        "Ideálny takt je čas, ktorý stroj potrebuje na jeden panel (SMT) alebo jeden kus (finálna montáž) bez akejkoľvek straty. Najpomalší krok je úzke miesto linky: určuje, koľko linka najviac vyrobí za hodinu, plán hodinovej tabule aj výkonovú časť OEE. Stroje podľa týchto časov bežia (s vlastným malým rozptylom), takže rýchlejší krok pomôže, len keď je úzkym miestom.")}</p>`;
  }

  function equipment() {
    const lines = page.lines.map((l) => `<details class="set-line"><summary><span class="mono">${esc(l.code)}</span> ${inp(l.name, { w: 28 })} <span class="muted small">${esc(l.area_name)} · ${l.stations.length} ${T("stations", "staníc")}</span></summary>
      <div class="table-wrap"><table><tr><th>${T("Station", "Stanica")}</th><th>${T("Type", "Typ")}</th><th>${T("Name", "Názov")}</th></tr>
      ${l.stations.map((s) => `<tr><td class="mono">${esc(s.code)}</td><td class="mono">${esc(s.type)}</td><td>${inp(s.name, { w: 28 })}</td></tr>`).join("")}</table></div></details>`).join("");
    const types = page.types.map((t) => `<tr><td class="mono">${esc(t.code)}</td><td>${inp(t.name, { w: 24 })}</td><td>${esc(t.kind)}${t.manual ? ` <span class="faint">· ${T("manual", "ručná")}</span>` : ""}</td>
      <td class="mono small">${esc(t.lines.length > 4 ? t.lines.slice(0, 3).join(", ") + ` +${t.lines.length - 3}` : t.lines.join(", "))}</td>
      <td class="num">${inp(t.changeover, { w: 5 })}</td><td class="num">${t.pm ? inp(t.pm.interval, { w: 6 }) : "–"}</td><td class="num">${t.pm ? inp(t.pm.duration, { w: 5 }) : "–"}</td>
      <td>${t.measurements.map((m) => `<div class="set-meas"><span class="mono">${esc(m.name)}</span> <span class="muted small">${m.mean} ± ${m.sd} ${esc(m.unit)}</span>
        <span class="small">${T("limits", "hranice")}</span> ${inp(m.lsl, { w: 6, step: "any" })} – ${inp(m.usl, { w: 6, step: "any" })} <span class="small">SPC</span> ${inp(m.spc)}</div>`).join("") || "–"}</td></tr>`).join("");
    return `<div class="panel"><h3>${T("Lines and their stations", "Linky a ich stanice")}</h3>${lines}
      <p class="muted small">${T("Names only: which machines stand on a line is the physical plant (config/plant.yaml). A station without a name of its own is called after its machine type.",
        "Len názvy: ktoré stroje stoja na linke, je fyzický závod (config/plant.yaml). Stanica bez vlastného názvu sa volá podľa typu stroja.")}</p></div>
      <div class="panel"><h3>${T("Machine types", "Typy strojov")}</h3><div class="table-wrap"><table>
      <tr><th>${T("Type", "Typ")}</th><th>${T("Name", "Názov")}</th><th>${T("Kind", "Druh")}</th><th>${T("On lines", "Na linkách")}</th><th class="num">${T("Changeover (min)", "Prestavba (min)")}</th>
        <th class="num">${T("Planned maintenance every (h)", "Plánovaná údržba každých (h)")}</th><th class="num">${T("It takes (min)", "Trvá (min)")}</th><th>${T("Measured values: spec limits, control chart", "Merané hodnoty: tolerancie, regulačná karta")}</th></tr>
      ${types}</table></div>
      <p class="muted small">${T("Spec limits: a measured value outside them is a failed piece, and the SPC page computes the capability (Cpk) against them. Tighter limits around the same process give a lower Cpk. Planned maintenance counts operating hours of the machine; the process drift (paste volume, peak temperature) goes back to its centre with it.",
        "Tolerancie: nameraná hodnota mimo nich je chybný kus a stránka SPC voči nim počíta spôsobilosť (Cpk). Užšie tolerancie okolo toho istého procesu dajú nižšie Cpk. Plánovaná údržba počíta prevádzkové hodiny stroja; drift procesu (objem pasty, špičková teplota) sa s ňou vráti do stredu.")}</p></div>`;
  }

  function addForm(section) {
    if (!canEdit) return "";
    const cats = section === "defect_codes" ? page.categories.defect : page.categories.downtime;
    return `<div class="set-add"><input type="text" placeholder="${esc(T("NEW_CODE", "NOVÝ_KÓD"))}" data-new-code="${section}" style="width:18ch" maxlength="30">
      <input type="text" placeholder="${esc(T("Name", "Názov"))}" data-new-name="${section}" style="width:28ch" maxlength="80">
      ${section === "defect_codes" ? `<input type="text" list="set-defect-cats" placeholder="${esc(T("Category", "Kategória"))}" data-new-cat="${section}" style="width:14ch">`
        : `<select data-new-cat="${section}">${cats.map((c) => `<option>${esc(c)}</option>`).join("")}</select>
           <select data-new-crew="${section}"><option value="">${T("repaired by –", "opravuje –")}</option><option>MAINTENANCE</option><option>IT</option></select>`}
      <button type="button" class="btn small" data-new="${section}">${T("Add the code", "Pridať kód")}</button></div>`;
  }
  function reasons() {
    const used = (u) => (u.length ? esc(u.join(", ")) : `<span class="faint">${T("not used by any machine", "nepoužíva ho žiadny stroj")}</span>`);
    const del = (section, r) => (r.added && canEdit ? ` <button type="button" class="btn small danger" data-del="${section}/${esc(r.code)}">${T("Remove", "Odstrániť")}</button>` : "");
    return `<datalist id="set-defect-cats">${page.categories.defect.map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
      <div class="panel"><h3>${T("Defect codes", "Kódy chýb")}</h3>${addForm("defect_codes")}<div class="table-wrap"><table>
      <tr><th>${T("Code", "Kód")}</th><th>${T("Name", "Názov")}</th><th>${T("Category (Pareto)", "Kategória (Pareto)")}</th><th>${T("Made or found by", "Vzniká alebo ju nájde")}</th></tr>
      ${page.defects.map((r) => `<tr><td class="mono">${esc(r.code)}${r.added ? ` <span class="set-chip">${T("new", "nový")}</span>` : ""}${del("defect_codes", r)}</td><td>${inp(r.name, { w: 32 })}</td>
        <td>${inp(r.category, { w: 17, list: "set-defect-cats" })}</td><td class="mono small">${used(r.used)}</td></tr>`).join("")}</table></div></div>
      <div class="panel"><h3>${T("Downtime reasons", "Dôvody prestojov")}</h3>${addForm("downtime_reasons")}<div class="table-wrap"><table>
      <tr><th>${T("Code", "Kód")}</th><th>${T("Name", "Názov")}</th><th>${T("Category (OEE loss)", "Kategória (strata OEE)")}</th><th>${T("Repaired by", "Opravuje")}</th><th>${T("The technician brings", "Technik prinesie")}</th><th>${T("Happens at", "Nastáva na")}</th></tr>
      ${page.reasons.map((r) => `<tr><td class="mono">${esc(r.code)}${r.added ? ` <span class="set-chip">${T("new", "nový")}</span>` : ""}${del("downtime_reasons", r)}</td><td>${inp(r.name, { w: 30 })}</td>
        <td>${inp(r.category)}</td><td>${inp(r.crew)}</td><td>${inp(r.needs)}</td><td class="mono small">${used(r.used)}</td></tr>`).join("")}</table></div>
      <p class="muted small">${T("The category puts a stop into its bucket of the OEE losses (a breakdown, a minor stop, a changeover, planned...). Who repairs it decides who walks to the machine: maintenance, or IT for the network, computers and printers. A new code is a new entry of the list; which faults a machine has is the machine (config/station_types.yaml).",
        "Kategória dá prestoj do jeho koša strát OEE (porucha, mikroprestoj, prestavba, plánované...). Kto opravuje, rozhodne, kto ide k stroju: údržba, alebo IT pri sieti, počítačoch a tlačiarňach. Nový kód je nová položka zoznamu; aké poruchy stroj má, je vec stroja (config/station_types.yaml).")}</p></div>`;
  }

  function shifts() {
    const len = (s) => {
      const [a, b] = [s.start.v, s.end].map((t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; });
      return ((b - a + 1440) % 1440 || 1440) / 60;
    };
    const at = (start, min) => { const [h, m] = start.split(":").map(Number); const t = (h * 60 + m + min) % 1440; return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`; };
    return `<div class="grid-2"><div class="panel"><h3>${T("Shifts", "Zmeny")}</h3><div class="table-wrap"><table>
      <tr><th>${T("Shift", "Zmena")}</th><th>${T("Name", "Názov")}</th><th>${T("Starts", "Začína")}</th><th>${T("Ends", "Končí")}</th><th class="num">${T("Hours", "Hodín")}</th></tr>
      ${page.shifts.map((s) => `<tr><td class="mono">${esc(s.code)}</td><td>${inp(s.name, { w: 14 })}</td><td>${inp(s.start)}</td><td>${esc(s.end)}</td><td class="num">${len(s)}</td></tr>`).join("")}</table></div>
      <p class="muted small">${T("A shift ends where the next one starts; each lasts 4 to 12 hours. The people of the plant come and go by them, the hour-by-hour boards and the KPI of a shift count by them.",
        "Zmena končí tam, kde začína ďalšia; každá trvá 4 až 12 hodín. Podľa nich ľudia prichádzajú a odchádzajú a podľa nich rátajú hodinové tabule aj KPI zmeny.")}</p></div>
      <div class="panel"><h3>${T("Breaks (every seat in turn)", "Prestávky (každé miesto postupne)")}</h3><div class="table-wrap"><table>
      <tr><th>${T("Break", "Prestávka")}</th><th class="num">${T("Starts (min after the shift starts)", "Začína (min po začiatku zmeny)")}</th><th class="num">${T("Minutes", "Minút")}</th><th>${T("In shift A", "V zmene A")}</th></tr>
      ${page.breaks.map((b) => `<tr><td>${esc(b.name)} <span class="faint">· ${esc(b.kind)}</span></td><td class="num">${inp(b.start, { w: 6 })}</td><td class="num">${inp(b.length, { w: 5 })}</td>
        <td class="mono">${esc(at(page.shifts[0].start.v, b.start.v))}–${esc(at(page.shifts[0].start.v, b.start.v + b.length.v))}</td></tr>`).join("")}</table></div></div>
      <div class="panel"><h3>${T("The hour-by-hour plan", "Hodinový plán")}</h3><p>${T("Plan of a line per hour = the ideal rate of its constraint ×", "Plán linky na hodinu = ideálny výkon jej úzkeho miesta ×")} ${inp(page.plant.plan_efficiency, { w: 6, step: 0.01 })}</p>
      <p class="muted small">${T("The share of the ideal a good hour still makes (a few minor stops, a changeover): 0.85 is a usual target. The boards over the lines and the shift meeting compare the output with this plan.",
        "Podiel ideálu, ktorý stihne aj dobrá hodina (pár mikroprestojov, prestavba): 0,85 je bežný cieľ. Tabule nad linkami a porada zmeny porovnávajú výrobu s týmto plánom.")}</p></div></div>`;
  }

  function usersTab() {
    const roles = `<div class="panel"><h3>${T("Roles", "Role")}</h3><div class="table-wrap"><table><tr><th>${T("Role", "Rola")}</th><th>${T("May", "Smie")}</th></tr>
      ${(users ? users.roles : ROLES_FALLBACK).map((r) => `<tr><td><b>${esc(L(r.name))}</b> <span class="faint mono">${esc(r.code)}</span></td><td>${r.perms.map((p) => esc(T(...ROLE_TEXT[p]))).join(" · ")}</td></tr>`).join("")}
      </table></div><p class="muted small">${T("Anybody may look at every page without signing in. Every change needs a signed-in user whose role may make it; the MES refuses the rest (and logs who tried).",
        "Ktokoľvek si môže pozrieť každú stránku aj bez prihlásenia. Každá zmena potrebuje prihláseného používateľa, ktorého rola ju smie urobiť; ostatné MES odmietne (a zapíše, kto to skúšal).")}</p></div>`;
    if (!canUsers) return `${roles}<div class="notice">${T("Only an administrator manages the users.", "Používateľov spravuje len administrátor.")}</div>`;
    if (!users) return `${roles}<div class="muted">…</div>`;
    const roleSel = (u) => `<select data-user-role="${esc(u.username)}">${users.roles.map((r) => `<option value="${r.code}"${r.code === u.role ? " selected" : ""}>${esc(L(r.name))}</option>`).join("")}</select>`;
    return `${password ? `<div class="notice set-pass">${T("The password of", "Heslo používateľa")} <b>${esc(password.user)}</b>: <code>${esc(password.value)}</code> · ${T("shown only now: pass it on and close this.", "zobrazí sa len teraz: odovzdaj ho a zavri toto.")} <button type="button" class="btn small" data-pass-close>×</button></div>` : ""}
      <div class="panel"><h3>${T("Users", "Používatelia")}</h3><div class="table-wrap"><table>
      <tr><th>${T("User", "Používateľ")}</th><th>${T("Name", "Meno")}</th><th>${T("Role", "Rola")}</th><th>${T("May sign in", "Smie sa prihlásiť")}</th><th>${T("Last sign-in", "Posledné prihlásenie")}</th><th></th></tr>
      ${users.users.map((u) => `<tr${u.active ? "" : ' class="faint"'}><td class="mono">${esc(u.username)}</td><td><input type="text" value="${esc(u.display_name)}" data-user-name="${esc(u.username)}" style="width:22ch" maxlength="80"></td>
        <td>${roleSel(u)}</td><td><input type="checkbox" data-user-active="${esc(u.username)}"${u.active ? " checked" : ""}></td><td>${esc(u.last_login_at ? u.last_login_at.slice(0, 16).replace("T", " ") : "–")}</td>
        <td><button type="button" class="btn small" data-user-pass="${esc(u.username)}">${T("New password", "Nové heslo")}</button></td></tr>`).join("")}
      </table></div>
      <div class="set-add"><input type="text" placeholder="${esc(T("user name", "používateľské meno"))}" id="nu-name" style="width:16ch" maxlength="40">
        <input type="text" placeholder="${esc(T("full name", "celé meno"))}" id="nu-display" style="width:24ch" maxlength="80">
        <select id="nu-role">${users.roles.map((r) => `<option value="${r.code}"${r.code === "SUPERVISOR" ? " selected" : ""}>${esc(L(r.name))}</option>`).join("")}</select>
        <button type="button" class="btn small primary" id="nu-add">${T("Add the user", "Pridať používateľa")}</button></div></div>${roles}`;
  }
  const ROLES_FALLBACK = [
    { code: "ADMIN", name: { en: "Administrator", sk: "Administrátor" }, perms: ["interface", "people", "plant", "quality", "settings", "users"] },
    { code: "ENGINEER", name: { en: "Process engineer", sk: "Technológ" }, perms: ["interface", "plant", "settings"] },
    { code: "QUALITY", name: { en: "Quality engineer", sk: "Kvalita" }, perms: ["people", "quality"] },
    { code: "SUPERVISOR", name: { en: "Shift supervisor", sk: "Majster" }, perms: ["people", "plant", "quality"] },
  ];

  const fmt = (v) => (v === null || v === undefined ? `<span class="faint">–</span>` : esc(typeof v === "object" ? Object.entries(v).filter(([k]) => k !== "added").map(([k, x]) => `${k}: ${x}`).join(", ") : show(v)));
  function changeRows(changes) {
    return changes.map((c) => `<tr><td>${esc(L(c.label))}${c.plant ? ` <span class="set-chip pl" title="${esc(T("the machines need it", "potrebujú to stroje"))}">⚙</span>` : ""}</td>
      <td>${fmt(c.old)}</td><td>${c.action === "RESET" ? `${fmt(c.new)} <span class="faint">(${T("as built", "pri postavení")})</span>` : fmt(c.new)}</td><td>${esc(c.by)}</td></tr>`).join("");
  }
  function logTab() {
    if (!log) return `<div class="muted">…</div>`;
    const revs = log.revisions.map((r) => `<details class="set-rev"${r === log.revisions[0] ? " open" : ""}><summary><b>${T("Revision", "Revízia")} ${r.number}</b>
      <span class="pill" style="--st:${r.status === "ACTIVE" ? "var(--s-running)" : "var(--s-warn)"}">${r.status === "ACTIVE" ? T("in force", "platí") : T("waiting", "čaká")}</span>
      <span class="muted">${esc(r.released_by)} · ${T("released", "vydaná")} ${esc(dt(r.released_at))} · ${T("in force from", "platí od")} ${esc(dt(r.activated_at || r.effective_at))} · ${changes(r.count)}${r.plant ? ` · ⚙ ${T("new run of the plant", "nový beh závodu")}` : ""}</span>
      <div class="set-reason">„${esc(r.reason)}“${r.check ? ` <span class="faint">· ${T("trial run", "skúšobný beh")} ${r.check.hours} h: ${n(r.check.fa_good)} ${T("good units in final assembly", "dobrých kusov vo finálnej montáži")}</span>` : ""}</div></summary>
      <div class="table-wrap"><table><tr><th>${T("What", "Čo")}</th><th>${T("Before", "Predtým")}</th><th>${T("After", "Potom")}</th><th>${T("Changed by", "Zmenil")}</th></tr>${changeRows(r.changes)}</table></div></details>`).join("");
    const ACTION = { CREATE: ["added", "pridaný"], ROLE: ["role", "rola"], ACTIVE: ["may sign in", "smie sa prihlásiť"], NAME: ["name", "meno"], PASSWORD: ["new password", "nové heslo"] };
    const ev = log.users.map((e) => `<tr><td>${esc(dt(e.at))}</td><td class="mono">${esc(e.subject)}</td><td>${esc(T(...(ACTION[e.action] || [e.action, e.action])))}</td>
      <td>${e.detail ? fmt(e.detail.old ?? null) + " → " + fmt(e.detail.new ?? e.detail.role ?? null) : ""}</td><td>${esc(e.by)}</td></tr>`).join("");
    return `<div class="set-add"><input type="search" id="log-q" value="${esc(logQuery)}" placeholder="${esc(T("Search: a code, a word of the reason…", "Hľadať: kód, slovo z dôvodu…"))}" style="width:36ch"></div>
      <div class="panel">${revs || `<p class="muted">${T("No revision has been released yet: the plant runs as it was built.", "Zatiaľ nebola vydaná žiadna revízia: závod beží, ako bol postavený.")}</p>`}</div>
      <div class="panel"><h3>${T("Users: who changed what", "Používatelia: kto čo zmenil")}</h3>${ev ? `<div class="table-wrap"><table><tr><th>${T("When", "Kedy")}</th><th>${T("User", "Používateľ")}</th><th>${T("What", "Čo")}</th><th>${T("Change", "Zmena")}</th><th>${T("By", "Kto")}</th></tr>${ev}</table></div>` : `<p class="muted">–</p>`}</div>`;
  }

  // ------------------------------------------------------------------ the release dialog
  function openRelease() {
    const d = page.draft;
    if (!d) return;
    const dlg = document.getElementById("set-release");
    const next = page.clock.next_shift;
    dlg.innerHTML = `<h3>${T("Release the draft as a revision", "Vydať návrh ako revíziu")}</h3>
      <div class="table-wrap set-dlg-table"><table><tr><th>${T("What", "Čo")}</th><th>${T("Now", "Teraz")}</th><th>${T("New", "Nové")}</th><th></th></tr>
      ${d.changes.map((c) => `<tr><td>${esc(L(c.label))}${c.plant ? ` <span class="set-chip pl">⚙</span>` : ""}</td><td>${fmt(c.old)}</td><td>${fmt(c.new)}</td>
        <td><button type="button" class="btn small" data-drop="${esc(c.path)}" title="${esc(T("take it out of the draft", "vybrať z návrhu"))}">×</button></td></tr>`).join("")}</table></div>
      <label class="field">${T("Why (the change log keeps it)", "Prečo (záznam zmien si to pamätá)")}<textarea id="rel-reason" rows="2" maxlength="300"></textarea></label>
      <fieldset class="set-when"><legend>${T("In force", "Platí")}</legend>
        <label><input type="radio" name="rel-when" value="shift" checked> ${T("from the start of the next shift", "od začiatku ďalšej zmeny")}, ${esc(dt(next))} <span class="muted">(${T("plant time", "čas závodu")})</span></label>
        <label><input type="radio" name="rel-when" value="now"> ${T("now", "hneď")} ${d.plant ? `<span class="muted">· ${T("the plant starts a new run with it: the shop floor starts empty", "závod s ňou začne nový beh: výroba začne prázdna")}</span>` : `<span class="muted">· ${T("only the MES changes, the plant keeps running", "zmení sa len MES, závod beží ďalej")}</span>`}</label>
      </fieldset>
      <p class="muted small">${T("Before it is released, the MES runs the plant with the draft for an hour in the background (a few seconds). A change that makes the plant fail is not released.",
        "Pred vydaním MES nechá závod s návrhom hodinu bežať na pozadí (pár sekúnd). Zmena, pri ktorej závod zlyhá, sa nevydá.")}</p>
      <div class="notice error" id="rel-error" hidden></div>
      <div class="set-dlg-btns"><button type="button" class="btn" data-dlg="close">${T("Back", "Späť")}</button>
        <button type="button" class="btn primary" data-dlg="release">${T("Run the trial and release", "Spustiť skúšku a vydať")}</button></div>`;
    if (!dlg.open) dlg.showModal();
  }
  async function release() {
    const dlg = document.getElementById("set-release");
    const btn = dlg.querySelector('[data-dlg="release"]'), err = document.getElementById("rel-error");
    const when = (dlg.querySelector('input[name="rel-when"]:checked') || {}).value || "shift";
    btn.disabled = true;
    btn.textContent = T("Trial run of the plant…", "Skúšobný beh závodu…");
    try {
      const out = await call("POST", "/api/v1/settings/release", { reason: document.getElementById("rel-reason").value, when });
      page = out;
      dlg.close();
      log = null;
      showError(null);
      render();
    } catch (e) {
      err.hidden = false;
      err.textContent = message(e);
      btn.disabled = false;
      btn.textContent = T("Run the trial and release", "Spustiť skúšku a vydať");
    }
  }

  // ------------------------------------------------------------------ rendering and events
  // ------------------------------------------------------------------ labels
  let lblTemplate = "KLT", lblMaterial = null, lblVersion = 0;
  function labelsTab() {
    const L0 = page.labels;
    const t = L0.templates.find((x) => x.code === lblTemplate) || L0.templates[0];
    if (!t) return `<div class="panel muted">${T("No label templates.", "Žiadne šablóny štítkov.")}</div>`;
    lblTemplate = t.code;
    if (!lblMaterial || !L0.products.some((p) => p.code === lblMaterial)) lblMaterial = L0.products[0] && L0.products[0].code;
    lblVersion += 1;
    const src = `/api/v1/settings/labels/${encodeURIComponent(t.code)}/preview?state=draft&material=${encodeURIComponent(lblMaterial || "")}&v=${lblVersion}`;
    const ph = Object.entries(L0.placeholders).map(([k, v]) => `<code>{${esc(k)}}</code> ${esc(MES.lang() === "sk" ? v[1] : v[0])}`).join(" · ");
    const code = (f) => (f.barcode ? `<span class="set-chip" title="${esc(T("printed as a bar code too", "tlačí sa aj ako čiarový kód"))}">${esc(f.barcode === "code128" ? "GS1-128" : "Code 39")}${f.di && f.di !== "(00)" ? ` · ${esc(f.di)}` : ""}</span>` : "");
    return `<div class="panel"><div class="set-lbl-head"><h3 style="margin:0">${T("Label", "Štítok")}</h3>
        <span class="segmented">${L0.templates.map((x) => `<button type="button" data-lbl-t="${esc(x.code)}" class="${x.code === t.code ? "on" : ""}">${esc(x.name.v || x.code)}</button>`).join("")}</span>
        <label class="muted small">${T("Preview with", "Náhľad s")} <select data-lbl-m>${L0.products.map((p) => `<option value="${esc(p.code)}"${p.code === lblMaterial ? " selected" : ""}>${esc(p.code)}</option>`).join("")}</select></label>
        <span class="muted small">${esc(t.size[0])} × ${esc(t.size[1])} mm · ${T("with the draft, made-up serial numbers", "s návrhom, vymyslené sériové čísla")}</span></div>
      <div class="set-lbl-prev"><img alt="${esc(t.code)}" src="${src}" style="width:${t.size[0] > t.size[1] ? "min(100%, 760px)" : "min(100%, 420px)"}"></div></div>
      <div class="panel"><h3>${T("Template", "Šablóna")} ${inp(t.name, { w: 34 })}</h3><div class="table-wrap"><table>
        <tr><th>${T("Box", "Pole")}</th><th>${T("Title", "Nadpis")}</th><th>${T("Content", "Obsah")}</th><th class="num">x</th><th class="num">y</th>
          <th class="num">${T("width", "šírka")}</th><th class="num">${T("height", "výška")}</th><th class="num">${T("text mm", "písmo mm")}</th>
          <th>${T("bold", "tučne")}</th><th>${T("printed", "tlačí sa")}</th></tr>
        ${t.fields.map((f) => `<tr><td class="mono">${esc(f.code)} ${code(f)}</td><td>${inp(f.title, { w: 20 })}</td><td>${inp(f.value, { w: 30 })}</td>
          <td class="num">${inp(f.x, { w: 4 })}</td><td class="num">${inp(f.y, { w: 4 })}</td><td class="num">${inp(f.w, { w: 4 })}</td><td class="num">${inp(f.h, { w: 4 })}</td>
          <td class="num">${inp(f.size, { w: 4, step: 0.1 })}</td><td>${inp(f.bold)}</td><td>${inp(f.show)}</td></tr>`).join("")}</table></div>
        <p class="muted small">${T("Millimetres from the top left corner. The content is text with placeholders the MES fills when it prints:", "Milimetre od ľavého horného rohu. Obsah je text so zástupnými poľami, ktoré MES vyplní pri tlači:")} ${ph}</p>
        <p class="muted small">${T("A label already printed keeps its layout and values: a change of the template applies to the next labels only. Bar codes and their data identifiers are part of the standard and are not changed here.",
          "Už vytlačený štítok si drží svoje rozloženie aj hodnoty: zmena šablóny platí až pre ďalšie štítky. Čiarové kódy a ich identifikátory dát sú daná norma a tu sa nemenia.")}</p></div>
      <div class="grid-2"><div class="panel"><h3>${T("What the labels say of the products", "Čo štítky hovoria o výrobkoch")}</h3><div class="table-wrap"><table>
        <tr><th>${T("Product", "Výrobok")}</th><th>${T("Customer part No.", "Číslo dielu zákazníka")}</th><th>${T("Change level", "Index výkresu")}</th><th class="num">${T("Unit g", "Kus g")}</th><th class="num">${T("Empty KLT g", "Prázdny KLT g")}</th></tr>
        ${L0.products.map((p) => `<tr><td class="mono">${esc(p.code)}<div class="faint">${esc(p.customer || "")}</div></td><td>${inp(p.customer_part, { w: 16 })}</td><td>${inp(p.change_level, { w: 4 })}</td>
          <td class="num">${inp(p.unit_g, { w: 6 })}</td><td class="num">${inp(p.klt_g, { w: 6 })}</td></tr>`).join("")}</table></div></div>
        <div class="panel"><h3>${T("Customers and us", "Zákazníci a my")}</h3><div class="table-wrap"><table>
        <tr><th>${T("Customer", "Zákazník")}</th><th>${T("Town, country", "Mesto, krajina")}</th><th>${T("Our supplier No.", "Naše číslo dodávateľa")}</th><th>${T("Unloading point", "Miesto vykládky")}</th></tr>
        ${L0.customers.map((c) => `<tr><td class="mono">${esc(c.code)}</td><td>${inp(c.city, { w: 14 })}</td><td>${inp(c.supplier_no, { w: 9 })}</td><td>${inp(c.dock, { w: 22 })}</td></tr>`).join("")}</table></div>
        <p>${T("Our company", "Naša firma")} ${inp(L0.supplier.name, { w: 30 })}</p><p>${T("Our address", "Naša adresa")} ${inp(L0.supplier.address, { w: 36 })}</p></div></div>`;
  }

  const TABS = { products, bom, routings, equipment, reasons, shifts, labels: labelsTab, users: usersTab, log: logTab };
  function render() {
    if (!page) return;
    renderBar();
    document.querySelectorAll("#set-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    const body = document.getElementById("set-body");
    const focus = document.activeElement && document.activeElement.id === "log-q";
    body.innerHTML = (TABS[tab] || products)();
    if (focus) { const q = document.getElementById("log-q"); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
    if (tab === "log" && !log) loadLog();
    if (tab === "users" && canUsers && !users) loadUsers();
  }
  async function loadLog() {
    try { log = await call("GET", `/api/v1/settings/log${logQuery ? "?q=" + encodeURIComponent(logQuery) : ""}`); } catch (e) { showError(message(e)); return; }
    if (tab === "log") render();
  }
  async function loadUsers() {
    try { users = await call("GET", "/api/v1/users"); } catch (e) { showError(message(e)); return; }
    if (tab === "users") render();
  }
  async function userCall(url, body) {
    try {
      const out = await call("POST", url, body);
      users = out;
      if (out.password) password = { user: out.username || body.__user, value: out.password };
      log = null;
      showError(null);
    } catch (e) {
      showError(message(e));
    }
    render();
  }

  document.getElementById("set-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (!b) return;
    tab = b.dataset.tab;
    MES.store.set("mes-settings-tab", tab);
    showError(null);
    render();
  });
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.dataset.p) { change({ path: el.dataset.p, value: valueOf(el) }); return; }
    if (el.dataset.lblM !== undefined) { lblMaterial = el.value; render(); return; }
    if (el.dataset.userRole) userCall(`/api/v1/users/${encodeURIComponent(el.dataset.userRole)}`, { role: el.value });
    if (el.dataset.userActive) userCall(`/api/v1/users/${encodeURIComponent(el.dataset.userActive)}`, { active: el.checked });
    if (el.dataset.userName) userCall(`/api/v1/users/${encodeURIComponent(el.dataset.userName)}`, { display_name: el.value });
  });
  let logTimer = null;
  document.addEventListener("input", (e) => {
    if (e.target.id !== "log-q") return;
    logQuery = e.target.value.trim();
    clearTimeout(logTimer);
    logTimer = setTimeout(loadLog, 300);
  });
  document.addEventListener("click", async (e) => {
    const t = e.target;
    const lt = t.closest("[data-lbl-t]");
    if (lt) { lblTemplate = lt.dataset.lblT; render(); return; }
    const reset = t.closest("[data-reset]");
    if (reset) { change({ path: reset.dataset.reset, reset: true }); return; }
    const act = t.closest("[data-act]");
    if (act && act.closest("#set-bar")) {
      if (act.dataset.act === "review") openRelease();
      if (act.dataset.act === "discard" && confirm(T("Throw the whole draft away?", "Zahodiť celý návrh?"))) {
        try { page = await call("DELETE", "/api/v1/settings/draft"); showError(null); } catch (err) { showError(message(err)); }
        render();
      }
      return;
    }
    const sched = t.closest("[data-sched]");
    if (sched) {
      if (sched.dataset.sched === "withdraw" && !confirm(T("Withdraw the waiting revision (it becomes a draft again)?", "Stiahnuť čakajúcu revíziu (bude z nej znova návrh)?"))) return;
      try { page = await call("POST", "/api/v1/settings/scheduled", { action: sched.dataset.sched }); log = null; showError(null); } catch (err) { showError(message(err)); }
      render();
      return;
    }
    const drop = t.closest("[data-drop]");
    if (drop) {
      try { page = await call("DELETE", `/api/v1/settings/draft/change?path=${encodeURIComponent(drop.dataset.drop)}`); } catch (err) { showError(message(err)); }
      render();
      if (page.draft) openRelease(); else document.getElementById("set-release").close();
      return;
    }
    const dlg = t.closest("[data-dlg]");
    if (dlg) { if (dlg.dataset.dlg === "close") document.getElementById("set-release").close(); else release(); return; }
    const add = t.closest("[data-add]");
    if (add) {
      const [mat, op] = add.dataset.add.split("|");
      const part = document.querySelector(`[data-add-part="${add.dataset.add}"]`).value;
      const qty = document.querySelector(`[data-add-qty="${add.dataset.add}"]`).value;
      if (part) change({ path: `materials/${mat}/routing/${op}/components/${part}`, value: qty });
      return;
    }
    const nw = t.closest("[data-new]");
    if (nw) {
      const s = nw.dataset.new;
      const code = (document.querySelector(`[data-new-code="${s}"]`).value || "").trim().toUpperCase();
      const value = { name: document.querySelector(`[data-new-name="${s}"]`).value, category: document.querySelector(`[data-new-cat="${s}"]`).value };
      const crew = document.querySelector(`[data-new-crew="${s}"]`);
      if (crew && crew.value) value.crew = crew.value;
      if (!code) { showError(T("Give the new code first", "Najprv zadaj nový kód")); return; }
      change({ path: `${s}/${code}`, value });
      return;
    }
    const del = t.closest("[data-del]");
    if (del) { change({ path: del.dataset.del, reset: true }); return; }
    if (t.closest("#nu-add")) {
      const body = { username: document.getElementById("nu-name").value.trim(), display_name: document.getElementById("nu-display").value, role: document.getElementById("nu-role").value };
      if (!body.username) { showError(T("Give the user name", "Zadaj používateľské meno")); return; }
      try {
        const out = await call("POST", "/api/v1/users", body);
        users = out; password = { user: out.username, value: out.password }; log = null; showError(null);
      } catch (err) { showError(message(err)); }
      render();
      return;
    }
    const up = t.closest("[data-user-pass]");
    if (up) {
      if (!confirm(T(`A new password for ${up.dataset.userPass}? The old one stops working.`, `Nové heslo pre ${up.dataset.userPass}? Staré prestane platiť.`))) return;
      const name = up.dataset.userPass;
      try { const out = await call("POST", `/api/v1/users/${encodeURIComponent(name)}`, { new_password: true }); users = out; password = { user: name, value: out.password }; log = null; } catch (err) { showError(message(err)); }
      render();
      return;
    }
    if (t.closest("[data-pass-close]")) { password = null; render(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.dataset && e.target.dataset.p && e.target.tagName === "INPUT") e.target.blur();
  });
  document.addEventListener("mes-lang", render);
  load().catch((e) => showError(message(e)));
  MES.refreshClock();
  setInterval(() => {                     // a revision waiting for its shift: see it go into force
    if (page && page.scheduled && !document.querySelector("#set-body input:focus, #set-release[open]")) load().catch(() => {});
  }, 15000);
})();
