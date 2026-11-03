/* The people page: qualification matrix, today's attendance and cover, certificates that run out,
   bench strength per skill and shift. */
(() => {
  const { esc, n } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const SKILL = {
    SMT: ["SMT lines", "linky SMT"], FA: ["final assembly", "finálna montáž"], INSPECTION: ["visual inspection", "vizuálna kontrola"],
    PACK: ["packing", "balenie"], REPAIR: ["repair (IPC)", "opravy (IPC)"],
  };
  const STATUS = { PRESENT: ["present", "prítomný"], SICK: ["sick", "chorý"], HOLIDAY: ["on holiday", "dovolenka"],
    TRAINING: ["retraining", "preškolenie"] };
  const ROLE = { OPERATOR: ["operator", "operátor"], MAINTENANCE: ["maintenance", "údržba"], IT: ["IT", "IT"], FOREMAN: ["foreman", "majster"],
    LOGISTICS: ["logistics", "sklad"], MANAGER: ["manager", "vedúci"] };
  let data = null;
  let view = "now";
  try { view = MES.store.get("mes-people-shift") || "now"; } catch (e) { view = "now"; }

  const canEdit = MES.can("people");
  const ACTION = { GRANT: ["granted", "udelená"], RENEW: ["renewed", "obnovená"], REVOKE: ["taken away", "odobratá"] };
  const cell = (c, p, skill) => {
    const data = canEdit && p.role === "OPERATOR" ? ` data-q="${p.id}" data-skill="${esc(skill)}" style="cursor:pointer"` : "";
    if (!c) return `<td class="q"${data} title="${esc(data ? T("not qualified · click to grant", "bez kvalifikácie · klik: udeliť") : "")}"></td>`;
    const color = c.state === "ok" ? "var(--s-running)" : c.state === "soon" ? "#f1a33c" : "var(--s-down)";
    let title = c.until ? `${T("valid until", "platné do")} ${c.until.slice(0, 10)}` : T("no expiry", "bez platnosti");
    if (c.state === "ending") title = `${T("taken away from", "odobratá od")} ${MES.dt(c.until)}: ${c.note || ""}`;
    if (c.state === "revoked") title = `${T("taken away", "odobratá")}: ${c.note || ""}`;
    const dot = c.state === "ending" ? `<span class="dotq" style="background:var(--s-running);box-shadow:0 0 0 2px var(--s-down)"></span>`
      : c.state === "revoked" ? `<span class="faint" style="font-weight:700">×</span>` : `<span class="dotq" style="background:${color}"></span>`;
    return `<td class="q"${data} title="${esc(title)}">${dot}</td>`;
  };

  function render() {
    if (!data) return;
    const d = data;
    const now = d.clock.shift.code;
    const shift = view === "now" ? now : view;
    document.querySelectorAll("#people-shift button").forEach((b) => b.classList.toggle("on", b.dataset.s === view));
    const t = d.today;
    document.getElementById("people-kpis").innerHTML = [
      [T("Present this shift", "Prítomní v tejto zmene"), t.present], [T("Sick", "Chorí"), t.sick], [T("On holiday", "Na dovolenke"), t.holiday],
      [T("Seats empty", "Prázdne miesta"), t.uncovered.length], [T("Certificates running out", "Certifikáty vypršia"), d.expiring.filter((x) => x.state === "soon").length],
    ].map(([k, v], i) => `<div class="kpi"><div class="label">${esc(k)}</div><div class="value"${i === 3 && v ? ' style="color:var(--s-down)"' : ""}>${n(v)}</div></div>`).join("");

    const ops = d.people.filter((p) => p.role === "OPERATOR" && p.shift === shift);
    const head = `<tr><th>${T("Operator", "Operátor")}</th>${d.skills.map((s) => `<th class="q" title="${esc(T(...(SKILL[s] || [s, s])))}">${esc(s)}</th>`).join("")}<th>${T("Seat", "Miesto")}</th></tr>`;
    const rows = ops.map((p) => {
      const off = p.today === "SICK" || p.today === "HOLIDAY" || p.today === "TRAINING";
      const seat = p.seat ? esc(p.seat) + (p.covers_name ? ` <span class="faint">· ${T("covers", "za")} ${esc(p.covers_name)}</span>` : "")
        : p.home ? `<span class="faint">${esc(p.home)}</span>` : `<span class="faint">${T("relief", "záskok")}</span>`;
      return `<tr${off ? ' style="opacity:.55"' : ""}><td><b>${esc(p.name)}</b>${p.today && p.today !== "PRESENT" ? ` <span class="pill st-MAINTENANCE">${esc(T(...STATUS[p.today]))}</span>` : ""}</td>
        ${d.skills.map((s) => cell(p.skills[s], p, s)).join("")}<td>${seat}</td></tr>`;
    }).join("");
    document.getElementById("people-matrix").innerHTML = `<table class="matrix">${head}${rows}</table>
      <p class="muted" style="font-size:12px">${T("Shift", "Zmena")} ${esc(shift)}${shift === now ? ` · ${T("now", "teraz")}` : ""} · ${ops.length} ${T("operators", "operátorov")}. ${T("Green valid, amber runs out within 30 days, red ran out, a red ring is taken away from the next shift, × taken away.", "Zelená platí, oranžová vyprší do 30 dní, červená vypršala, červený krúžok je odobratá od ďalšej zmeny, × odobratá.")}
      ${canEdit ? T("Click a cell to grant, renew or take away a qualification.", "Klik na bunku: udeliť, obnoviť alebo odobrať kvalifikáciu.") : T("Sign in as a supervisor, quality or an administrator to change the qualifications.", "Na zmenu kvalifikácií sa prihlás ako majster, kvalita alebo administrátor.")}</p>`;

    const h = d.history || [];
    document.getElementById("people-history").innerHTML = h.length ? `<table>
      <tr><th>${T("When", "Kedy")}</th><th>${T("Person", "Osoba")}</th><th>${T("Skill", "Kvalifikácia")}</th><th>${T("Decision", "Rozhodnutie")}</th><th>${T("Why", "Prečo")}</th><th>${T("Who", "Kto")}</th></tr>
      ${h.map((x) => `<tr><td class="mono">${esc(MES.dt(x.at))}</td><td><b>${esc(x.name)}</b> <span class="faint">${esc(x.shift)}</span></td><td>${esc(x.skill)}</td>
        <td>${esc(T(...ACTION[x.action]))}${x.action === "REVOKE" ? ` <span class="faint">${T("from", "od")} ${esc(MES.dt(x.effective_at))}</span>` : x.until ? ` <span class="faint">${T("valid until", "platí do")} ${esc(x.until.slice(0, 10))}</span>` : ""}</td>
        <td>${esc(x.reason)}</td><td>${esc(x.by)}</td></tr>`).join("")}</table>`
      : `<p class="muted">${T("No decisions yet: the qualifications are as the plant started with them.", "Zatiaľ žiadne rozhodnutia: kvalifikácie sú také, s akými závod začal.")}</p>`;

    const absent = d.people.filter((p) => p.shift === now && (p.today === "SICK" || p.today === "HOLIDAY" || p.today === "TRAINING"));
    const covered = d.people.filter((p) => p.shift === now && p.covers);
    document.getElementById("people-today").innerHTML = `
      ${absent.length ? `<table>${absent.map((p) => {
        const by = covered.find((c) => c.covers === p.id);
        return `<tr><td><b>${esc(p.name)}</b> <span class="faint">${esc(T(...(ROLE[p.role] || [p.role, p.role])))}</span></td><td>${esc(T(...STATUS[p.today]))}</td>
          <td>${by ? `${T("seat taken by", "miesto prevzal(a)")} <b>${esc(by.name)}</b>` : p.role !== "OPERATOR" ? ""
            : !p.home ? `<span class="faint">${T("a relief person: fewer breaks covered", "záskok: menej pokrytých prestávok")}</span>`
            : `<span style="color:var(--s-down)">${T("nobody qualified free: the seat is empty", "nikto kvalifikovaný voľný: miesto je prázdne")}</span>`}</td></tr>`;
      }).join("")}</table>` : `<p class="muted">${T("Everybody came.", "Prišli všetci.")}</p>`}
      ${t.uncovered.length ? `<p class="tune-verdict" style="margin-top:8px">${T("Seats empty for the shift", "Miesta prázdne celú zmenu")}: <b>${t.uncovered.map(esc).join(", ")}</b></p>` : ""}`;

    document.getElementById("people-expiring").innerHTML = d.expiring.length ? `<table>
      <tr><th>${T("Person", "Osoba")}</th><th>${T("Skill", "Kvalifikácia")}</th><th>${T("Valid until", "Platí do")}</th></tr>
      ${d.expiring.slice(0, 20).map((x) => `<tr><td><b>${esc(x.name)}</b> <span class="faint">${esc(x.shift)}</span></td><td>${esc(x.skill)}</td>
        <td style="color:${x.state === "expired" ? "var(--s-down)" : "#c47f17"}">${esc((x.until || "").slice(0, 10))}${x.state === "expired" ? ` · ${T("ran out: not qualified", "vypršal: nesmie")}` : ""}</td></tr>`).join("")}</table>`
      : `<p class="muted">${T("None in the next 30 days.", "Žiadny v najbližších 30 dňoch.")}</p>`;

    const shifts = Object.keys(d.bench).sort();
    document.getElementById("people-bench").innerHTML = `<table><tr><th></th>${shifts.map((s) => `<th class="num">${esc(s)}</th>`).join("")}</tr>
      ${d.skills.map((k) => `<tr><td>${esc(T(...(SKILL[k] || [k, k])))}</td>${shifts.map((s) => {
        const v = (d.bench[s] || {})[k] || 0;
        return `<td class="num"${v < 3 ? ' style="color:var(--s-down);font-weight:600"' : ""}>${v}</td>`;
      }).join("")}</tr>`).join("")}</table>`;
  }

  // ------------------------------------------------------------------ a decision about a qualification
  const dialog = document.getElementById("q-dialog");
  function openDialog(id, skill) {
    const p = data.people.find((x) => x.id === id);
    if (!p) return;
    const c = p.skills[skill];
    const r = data.rules, certified = r.certified.includes(skill);
    const holds = c && (c.state === "ok" || c.state === "soon");
    const plantNow = Date.parse(data.clock.plant_time);
    const until = new Date(plantNow + r.valid_days * 86400000).toISOString().slice(0, 10);
    const state = !c ? T("not qualified", "bez kvalifikácie")
      : c.state === "ending" ? `${T("taken away from", "odobratá od")} ${MES.dt(c.until)}`
      : c.state === "revoked" ? `${T("taken away", "odobratá")}: ${c.note || ""}`
      : c.state === "expired" ? `${T("the certificate ran out", "certifikát vypršal")} ${c.until.slice(0, 10)}`
      : c.until ? `${T("valid until", "platí do")} ${c.until.slice(0, 10)}` : T("valid, no expiry", "platí, bez obmedzenia");
    const actions = [];
    if (c && c.state === "expired" && certified) actions.push(["RENEW", T("Renew the certificate", "Obnoviť certifikát")]);
    else if (!holds && !(c && c.state === "ending")) actions.push(["GRANT", T("Grant (trained)", "Udeliť (zaškolený)")]);
    if (holds && certified) actions.push(["RENEW", T("Renew the certificate", "Obnoviť certifikát")]);
    if (holds) actions.push(["REVOKE", T("Take away", "Odobrať")]);
    const form = `<form method="dialog">
        <div class="segmented" id="q-actions">${actions.map(([a, label], i) => `<button type="button" data-a="${a}" class="${i ? "" : "on"}">${esc(label)}</button>`).join("")}</div>
        <p id="q-until-row"><label>${T("Certificate valid until", "Certifikát platí do")} <input type="date" id="q-until" value="${until}"></label></p>
        <p id="q-revoke-note" class="muted small">${T("From the start of the next shift", "Od začiatku ďalšej zmeny")} (${esc(MES.dt(r.revoke_from))}): ${T("the foreman plans the seats without it and the MES refuses the person at such a station. The shift that is on now finishes as planned.", "majster plánuje miesta bez nej a MES osobu na takej stanici odmietne. Zmena, ktorá práve beží, dobehne podľa plánu.")}</p>
        <p><label>${T("Why", "Prečo")}<br><textarea id="q-reason" rows="2" style="width:100%" placeholder="${esc(T("e.g. trained on the line, IPC-7711 course passed, escape at the final inspection", "napr. zaškolený na linke, kurz IPC-7711 absolvovaný, únik na výstupnej kontrole"))}"></textarea></label></p>
        <p class="notice error" id="q-error" hidden></p>
        <div style="display:flex;gap:8px;justify-content:flex-end"><button type="button" class="btn" data-close>${T("Cancel", "Zrušiť")}</button>
          <button type="button" class="btn primary" id="q-save">${T("Save", "Uložiť")}</button></div></form>`;
    dialog.innerHTML = `<h3 style="margin-top:0">${esc(p.name)} · ${esc(skill)}</h3>
      <p class="muted">${esc(T(...(SKILL[skill] || [skill, skill])))} · ${esc(state)}</p>
      ${actions.length ? form : `<p>${T("Taken away from the next shift; it can be granted again after that.", "Odobratá od ďalšej zmeny; znova udeliť sa dá potom.")}</p>
        <div style="text-align:right"><button type="button" class="btn" data-close>${T("Close", "Zavrieť")}</button></div>`}`;
    let action = actions.length ? actions[0][0] : null;
    const show = () => {
      const row = dialog.querySelector("#q-until-row"), note = dialog.querySelector("#q-revoke-note");
      if (row) row.hidden = !(certified && action !== "REVOKE");
      if (note) note.hidden = action !== "REVOKE";
      dialog.querySelectorAll("#q-actions button").forEach((b) => b.classList.toggle("on", b.dataset.a === action));
    };
    show();
    const bar = dialog.querySelector("#q-actions");
    if (bar) bar.addEventListener("click", (e) => {
      const b = e.target.closest("[data-a]");
      if (b) { action = b.dataset.a; show(); }
    });
    dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));
    const save = dialog.querySelector("#q-save");
    if (save) save.addEventListener("click", async () => {
      const err = dialog.querySelector("#q-error");
      try {
        await MES.post(`/api/v1/people/${id}/skills`, { skill, action, reason: dialog.querySelector("#q-reason").value,
          until: certified && action !== "REVOKE" ? dialog.querySelector("#q-until").value : null });
        dialog.close();
        data = await (await fetch("/api/v1/people", { cache: "no-store" })).json();
        render();
      } catch (e) { err.textContent = e.message; err.hidden = false; }
    });
    dialog.showModal();
  }
  document.getElementById("people-matrix").addEventListener("click", (e) => {
    const td = e.target.closest("[data-q]");
    if (td) openDialog(Number(td.dataset.q), td.dataset.skill);
  });

  document.getElementById("people-shift").addEventListener("click", (e) => {
    const b = e.target.closest("[data-s]");
    if (!b) return;
    view = b.dataset.s;
    MES.store.set("mes-people-shift", view);
    render();
  });
  document.addEventListener("mes-lang", render);
  MES.poll("/api/v1/people", (d) => { data = d; render(); }, 10000);
})();
