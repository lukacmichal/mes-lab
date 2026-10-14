/* The tuning game: knobs, a run of the plant offline, the result against the plant as it is, the board. */
(() => {
  const { esc, n } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const KNOB_NAMES = {
    smt_lines: ["SMT lines", "Linky SMT"], robots: ["Robots (AMR)", "Roboty (AMR)"],
    maintenance_day: ["Maintenance · day shifts", "Údržba · denné zmeny"], maintenance_night: ["Maintenance · night", "Údržba · nočná"],
    relief: ["Relief operators per shift", "Záskoky na zmenu"], kit_bins: ["Bins of parts at the loader", "Zásobníky dielov pri nakladači"],
    fa_buffer: ["Places between the cells (FA)", "Miesta medzi bunkami (FA)"],
  };
  const KNOB_HELP = {
    smt_lines: ["They make every board; each one costs depreciation and service every hour.", "Vyrábajú všetky dosky; každá stojí každú hodinu odpisy a servis."],
    robots: ["They carry repairs, quarantine, bins of parts and empty boxes. Too few: the loader waits for parts.", "Vozia opravy, karanténu, zásobníky dielov a prázdne boxy. Primálo: nakladač čaká na diely."],
    maintenance_day: ["Technicians repair breakdowns; when all are busy, the machine waits.", "Technici opravujú poruchy; keď sú všetci zaneprázdnení, stroj čaká."],
    maintenance_night: ["The night shift has fewer people.", "Nočná zmena má menej ľudí."],
    relief: ["They sit in while the operators have their breaks.", "Sedia na mieste operátorov počas prestávok."],
    kit_bins: ["More bins give the robots more time, but tie up material at the line.", "Viac zásobníkov dá robotom viac času, ale viaže materiál pri linke."],
    fa_buffer: ["A longer conveyor between cells evens out short stops.", "Dlhší pás medzi bunkami vyrovná krátke výpadky."],
  };
  let board = null;
  let knobs = null;
  let hours = 12;
  let shown = null;          // the result on screen
  let job = null;

  const pct = (v, d = 1) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(d)} %`);
  const money = (v, d = 0) => (v === null || v === undefined ? "–" : `${Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })} €`);
  const num = (v, d = 1) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));

  function knobsHtml() {
    return Object.entries(board.knobs).map(([k, spec]) => {
      const v = knobs[k], base = board.base[k];
      return `<div class="knob"><div><b>${esc(T(...KNOB_NAMES[k]))}</b>${v !== base ? ` <span class="faint">(${T("now", "teraz")} ${base})</span>` : ""}
          <div class="muted" style="font-size:12px">${esc(T(...KNOB_HELP[k]))}</div></div>
        <div class="stepper"><button type="button" data-k="${k}" data-d="-1"${v <= spec.min ? " disabled" : ""}>−</button>
          <span>${v}</span><button type="button" data-k="${k}" data-d="1"${v >= spec.max ? " disabled" : ""}>+</button></div></div>`;
    }).join("");
  }

  /* one result next to the plant as it is: the score and what moved it */
  function resultHtml(r) {
    const base = board.baseline;
    if (!r) return `<h3>${T("Result", "Výsledok")}</h3><p class="muted">${T("Set the knobs and press Simulate. The plant as it is runs first, as the reference.", "Nastav gombíky a stlač Prepočítať. Najprv sa ako porovnanie prepočíta závod, aký je.")}</p>`;
    const delta = (a, b, fmt, better = 1) => {
      if (!base || a === null || b === null || a === undefined || b === undefined || r === base) return "";
      const d = a - b;
      if (Math.abs(d) < 1e-9) return `<span class="faint">=</span>`;
      const good = d * better > 0;
      return `<span class="delta ${good ? "up" : "down"}">${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))}</span>`;
    };
    const row = (name, key, fmt, better = 1, src = (x) => x[key]) => `<tr><td>${esc(name)}</td><td class="num"><b>${fmt(src(r))}</b></td>
      <td class="num faint">${base ? fmt(src(base)) : "–"}</td><td class="num">${base ? delta(src(r), src(base), fmt, better) : ""}</td></tr>`;
    const same = base && JSON.stringify(r.knobs) === JSON.stringify(base.knobs);
    const verdict = !base || same ? "" : (() => {
      const d = r.margin_per_h - base.margin_per_h, units = r.good_units - base.good_units;
      const what = Math.abs(d) < 5 ? T("about the same money as the plant as it is", "zhruba rovnaké peniaze ako závod, aký je")
        : d > 0 ? T(`${money(d)} an hour more than the plant as it is`, `o ${money(d)} za hodinu viac ako závod, aký je`)
          : T(`${money(-d)} an hour less than the plant as it is`, `o ${money(-d)} za hodinu menej ako závod, aký je`);
      const made = units === 0 ? "" : units > 0 ? T(`, ${n(units)} good units more`, `, o ${n(units)} dobrých kusov viac`)
        : T(`, ${n(-units)} good units fewer`, `, o ${n(-units)} dobrých kusov menej`);
      return `<p class="tune-verdict">${T("This scenario earns", "Tento scenár zarobí")} ${what}${made} (${r.hours} h).
        ${T("It ran offline: the live plant is unchanged.", "Bežal mimo živého závodu: živý závod je nezmenený.")}</p>`;
    })();
    return `${verdict}<div class="tune-score"><div><div class="label">${T("Margin per hour", "Marža za hodinu")}</div>
        <div class="big">${money(r.margin_per_h)}</div>
        ${base && !same ? `<div>${delta(r.margin_per_h, base.margin_per_h, (v) => money(v), 1)} ${T("against the plant as it is", "oproti závodu, aký je")}</div>` : `<div class="muted">${same ? T("this is the plant as it is", "toto je závod, aký je") : ""}</div>`}</div>
        <div class="muted" style="text-align:right">${r.hours} h · ${T("knobs", "gombíky")}: ${Object.entries(r.knobs).map(([k, v]) => `${esc(T(...KNOB_NAMES[k]).split(" ")[0])} ${v}`).join(", ")}</div></div>
      <table><tr><th></th><th class="num">${T("this run", "tento beh")}</th><th class="num">${T("as it is", "ako je")}</th><th></th></tr>
        ${row(T("Good units", "Dobré kusy"), "good_units", (v) => n(Math.round(v)))}
        ${row(T("Cost of a good unit", "Náklady na dobrý kus"), "cost_per_unit", (v) => money(v, 2), -1)}
        ${row(T("… without material", "… bez materiálu"), "conversion_per_unit", (v) => money(v, 2), -1)}
        ${row(T("OEE final assembly", "OEE finálna montáž"), "oee", (v) => pct(v))}
        ${row(T("OEE SMT", "OEE SMT"), "oee_smt", (v) => pct(v))}
        ${row(T("SMT work content (lines)", "Obsah práce SMT (linky)"), "smt_work_lines", (v) => num(v, 2))}
        ${row("RTY", "rty", (v) => pct(v))}
        ${row(T("Units per person-hour", "Kusy na človekohodinu"), "units_per_person_h", (v) => num(v, 2))}
        ${row(T("Robots busy", "Roboty vyťažené"), "robot_utilization", (v) => pct(v, 0))}
        ${row(T("Loaders waiting for parts", "Nakladače čakali na diely"), "material_wait_h", (v) => `${num(v, 1)} h`, -1)}
        ${row(T("Repair time per breakdown", "Oprava na poruchu"), "mttr_min", (v) => `${num(v, 0)} min`, -1)}
        ${row(T("People", "Ľudia"), "labor", (v) => money(v), -1, (x) => x.costs && x.costs.labor)}
        ${row(T("Lines", "Linky"), "lines", (v) => money(v), -1, (x) => x.costs && x.costs.lines)}
        ${row(T("Energy", "Energia"), "energy", (v) => money(v), -1, (x) => x.costs && x.costs.energy)}
        ${row(T("Robots", "Roboty"), "robots", (v) => money(v), -1, (x) => x.costs && x.costs.robots)}
        ${row(T("Scrap", "Šrot"), "scrap", (v) => money(v), -1, (x) => x.costs && x.costs.scrap)}
      </table>
      <h3 style="margin-top:14px">${T("Lines in this run", "Linky v tomto behu")}</h3>
      <div class="tune-lines">${r.lines.map((l) => `<span title="${esc(l.main_loss ? MES.label(l.main_loss) : "")}"><b>${esc(l.code)}</b> ${l.oee === null ? `${T("loading", "vyťaž.")} ${pct(l.loading, 0)}` : `OEE ${pct(l.oee, 0)}`}</span>`).join("")}</div>`;
  }

  function boardHtml() {
    const rows = board.best || [];
    if (!rows.length) return `<tr><td class="muted">${T("Nothing run yet.", "Zatiaľ nič nebežalo.")}</td></tr>`;
    return `<tr><th>#</th><th>${T("Knobs", "Gombíky")}</th><th class="num">${T("margin / h", "marža / h")}</th><th class="num">${T("units", "ks")}</th><th class="num">${T("€ / unit", "€ / ks")}</th><th></th></tr>` +
      rows.map((r, i) => `<tr><td>${i + 1}</td><td style="font-size:12px">${Object.entries(r.knobs).map(([k, v]) => `<span class="${board.base[k] !== v ? "changed" : "faint"}">${esc(T(...KNOB_NAMES[k]).split(" ")[0])}&nbsp;${v}</span>`).join(" · ")}</td>
        <td class="num"><b>${money(r.margin_per_h)}</b></td><td class="num">${n(r.good_units)}</td><td class="num">${money(r.cost_per_unit, 2)}</td>
        <td><button type="button" class="btn small" data-load="${i}">${T("Show", "Ukázať")}</button></td></tr>`).join("");
  }

  function render() {
    renderLive();
    if (!board) return;
    document.getElementById("tune-knobs").innerHTML = knobsHtml();
    document.getElementById("tune-board").innerHTML = boardHtml();
    document.getElementById("tune-result").innerHTML = resultHtml(shown);
    const sel = document.getElementById("tune-hours");
    sel.innerHTML = board.hours.map((h) => `<option value="${h}"${h === hours ? " selected" : ""}>${h} h</option>`).join("");
  }

  function setStatus(text) { document.getElementById("tune-status").innerHTML = text; }

  // ---- the live plant: as configured, or a scenario put into it from here
  let live = null;
  const knobList = (k) => Object.entries(k).map(([name, v]) => `${esc(T(...KNOB_NAMES[name]))} <b>${v}</b>`).join(" · ");
  function diffList(k) {
    if (!board) return "";
    const changed = Object.entries(k).filter(([name, v]) => board.base[name] !== v);
    return changed.length ? changed.map(([name, v]) => `${esc(T(...KNOB_NAMES[name]))} <b>${v}</b> <span class="faint">(${T("as it is", "ako je")} ${board.base[name]})</span>`).join(" · ")
      : T("as configured", "ako je nastavený");
  }
  function liveHtml() {
    if (!live) return `<h3>${T("The live plant", "Živý závod")}</h3>`;
    const now = live.live.knobs;
    const pending = live.pending;
    const head = now ? `${T("runs a scenario", "beží podľa scenára")}: ${diffList(now)}` : T("runs as configured (no scenario)", "beží tak, ako je nastavený (bez scenára)");
    const wait = pending ? `<p class="tune-verdict">${T("Changing: the plant starts a new run with", "Mení sa: závod začína nový beh s")} ${live.wanted.knobs ? diffList(live.wanted.knobs) : T("the plant as configured", "závodom, aký je nastavený")} &hellip;</p>` : "";
    const can = window.MES_CAN_ACT;
    return `<h3>${T("The live plant", "Živý závod")}</h3><p>${head}</p>${wait}
      <p class="muted" style="font-size:12.5px">${T("The live plant is what the Floor and the other pages show. Simulate runs a copy offline; Use in the live plant puts the knobs into the live plant (a new run, the shop floor starts empty).",
        "Živý závod je to, čo ukazuje Floor a ostatné stránky. Prepočítať spustí kópiu mimo neho; Použiť v živom závode dá gombíky do živého závodu (nový beh, dielňa začne prázdna).")}</p>
      ${now ? `<div class="tune-run"><button type="button" class="btn" id="tune-live-reset"${can ? "" : " disabled"}>${T("Back to the plant as it is", "Späť na závod, aký je")}</button>
        <a class="btn" href="/">${T("Look at it on the Floor", "Pozrieť na Floor")}</a></div>` : ""}
      ${can ? "" : `<p class="muted" style="font-size:12.5px">${T("Sign in to change the live plant.", "Na zmenu živého závodu sa prihlás.")}</p>`}`;
  }
  function renderLive() {
    document.getElementById("tune-live").innerHTML = liveHtml();
    const go = document.getElementById("tune-live-go");
    if (go) {
      go.disabled = !window.MES_CAN_ACT || !knobs;
      go.title = window.MES_CAN_ACT ? T("The live plant starts a new run with these knobs", "Živý závod začne nový beh s týmito gombíkmi") : T("Sign in first", "Najprv sa prihlás");
    }
  }
  async function loadLive() {
    try {
      const r = await fetch("/api/v1/plant/scenario", { cache: "no-store" });
      if (r.ok) { live = await r.json(); renderLive(); }
    } catch (e) { /* the next try follows */ }
    setTimeout(loadLive, live && live.pending ? 2000 : 8000);
  }
  async function putLive(k) {
    try {
      live = await MES.post("/api/v1/plant/scenario", { knobs: k });
      renderLive();
      setStatus(k ? T("The live plant starts a new run with these knobs in a moment. Look at it on the Floor.", "Živý závod o chvíľu začne nový beh s týmito gombíkmi. Pozri sa naň na Floor.")
        : T("The live plant goes back to the plant as configured in a moment.", "Živý závod sa o chvíľu vráti na závod, aký je nastavený."));
    } catch (e) { setStatus(`<span style="color:var(--s-down)">${esc(e.message)}</span>`); }
  }

  async function loadBoard() {
    const r = await fetch(`/api/v1/tuning?hours=${hours}`, { cache: "no-store" });
    if (!r.ok) return;
    board = await r.json();
    if (!knobs) knobs = { ...board.base };
    if (!shown && board.baseline) shown = board.baseline;
    render();
    if (!board.baseline && !job) run({ ...board.base }, true);          // the reference first
  }

  async function run(k, quiet = false) {
    try {
      const status = await MES.post("/api/v1/tuning/runs", { knobs: k, hours });
      follow(status, quiet);
    } catch (e) { setStatus(`<span style="color:var(--s-down)">${esc(e.message)}</span>`); }
  }

  function follow(status, quiet) {
    if (status.state === "done") {
      job = null;
      shown = status.result;
      setStatus(quiet ? "" : T("Done: the result is on the left (the live plant is unchanged).", "Hotovo: výsledok je vľavo (živý závod je nezmenený)."));
      loadBoard();
      if (!quiet) document.getElementById("tune-result").scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (status.state === "failed") { job = null; setStatus(`<span style="color:var(--s-down)">${esc(status.error || "failed")}</span>`); return; }
    job = status.id;
    const what = status.state === "queued" ? T(`waiting, ${status.ahead} ahead`, `čaká, pred ním ${status.ahead}`)
      : `${T("the plant runs", "závod beží")} · ${Math.round((status.progress || 0) * 100)} %`;
    setStatus(`<div class="progress" style="margin:8px 0"><div style="width:${Math.round((status.progress || 0) * 100)}%"></div></div>${what}${quiet ? ` · ${T("the plant as it is, for comparison", "závod, aký je, na porovnanie")}` : ""}`);
    setTimeout(async () => {
      try {
        const r = await fetch(`/api/v1/tuning/runs/${status.id}`, { cache: "no-store" });
        if (r.ok) follow(await r.json(), quiet);
      } catch (e) { setTimeout(() => follow(status, quiet), 3000); }
    }, 1500);
  }

  document.addEventListener("click", (e) => {
    const step = e.target.closest("[data-k]");
    if (step && knobs) {
      const k = step.dataset.k, spec = board.knobs[k];
      knobs[k] = Math.max(spec.min, Math.min(spec.max, knobs[k] + Number(step.dataset.d)));
      render();
      return;
    }
    const load = e.target.closest("[data-load]");
    if (load && board) {
      const r = board.best[+load.dataset.load];
      knobs = { ...r.knobs }; hours = r.hours; shown = r;
      render();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  });
  document.getElementById("tune-go").addEventListener("click", () => { if (knobs && !job) run({ ...knobs }); });
  document.getElementById("tune-live-go").addEventListener("click", () => { if (knobs) putLive({ ...knobs }); });
  document.addEventListener("click", (e) => { if (e.target.closest("#tune-live-reset")) putLive(null); });
  document.getElementById("tune-reset").addEventListener("click", () => { if (board) { knobs = { ...board.base }; render(); } });
  document.getElementById("tune-hours").addEventListener("change", (e) => { hours = +e.target.value; shown = null; loadBoard(); });
  document.addEventListener("mes-lang", render);
  loadBoard();
  loadLive();
})();
