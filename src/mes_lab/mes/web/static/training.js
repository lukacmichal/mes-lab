/* The training page: the scenarios, the one running (brief, hints, the holds made since it began, done),
   and the graded runs with their debrief. */
(() => {
  const { esc, n, dt } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const L = (pair) => (MES.lang() === "sk" ? pair[1] : pair[0]);
  const ABOUT = {
    BAD_LOT: ["A supplier warns that one lot of a chip part is bad. Find which one, hold it, contain what was made with it.",
      "Dodávateľ varuje, že jedna šarža čipovej súčiastky je zlá. Zisti ktorá, zablokuj ju, ohranič, čo sa z nej vyrobilo."],
    AOI_STORM: ["An AOI calls every board; tired operators wave real defects through. Get it fixed, contain what it passed.",
      "AOI hlási každú dosku; unavení operátori púšťajú aj skutočné chyby. Zariaď opravu, ohranič, čo prepustila."],
    OVEN_FAILURE: ["A reflow oven fails with panels inside. Find them and keep them from going on.",
      "Pretavovacia pec zlyhá s panelmi vo vnútri. Nájdi ich a nepusti ich ďalej."],
    COMPLAINT: ["A customer reports a failed unit. Bound the suspect units, contain them, name the shipments to report.",
      "Zákazník hlási zlyhaný kus. Ohranič podozrivé kusy, zastav ich a vymenuj zásielky na nahlásenie."],
  };
  const CAT = {
    contained: ["contained", "ohraničené", "var(--s-running)"], caught: ["caught by a tester", "zachytil tester", "#4a90d9"],
    escaped: ["escaped", "unikli", "var(--s-down)"], open: ["open", "otvorené", "#c47f17"], before: ["left before", "odišli pred", "var(--faint)"],
  };
  const HOW = { "fixed by maintenance": "opravila údržba", "fixed by it": "opravilo IT", "the oven broke down": "pec sa pokazila",
    "the lot was put on hold": "šarža išla na blokáciu", "ended by itself (nobody fixed it)": "skončila sama (nikto ju neopravil)",
    "the scenario was ended": "scenár sa ukončil", "the plant started a new run": "závod začal nový beh" };
  const how = (text) => (MES.lang() === "sk" && HOW[text] ? HOW[text] : text || "");
  let data = null, busy = false;

  const fill = (text, e) => {
    const p = e.params || {};
    const map = { start: dt(e.started_at), line: p.line || "", part: p.part || "", customer: p.customer_name || p.customer || "", serial: p.serial || "",
      asn: p.asn || "", station: p.station || "", window_from: dt(p.window_from), window_to: dt(p.window_to) };
    return esc(text).replace(/\{(\w+)\}/g, (_, k) => `<b>${esc(map[k] ?? "")}</b>`);
  };
  const counts = (c) => Object.entries(CAT).map(([k, [en, sk, color]]) =>
    `<span class="chip" style="border-color:${color}"><b style="color:${color}">${n(c[k] || 0)}</b> ${esc(T(en, sk))}</span>`).join(" ");

  function verdict(r, e) {
    if (r.score === null) return T("No suspect piece could be reached any more.", "K žiadnemu podozrivému kusu sa už nedalo dostať.");
    const c = r.counts;
    const lines = [];
    lines.push(c.escaped ? T(`${c.escaped} suspect pieces left on a truck after the start: the customer has to be told.`, `${c.escaped} podozrivých kusov odišlo po začiatku na kamióne: treba to oznámiť zákazníkovi.`)
      : T("Nothing suspect left the plant after the start.", "Po začiatku neodišiel z továrne nič podozrivé."));
    if (c.open) lines.push(T(`${c.open} suspect pieces are still in the plant without a hold: they will go on.`, `${c.open} podozrivých kusov je stále v závode bez blokácie: pôjdu ďalej.`));
    if (r.over_contained) lines.push(T(`Your holds also stopped ${n(r.over_contained)} pieces that were not suspect: a narrower window or the right lot saves sorting.`,
      `Tvoje blokácie zastavili aj ${n(r.over_contained)} kusov, ktoré podozrivé neboli: užšie okno alebo správna šarža ušetrí triedenie.`));
    if (r.first_hold_min !== null) lines.push(T(`The first hold came ${r.first_hold_min} min after the start.`, `Prvá blokácia prišla ${r.first_hold_min} min po začiatku.`));
    else lines.push(T("No hold was made.", "Žiadna blokácia nevznikla."));
    if (e.fault_end_how) lines.push(T(`The fault stopped after ${r.fault_minutes} min: ${how(e.fault_end_how)}.`, `Porucha sa zastavila po ${r.fault_minutes} min: ${how(e.fault_end_how)}.`));
    if (r.oven_in_repair) lines.push(T("The oven was still being repaired: the panels inside have no record of it yet and are not counted.", "Pec sa ešte opravovala: panely vo vnútri ešte nemajú záznam a nie sú započítané."));
    if (r.report) {
      const rp = r.report;
      lines.push(T(`Shipments to report: ${rp.right.length} named right, ${rp.missed.length} missed${rp.missed.length ? ` (${rp.missed.join(", ")})` : ""}, ${rp.wrong.length} named for nothing.`,
        `Zásielky na nahlásenie: ${rp.right.length} správne, ${rp.missed.length} chýba${rp.missed.length ? ` (${rp.missed.join(", ")})` : ""}, ${rp.wrong.length} navyše.`));
    }
    return lines.map((x) => `<li>${esc(x)}</li>`).join("");
  }

  function debrief(e) {
    const r = e.result;
    if (!r) return `<p class="muted">${T("Aborted: not graded.", "Prerušené: bez hodnotenia.")}</p>`;
    const ex = Object.entries(r.examples || {}).map(([k, s]) => `${esc(T(CAT[k][0], CAT[k][1]))}: <a class="mono" href="/trace?q=${encodeURIComponent(s)}">${esc(s)}</a>`).join(" · ");
    return `<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:8px 0">
        <div class="kpi" style="min-width:140px"><div class="label">${T("Score", "Skóre")}</div><div class="value">${r.score === null ? "–" : `${r.score} %`}</div></div>
        <div>${counts(r.counts)}<div class="muted small" style="margin-top:4px">${n(r.suspects)} ${T("suspect pieces", "podozrivých kusov")} · ${T("stopped for nothing", "zastavené zbytočne")}: ${n(r.over_contained)}</div></div></div>
      <ul>${verdict(r, e)}</ul>
      ${r.holds.length ? `<table><tr><th>${T("Hold", "Blokácia")}</th><th>${T("Made", "Vznikla")}</th><th>${T("What", "Čo")}</th><th class="num">${T("Pieces", "Kusov")}</th></tr>
        ${r.holds.map((h) => `<tr><td class="mono">#${h.id}</td><td class="mono">${esc(dt(h.at))}</td><td>${esc(h.kind)} <b>${esc(h.target)}</b>${h.from ? ` <span class="faint">${esc(dt(h.from))}–${esc(dt(h.to))}</span>` : ""}</td><td class="num">${n(h.affected)}</td></tr>`).join("")}</table>` : ""}
      ${r.asns_to_report.length ? `<p class="small">${T("Shipments with suspect pieces", "Zásielky s podozrivými kusmi")}: <span class="mono">${r.asns_to_report.map(esc).join(", ")}</span></p>` : ""}
      ${ex ? `<p class="small muted">${T("For example", "Napríklad")}: ${ex}</p>` : ""}`;
  }

  function render() {
    if (!data) return;
    const can = MES.can("plant");
    const run = data.running;
    const sc = data.scenarios;
    const runEl = document.getElementById("tr-running");
    if (run) {
      const s = sc[run.scenario];
      const fault = run.scenario === "COMPLAINT" ? "" : run.fault_end_at
        ? `<span class="pill st-RUNNING">${T("the fault stopped", "porucha sa zastavila")} ${esc(dt(run.fault_end_at))}: ${esc(how(run.fault_end_how))}</span>`
        : `<span class="pill st-DOWN">${T("the fault is on", "porucha trvá")}</span>`;
      runEl.innerHTML = `<div class="panel" style="margin-bottom:16px;border-color:var(--accent)">
        <div class="unit-head"><h3 style="margin:0">${T("Running", "Beží")}: ${esc(L(s.name))}</h3><span class="spacer"></span>${fault}
          <span class="muted small">${T("since", "od")} ${esc(dt(run.started_at))} · ${run.minutes} min · ${esc(run.started_by)}</span></div>
        <p style="font-size:15px;line-height:1.5">${fill(L(s.brief), run)}</p>
        <details><summary>${T("Hints", "Nápovedy")}</summary><ul>${(MES.lang() === "sk" ? s.hints[1] : s.hints[0]).map((h) => `<li>${esc(h)}</li>`).join("")}</ul></details>
        <h4>${T("Holds made since the start", "Blokácie od začiatku")}</h4>
        ${run.holds.length ? `<table>${run.holds.map((h) => `<tr><td class="mono">#${h.id}</td><td class="mono">${esc(dt(h.at))}</td><td>${esc(h.kind)} <b>${esc(h.target)}</b></td><td class="num">${n(h.affected)} ${T("pieces", "kusov")}</td></tr>`).join("")}</table>`
          : `<p class="muted">${T("None yet.", "Zatiaľ žiadna.")} <a href="/holds">${T("Holds", "Blokácie")} →</a></p>`}
        ${can ? `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px">
          ${run.scenario === "COMPLAINT" ? `<label class="small">${T("Shipments (ASN) to report, separated by commas", "Zásielky (ASN) na nahlásenie, oddelené čiarkou")}<br>
            <input id="tr-asns" class="mono" style="width:min(520px,80vw)" placeholder="ASNXXXXX0001, ASNXXXXX0002"></label>` : ""}
          <span class="spacer"></span>
          <button class="btn" data-finish="abort">${T("Abort", "Prerušiť")}</button>
          <button class="btn primary" data-finish="done">${T("Done: grade it", "Hotovo: vyhodnotiť")}</button></div>` : ""}
      </div>`;
    } else {
      runEl.innerHTML = "";
    }
    document.getElementById("tr-cards").innerHTML = Object.entries(sc).map(([code, s]) => `<div class="panel">
        <h3>${esc(L(s.name))}</h3><p>${esc(L(ABOUT[code]))}</p>
        ${can && !run ? `<button class="btn primary" data-start="${code}">${T("Start in the live plant", "Spustiť v živom závode")}</button>`
          : !can ? `<p class="muted small">${T("Sign in to start a scenario.", "Na spustenie scenára sa prihlás.")}</p>` : ""}</div>`).join("");
    document.getElementById("tr-past").innerHTML = data.past.length ? data.past.map((e) => `<details class="tr-run"${e === data.past[0] ? " open" : ""}>
        <summary><b>${esc(L(sc[e.scenario].name))}</b> <span class="mono">${esc(dt(e.started_at))}</span> · ${esc(e.started_by)} · ${e.minutes} min ·
          ${e.status === "ABORTED" ? T("aborted", "prerušené") : `${T("score", "skóre")} <b>${e.result && e.result.score !== null ? `${e.result.score} %` : "–"}</b>`}</summary>
        ${debrief(e)}
        ${e.active_holds && MES.can("quality") ? `<p><button class="btn" data-release="${e.id}">${T(`Release the ${e.active_holds} holds of this run to production (sorted)`, `Uvoľniť ${e.active_holds} blokácie tohto behu do výroby (roztriedené)`)}</button>
          <span class="muted small">${T("Otherwise the live plant keeps waiting for the held pieces.", "Inak živý závod na zablokované kusy čaká ďalej.")}</span></p>` : ""}</details>`).join("")
      : `<p class="muted">${T("No run graded yet.", "Zatiaľ žiadny vyhodnotený beh.")}</p>`;
  }

  document.addEventListener("click", async (e) => {
    const start = e.target.closest("[data-start]"), fin = e.target.closest("[data-finish]"), rel = e.target.closest("[data-release]");
    if ((!start && !fin && !rel) || busy) return;
    busy = true;
    try {
      if (rel) await MES.post(`/api/v1/training/${rel.dataset.release}/release-holds`, {});
      else if (start) await MES.post("/api/v1/training/start", { scenario: start.dataset.start });
      else {
        const abort = fin.dataset.finish === "abort";
        if (abort && !confirm(T("Abort the scenario without a grade?", "Prerušiť scenár bez hodnotenia?"))) return;
        const asns = (document.getElementById("tr-asns")?.value || "").split(/[,\s]+/).filter(Boolean);
        await MES.post("/api/v1/training/finish", { asns, abort });
      }
      await refresh();
    } catch (err) { alert(err.message); } finally { busy = false; }
  });

  let timer = null;
  async function refresh() {
    clearTimeout(timer);
    try {
      const r = await fetch("/api/v1/training", { cache: "no-store" });
      if (r.ok) {
        const typing = document.activeElement && document.activeElement.id === "tr-asns" ? document.activeElement.value : null;
        data = await r.json();
        MES.setClock(data.clock);
        render();
        if (typing !== null) { const el = document.getElementById("tr-asns"); el.value = typing; el.focus(); }
      }
    } finally {
      timer = setTimeout(refresh, 5000);
    }
  }
  document.addEventListener("mes-lang", render);
  refresh();
})();
