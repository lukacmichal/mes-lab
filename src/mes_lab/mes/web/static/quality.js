(() => {
  const { esc, n, upill, dt, post } = MES;
  const link = (serial) => `<a class="mono" href="/trace?q=${encodeURIComponent(serial)}">${esc(serial)}</a>`;
  const kpi = (label, value, alert) => `<div class="kpi${alert ? " alert" : ""}"><div class="label">${esc(label)}</div>
    <div class="value">${value}</div></div>`;

  function render(d) {
    const k = d.kpis;
    document.getElementById("kpis").innerHTML = [
      kpi("Pieces with open defects", n(k.open_units)),
      kpi("In quarantine", n(k.quarantine), k.quarantine > 0),
      kpi("Repaired · 24 h", n(k.repaired_24h)),
      kpi("Scrapped · 24 h", n(k.scrapped_24h)),
    ].join("");

    document.getElementById("quarantine").innerHTML = d.quarantine.length ? `<table>
      <tr><th>Serial</th><th>Material</th><th>Why</th><th>Since</th><th></th></tr>
      ${d.quarantine.map((q) => `<tr><td>${link(q.serial)}<div class="faint">${esc(q.kind)}</div></td>
        <td>${esc(q.material)}</td><td>${esc(q.reason || "")}<div class="faint">${esc(q.location || "")}</div></td>
        <td class="mono">${esc(dt(q.since))}</td>
        <td style="white-space:nowrap">${MES.can("quality") ? `<button class="btn small" data-serial="${esc(q.serial)}" data-act="RETURN">Release</button>
          <button class="btn small danger" data-serial="${esc(q.serial)}" data-act="SCRAP">Scrap</button>` : ""}</td></tr>`).join("")}
      </table>` : `<p class="empty">Nothing in quarantine.</p>`;

    document.getElementById("open").innerHTML = d.open.length ? `<table>
      <tr><th>Serial</th><th>Defect</th><th>Found at</th><th>When</th></tr>
      ${d.open.map((o) => `<tr><td>${link(o.serial)}<div class="faint">${esc(o.kind)} · ${esc(o.location || "")}</div></td>
        <td>${esc(o.name)}<div class="faint mono">${esc(o.code)}</div></td><td>${esc(o.station)}</td>
        <td class="mono">${esc(dt(o.found_at))}</td></tr>`).join("")}</table>`
      : `<p class="empty">No open defects.</p>`;

    const max = Math.max(1, ...d.pareto.map((p) => p.count));
    document.getElementById("pareto").innerHTML = d.pareto.length ? d.pareto.slice(0, 14).map((p) => `
      <div class="bar-row" title="${esc(p.code)}"><span>${esc(p.name)}</span>
        <div class="track"><div class="fill low" style="width:${(100 * p.count) / max}%"></div></div>
        <span class="num">${n(p.count)}</span></div>`).join("") : `<p class="empty">No defects in the last 24 hours.</p>`;

    document.getElementById("repairs").innerHTML = d.repairs.length ? `<table>
      <tr><th>When</th><th>Serial</th><th>Bench</th><th>Defects</th><th>Result</th></tr>
      ${d.repairs.map((r) => `<tr><td class="mono">${esc(dt(r.at))}</td><td>${link(r.serial)}</td>
        <td>${esc(r.station.split("-").pop())}</td><td class="mono" style="font-size:12px">${esc(r.defects || "")}</td>
        <td>${upill(r.result)}</td></tr>`).join("")}</table>` : `<p class="empty">No repairs yet.</p>`;
  }

  document.getElementById("quarantine").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    b.disabled = true;
    try {
      await post(`/api/v1/quarantine/${encodeURIComponent(b.dataset.serial)}/disposition`, { action: b.dataset.act });
      b.closest("tr").remove();
    } catch (err) {
      alert(err.message);
      b.disabled = false;
    }
  });

  MES.poll("/api/v1/quality", render, 4000);

  // the master sample checks of the testers
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  let checks = null;
  function renderChecks() {
    if (!checks) return;
    const rows = checks.testers.map((v) => `<tr><td><b>${MES.esc(v.station)}</b><div class="faint">${MES.esc(v.name)}</div></td>
      <td>${v.valid ? `<span class="pill st-RUNNING">${T("valid this shift", "platné v tejto zmene")}</span>` : `<span class="pill st-MAINTENANCE">${T("check due", "čaká na overenie")}</span>`}</td>
      <td>${v.last ? `${MES.esc(v.last.at.slice(11, 16))} · ${v.last.result === "PASS" ? "PASS" : `<b style="color:var(--s-down)">FAIL</b>`}${v.last.person ? ` · ${MES.esc(v.last.person)}` : ""}` : "–"}</td>
      <td class="num">${v.fails_24h ? `<b style="color:var(--s-down)">${v.fails_24h}</b>` : "0"}</td></tr>`).join("");
    document.getElementById("verifications").innerHTML = `<table><tr><th>${T("Tester", "Tester")}</th><th></th><th>${T("Last check", "Posledné overenie")}</th><th class="num">${T("failed 24 h", "zlyhalo 24 h")}</th></tr>${rows}</table>`;
  }
  document.addEventListener("mes-lang", renderChecks);
  MES.poll("/api/v1/verifications", (d) => { checks = d; renderChecks(); }, 10000);
})();
