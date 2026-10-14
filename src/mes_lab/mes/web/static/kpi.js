/* The KPI page: OEE, yields, automation, people, robots and the cost of a good unit over a window of
   plant time. Everything is computed in the MES (mes_lab/kpi.py); this page only shows it. */
(() => {
  const { esc, n, label } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  let windowName = MES.store.get("mes-kpi-window") || "shift";
  let data = null;

  const pct = (v, digits = 1) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(digits)} %`);
  const num = (v, digits = 1) => (v === null || v === undefined ? "–" : Number(v).toFixed(digits));
  const money = (v, digits = 0) => (v === null || v === undefined ? "–"
    : `${Number(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })} €`);
  const LOSS = ["RUNNING", "MINOR_STOP", "STARVED", "BLOCKED", "DOWN", "CHANGEOVER", "MAINTENANCE", "NO_ORDER"];

  /* how good is an OEE / a yield: a word next to the number, never colour alone */
  function grade(v, good, fair) {
    if (v === null || v === undefined) return "";
    if (v >= good) return `<span class="grade good">${T("good", "dobré")}</span>`;
    if (v >= fair) return `<span class="grade fair">${T("fair", "priemer")}</span>`;
    return `<span class="grade weak">${T("weak", "slabé")}</span>`;
  }

  function tile(title, value, sub = "", hint = "") {
    return `<div class="kpi"${hint ? ` title="${esc(hint)}"` : ""}><div class="label">${esc(title)}</div>
      <div class="value">${value}</div>${sub ? `<div class="muted" style="font-size:12px">${sub}</div>` : ""}</div>`;
  }

  function tiles(d) {
    const p = d.plant, c = d.costs;
    return [
      tile(T("OEE · final assembly", "OEE · finálna montáž"), pct(p.oee), grade(p.oee, 0.85, 0.65),
        T("availability × performance × quality at the constraint of each line, weighted by planned time", "dostupnosť × výkon × kvalita na úzkom hrdle každej linky, vážené plánovaným časom")),
      tile(T("OEE · SMT", "OEE · SMT"), pct(p.oee_smt), grade(p.oee_smt, 0.85, 0.65)),
      tile(T("First pass yield · final assembly", "FPY · finálna montáž"), pct(p.fpy_fa), grade(p.fpy_fa, 0.98, 0.95)),
      tile(T("RTY · board to unit", "RTY · od dosky po výrobok"), pct(p.rty), T("without a single repair", "bez jedinej opravy")),
      tile(T("Good units", "Dobré kusy"), n(p.good_units), `${num(p.units_per_h, 0)} ${T("per hour", "za hodinu")} · ${n(p.shipped)} ${T("shipped", "odoslaných")}`),
      tile(T("Automation degree", "Stupeň automatizácie"), pct(p.automation, 0), `${num(p.manual_s_per_unit, 0)} s ${T("of manual work per unit", "ručnej práce na kus")}`),
      tile(T("Productivity", "Produktivita"), num(p.units_per_person_h, 1), `${T("units per person-hour", "ks na človekohodinu")} · ${n(Math.round(p.person_h))} h`),
      tile("MTBF / MTTR", `${num(p.mtbf_h, 0)} h <small>/ ${num(p.mttr_min, 0)} min</small>`, `${n(p.breakdowns)} ${T("breakdowns", "porúch")}`),
      tile(T("Scrap · repairs", "Šrot · opravy"), pct(p.scrap_rate, 2), `${n(p.repairs)} ${T("repairs", "opráv")} (${pct(p.repair_rate, 1)})`),
      tile(T("Robots (AMR)", "Roboty (AMR)"), pct(p.robot_utilization, 0), `${p.robots} ${T("robots", "robotov")} · ${num(p.transports_per_h, 0)} ${T("transports per hour", "prevozov za hodinu")}`,
        T("share of the time the robots spend on transports (driving, docking, waiting at crossings)", "podiel času, ktorý roboty trávia prevozmi (jazda, parkovanie, čakanie na križovaní)")),
      c ? tile(T("Cost of a good unit", "Náklady na dobrý kus"), money(c.cost_per_unit, 2), `${money(c.conversion_per_unit, 2)} ${T("without material", "bez materiálu")}`) : "",
      c ? tile(T("Margin", "Marža"), pct(c.margin_share, 1), `${money(c.margin_per_h, 0)} ${T("per hour", "za hodinu")}`) : "",
    ].join("");
  }

  /* where the time of the constraint went: one bar per line, the states in the colours of the plan */
  function timeBar(losses) {
    const run = Math.max(0, 1 - Object.values(losses).reduce((a, b) => a + b, 0));
    const parts = { RUNNING: run, ...losses };
    return `<div class="stack-bar kpi-bar">${LOSS.filter((s) => parts[s] > 0.002).map((s) =>
      `<i class="st-${s}" style="width:${(parts[s] * 100).toFixed(2)}%" title="${esc(label(s))} ${pct(parts[s], 1)}"></i>`).join("")}</div>`;
  }

  function linesTable(d) {
    const head = `<tr><th>${T("Line", "Linka")}</th><th class="num">OEE</th><th class="num" title="${esc(T("availability", "dostupnosť"))}">A</th>
      <th class="num" title="${esc(T("performance", "výkon"))}">P</th><th class="num" title="${esc(T("quality = first pass yield", "kvalita = výťažnosť na prvý raz"))}">Q</th>
      <th class="num" title="${esc(T("how much of the time the line had work", "koľko času mala linka prácu"))}">${T("loading", "vyťaž.")}</th>
      <th class="num">${T("output", "výstup")}</th><th class="num">MTBF</th><th class="num">MTTR</th>
      <th style="min-width:160px">${T("time of the constraint", "čas úzkeho hrdla")}</th></tr>`;
    const rows = d.lines.map((l) => `<tr><td><a href="/lines/${encodeURIComponent(l.code)}"><b>${esc(l.code)}</b></a>
        <div class="faint" style="font-size:11px">${esc(l.constraint)}${l.main_loss ? ` · ${esc(label(l.main_loss))}` : ""}</div></td>
      <td class="num"><b>${pct(l.oee, 0)}</b></td><td class="num">${pct(l.availability, 0)}</td><td class="num">${pct(l.performance, 0)}</td>
      <td class="num">${pct(l.quality, 1)}</td><td class="num">${pct(l.loading, 0)}</td><td class="num">${n(l.output)}</td>
      <td class="num">${l.mtbf_h ? num(l.mtbf_h, 0) + " h" : "–"}</td><td class="num">${l.mttr_min ? num(l.mttr_min, 0) + " min" : "–"}</td>
      <td>${timeBar(l.losses)}</td></tr>`).join("");
    return head + rows;
  }

  function productsTable(d) {
    const head = `<tr><th>${T("Product", "Výrobok")}</th><th class="num">FPY SMT</th><th class="num">FPY FA</th><th class="num">RTY</th>
      <th class="num">${T("good", "dobré")}</th><th class="num">${T("shipped", "odoslané")}</th><th class="num">${T("scrapped", "šrot")}</th></tr>`;
    return head + Object.entries(d.products).map(([code, p]) => `<tr><td><b>${esc(code)}</b></td><td class="num">${pct(p.fpy_smt, 1)}</td>
      <td class="num">${pct(p.fpy_fa, 1)}</td><td class="num"><b>${pct(p.rty, 1)}</b></td><td class="num">${n(p.good)}</td>
      <td class="num">${n(p.shipped)}</td><td class="num">${n(p.scrapped)}</td></tr>`).join("");
  }

  function capacity(d) {
    const a = d.areas;
    const row = (name, area) => {
      const need = area.work_lines || 0, have = area.lines || 0;
      const share = have ? Math.min(1, need / have) : 0;
      return `<div class="bar-row"><span>${esc(name)}</span><div class="track"><div class="fill" style="width:${(share * 100).toFixed(1)}%"></div></div>
        <span class="num">${num(need, 1)} / ${have}</span></div>`;
    };
    const smt = a.SMT, fa = a.FA;
    const spare = smt && smt.lines ? smt.lines - smt.work_lines / 0.85 : 0;
    return `<div class="bars">${row(T("SMT lines", "Linky SMT"), smt)}${row(T("Final assembly", "Finálna montáž"), fa)}</div>
      <p class="muted" style="margin:8px 0 0">${T("Work content of the area in lines running flat out at their constraint, against the lines there are.",
        "Obsah práce oblasti v linkách bežiacich naplno na úzkom hrdle, oproti počtu liniek.")}
      ${spare >= 1 ? T(` At a usual 85 % the SMT work would need ${num(smt.work_lines / 0.85, 1)} lines: about ${Math.floor(spare)} of them could go (see the Tuning page).`,
        ` Pri bežných 85 % by práca SMT potrebovala ${num(smt.work_lines / 0.85, 1)} linky: zhruba ${Math.floor(spare)} by mohli ísť preč (pozri stránku Tuning).`) : ""}</p>`;
  }

  function costsBlock(d) {
    const c = d.costs;
    if (!c) return `<p class="muted">${T("No cost rates configured.", "Sadzby nákladov nie sú nastavené.")}</p>`;
    const parts = [["labor", T("people", "ľudia")], ["lines", T("lines (depreciation, service)", "linky (odpisy, servis)")],
      ["energy", T("energy", "energia")], ["robots", T("robots", "roboty")], ["scrap", T("scrapped material", "zošrotovaný materiál")]];
    const max = Math.max(1, ...parts.map(([k]) => c[k] || 0));
    const good = d.plant.good_units || 0;
    return `<div class="bars">${parts.map(([k, name]) => `<div class="bar-row"><span>${esc(name)}</span>
        <div class="track"><div class="fill" style="width:${((c[k] || 0) / max * 100).toFixed(1)}%"></div></div>
        <span class="num" title="${esc(money(c[k]))}">${good ? money((c[k] || 0) / good, 2) : "–"}</span></div>`).join("")}</div>
      <table style="margin-top:10px"><tr><td>${T("Revenue", "Tržby")}</td><td class="num">${money(c.revenue)}</td></tr>
        <tr><td>${T("Material of the good units", "Materiál dobrých kusov")}</td><td class="num">− ${money(c.material)}</td></tr>
        <tr><td>${T("Conversion (people, lines, energy, robots, scrap)", "Spracovanie (ľudia, linky, energia, roboty, šrot)")}</td><td class="num">− ${money(c.conversion)}</td></tr>
        <tr><td><b>${T("Margin", "Marža")}</b></td><td class="num"><b>${money(c.margin)}</b> (${pct(c.margin_share, 1)})</td></tr></table>
      <p class="muted" style="margin:8px 0 0">${T("The bars show the conversion cost per good unit.", "Pruhy ukazujú náklady na spracovanie jedného dobrého kusa.")}</p>`;
  }

  function render() {
    if (!data) return;
    const w = data.window;
    document.getElementById("kpi-when").textContent = `${MES.dt(w.since)} – ${MES.dt(w.until)} · ${num(w.hours, 1)} h ${T("of plant time", "času závodu")}`;
    document.getElementById("kpi-tiles").innerHTML = tiles(data);
    document.getElementById("kpi-lines").innerHTML = linesTable(data);
    document.getElementById("kpi-products").innerHTML = productsTable(data);
    document.getElementById("kpi-capacity").innerHTML = capacity(data);
    document.getElementById("kpi-costs").innerHTML = costsBlock(data);
    document.getElementById("kpi-legend").innerHTML = LOSS.map((s) => `<span class="st-${s}"><i></i>${esc(label(s))}</span>`).join("");
    document.querySelectorAll("#kpi-window button").forEach((b) => b.classList.toggle("on", b.dataset.w === windowName));
  }

  async function load() {
    try {
      const r = await fetch(`/api/v1/kpi?window=${encodeURIComponent(windowName)}`, { cache: "no-store" });
      if (r.ok) { data = await r.json(); MES.setClock(data.clock); render(); }
    } catch (e) { console.warn("kpi load failed", e); }
  }

  document.querySelectorAll("#kpi-window button").forEach((b) => b.addEventListener("click", () => {
    windowName = b.dataset.w;
    MES.store.set("mes-kpi-window", windowName);
    data = null;
    render();
    load();
  }));
  document.addEventListener("mes-lang", render);
  load();
  setInterval(load, 15000);
})();
