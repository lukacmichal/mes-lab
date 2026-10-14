(() => {
  const { esc, n, upill, dt, post, tr } = MES;
  const input = document.getElementById("q");
  const out = document.getElementById("result");
  const link = (serial) => `<a class="mono" href="/trace?q=${encodeURIComponent(serial)}">${esc(serial)}</a>`;
  const statusPill = (s) => `<span title="${esc(tr((window.MES_TEXT || {}).units?.[s]))}">${upill(s)}</span>`;

  function tree(node, root = true) {
    if (!node.children || !node.children.length) return "";
    return `<ul${root ? ' class="tree"' : ""}>${node.children.map((c) => `<li><span class="kind">${esc(c.kind)}</span>${link(c.serial)}
      ${upill(c.status)} <span class="link">${esc(c.material)} · ${esc(c.link || "")}</span>${tree(c, false)}</li>`).join("")}</ul>`;
  }

  function measurements(m) {
    const keys = Object.keys(m || {});
    return keys.length ? `<span class="meas">${keys.map((k) => `${esc(k)}=${esc(m[k])}`).join(" · ")}</span>` : "";
  }

  function renderUnit(d) {
    const u = d.unit;
    const steps = (u.steps || []).map((s) => `<span class="chip ${u.completed.includes(s) ? "done" : ""}">${esc(s)}</span>`).join("");
    const holds = d.holds.length ? `<div class="warn-box"><b>On hold.</b> ${d.holds.map((h) =>
      `#${h.id} ${esc(h.kind)} ${esc(h.target)}: ${esc(h.reason)}`).join("<br>")}</div>` : "";
    const quarantine = u.status === "QUARANTINE" ? `<div class="warn-box"><b>In quarantine:</b> ${esc(u.quarantine_reason || "")}
      ${window.MES_CAN_ACT ? `<div style="margin-top:8px;display:flex;gap:8px">
        <button class="btn small" data-act="RETURN">Release to production</button>
        <button class="btn small danger" data-act="SCRAP">Scrap</button></div>` : ""}</div>` : "";
    const history = d.history.length ? `<table>
      <tr><th>Time</th><th>Serial</th><th>Station</th><th>Step</th><th>Result</th><th>Details</th></tr>
      ${d.history.map((r) => `<tr><td class="mono">${esc(dt(r.end))}</td>
        <td>${r.serial === u.serial ? '<span class="faint">this</span>' : link(r.serial)}</td>
        <td><a href="/lines/${encodeURIComponent(r.station.split("-").slice(0, 2).join("-"))}">${esc(r.station)}</a></td>
        <td>${esc(r.step)}</td><td>${upill(r.result)}</td>
        <td>${r.action ? `<span class="chip">${esc(r.action)}</span> ` : ""}${measurements(r.measurements)}</td></tr>`).join("")}
      </table>` : `<p class="empty">No operations recorded yet.</p>`;
    const defects = d.defects.length ? `<table>
      <tr><th>Found</th><th>Serial</th><th>Defect</th><th>Station</th><th>Status</th><th>Closed</th></tr>
      ${d.defects.map((x) => `<tr><td class="mono">${esc(dt(x.found_at))}</td><td>${link(x.serial)}</td>
        <td>${esc(x.name)}<div class="faint mono">${esc(x.code)}</div></td><td>${esc(x.station)}</td>
        <td>${upill(x.status === "OPEN" ? "OPEN_DEFECT" : x.status, x.status)}</td><td class="mono">${esc(dt(x.closed_at))}</td></tr>`).join("")}
      </table>` : `<p class="empty">No defects.</p>`;

    out.innerHTML = `
      <div class="panel" style="margin-bottom:16px">
        <div class="unit-head"><span class="serial">${esc(u.serial)}</span><span class="chip">${esc(u.kind)}</span>
          ${statusPill(u.status)}<span class="muted">${esc(u.material)} · ${esc(u.material_name)}</span>
          <span class="spacer"></span>
          ${window.MES_CAN_ACT ? `<a class="btn small" href="/holds?serial=${encodeURIComponent(u.serial)}">Put on hold…</a>` : ""}</div>
        <div class="facts">
          <div><span>Location</span>${esc(u.location || "–")}</div>
          <div><span>Last station</span>${esc(u.station || "–")}</div>
          <div><span>Work order</span>${u.work_order ? link(u.work_order) : "–"}</div>
          <div><span>Created</span>${esc(dt(u.created_at))}</div>
          <div><span>Last change</span>${esc(dt(u.updated_at))}</div>
          ${u.qty ? `<div><span>Pieces inside</span>${n(u.qty)}</div>` : ""}
        </div>
        ${steps ? `<div class="steps"><span class="faint" style="margin-right:6px">Routing ${esc(u.operation || "")}:</span>${steps}</div>` : ""}
        ${holds}${quarantine}
      </div>
      <div class="grid-2" style="grid-template-columns:1fr 1fr;margin-bottom:16px">
        <div class="panel"><h3>Made from</h3>${tree(d.up) || '<p class="empty">Nothing – this is where it started.</p>'}</div>
        <div class="panel"><h3>Went into</h3>${tree(d.down) || '<p class="empty">Not used further yet.</p>'}</div>
      </div>
      ${d.truncated ? '<p class="faint">The trees are cut after 400 pieces.</p>' : ""}
      <div class="panel" style="margin-bottom:16px"><h3>History</h3>${history}</div>
      <div class="panel"><h3>Defects</h3>${defects}</div>`;

    out.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
      try {
        await post(`/api/v1/quarantine/${encodeURIComponent(u.serial)}/disposition`, { action: b.dataset.act });
        load(u.serial);
      } catch (e) { alert(e.message); }
    }));
  }

  function renderList(title, rows, extra = "") {
    out.innerHTML = `<div class="panel"><h3>${esc(title)}</h3>${extra}${rows.length ? `<table>
      <tr><th>Serial</th><th>Kind</th><th>Status</th><th>Location</th></tr>
      ${rows.map((r) => `<tr><td>${link(r.serial)}</td><td>${esc(r.kind)}</td><td>${statusPill(r.status)}</td>
        <td>${esc(r.location || "")}</td></tr>`).join("")}</table>` : '<p class="empty">Nothing found.</p>'}</div>`;
  }

  async function load(q) {
    q = (q || "").trim();
    if (!q) return;
    input.value = q;
    history.replaceState(null, "", `/trace?q=${encodeURIComponent(q)}`);
    out.innerHTML = `<p class="empty">Searching…</p>`;
    const s = await fetch(`/api/v1/trace/search?q=${encodeURIComponent(q)}`).then((r) => r.json());
    if (s.kind === "unit") {
      const r = await fetch(`/api/v1/trace/units/${encodeURIComponent(s.serial)}`);
      const d = await r.json();
      MES.setClock(d.clock);
      renderUnit(d);
    } else if (s.kind === "order") {
      renderList(`Work order ${s.order_no} · ${s.material}`, s.units,
        `<p class="muted">${n(s.total)} serial numbers belong to this order${s.total > s.units.length ? `, first ${s.units.length} shown` : ""}.</p>`);
    } else if (s.kind === "matches") {
      renderList(`Serial numbers starting with ${s.query}`, s.matches);
    }
  }

  document.getElementById("trace-form").addEventListener("submit", (e) => { e.preventDefault(); load(input.value); });
  load(new URLSearchParams(location.search).get("q"));
  MES.poll("/api/v1/clock", () => {}, 5000);
})();
