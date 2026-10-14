/* The andon page: open calls with their level, the escalations of the last moments, the escalated
   breakdowns of the last day. */
(() => {
  const { esc, n } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const LV = { 1: ["technician", "technik"], 2: ["foreman", "majster"], 3: ["production manager", "vedúci výroby"] };
  const COLOR = { 1: "#4a8fd8", 2: "#f1a33c", 3: "#e0453a" };
  let last = null;

  const level = (lv) => `<span class="pill" style="--st:${COLOR[lv]}">${lv} · ${esc(T(...LV[lv]))}</span>`;
  const hm = (iso) => (iso ? iso.slice(11, 16) : "–");
  const mins = (iso) => {
    const s = MES.since(iso);
    return s === null ? "–" : MES.duration(s);
  };
  const phase = (c) => ({ wait: T("waiting for somebody free", "čaká na voľného"), walk: T("on the way", "ide"), fix: T("being repaired", "opravuje sa") })[c.phase] || c.phase;

  function render(d) {
    last = d;
    const open = d.open.slice().sort((a, b) => b.level - a.level);
    document.getElementById("andon-open").innerHTML = open.length ? `<table>
      <tr><th>${T("Machine", "Stroj")}</th><th>${T("Reason", "Dôvod")}</th><th>${T("Now", "Teraz")}</th><th class="num">${T("open for", "otvorené")}</th><th>${T("Level", "Úroveň")}</th></tr>
      ${open.map((c) => `<tr><td><b>${esc(c.station)}</b><div class="faint">${esc(c.name || "")}</div></td><td>${c.kind === "request" ? T("called from the MES", "privolaný z MES") : esc(c.reason)}</td>
        <td>${esc(phase(c))}</td><td class="num">${esc(mins(c.since))}</td><td>${c.kind === "repair" ? level(c.level) : `<span class="faint">${T("request", "privolanie")}</span>`}</td></tr>`).join("")}</table>`
      : `<p class="muted">${T("No open call: every machine runs or is looked after.", "Žiadne otvorené volanie: všetky stroje bežia alebo sa o ne niekto stará.")}</p>`;

    const c = d.counts;
    document.getElementById("andon-counts").innerHTML = `<h3>${T("Breakdowns · last 24 h", "Poruchy · posledných 24 h")}</h3>
      <div class="kpi-row" style="display:flex;gap:18px;flex-wrap:wrap">
        ${[1, 2, 3].map((lv) => `<div><div class="muted" style="font-size:12px">${lv === 1 ? T("solved by the technician", "vyriešil technik") : lv === 2 ? T(`reached the foreman (${d.rule.foreman_min} min)`, `došli k majstrovi (${d.rule.foreman_min} min)`) : T(`reached the manager (${d.rule.manager_min} min)`, `došli k vedúcemu (${d.rule.manager_min} min)`)}</div>
          <div style="font-size:28px;font-weight:700;color:${COLOR[lv]}">${n(c[lv])}</div></div>`).join("")}
      </div>
      ${d.meeting ? `<p class="tune-verdict" style="margin-top:10px">${T("The shift meeting is on now, in front of the andon board in the production office.", "Práve prebieha porada zmeny pred andon tabuľou v kancelárii výroby.")}</p>` : ""}`;

    document.getElementById("andon-esc").innerHTML = d.escalations.length ? `<table>
      <tr><th>${T("When", "Kedy")}</th><th>${T("Machine", "Stroj")}</th><th>${T("Level", "Úroveň")}</th><th>${T("Who", "Kto")}</th></tr>
      ${d.escalations.map((e) => `<tr><td>${hm(e.at)}</td><td><b>${esc(e.station)}</b></td><td>${level(e.level)}</td>
        <td>${e.person ? esc(e.person) + ` · ${T("came to the machine", "prišiel k stroju")}` : T("called on the phone (not on site)", "telefonicky (nie je v závode)")}</td></tr>`).join("")}</table>`
      : `<p class="muted">${T("None since the plant last started.", "Žiadne od posledného spustenia závodu.")}</p>`;

    document.getElementById("andon-history").innerHTML = d.history.length ? `<table>
      <tr><th>${T("Machine", "Stroj")}</th><th>${T("Reason", "Dôvod")}</th><th>${T("From", "Od")}</th><th class="num">${T("minutes", "minút")}</th><th>${T("Level", "Úroveň")}</th></tr>
      ${d.history.map((h) => `<tr><td><b>${esc(h.station)}</b></td><td>${esc(h.reason || "")}</td><td>${hm(h.start)}${h.open ? ` <span class="faint">${T("still open", "stále otvorené")}</span>` : ""}</td>
        <td class="num">${Math.round(h.minutes)}</td><td>${level(h.level)}</td></tr>`).join("")}</table>`
      : `<p class="muted">${T("No breakdown lasted long enough to escalate.", "Žiadna porucha netrvala tak dlho, aby eskalovala.")}</p>`;
  }

  document.addEventListener("mes-lang", () => { if (last) render(last); });
  MES.poll("/api/v1/andon", render, 5000);
})();
