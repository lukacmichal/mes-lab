(() => {
  const { esc, n, pill, label, STATES, duration, since, hm } = MES;

  const legend = () => {
    document.getElementById("legend").innerHTML = STATES.map((s) =>
      `<span class="st-${s}" title="${MES.esc(MES.tr((window.MES_TEXT || {}).states?.[s]))}"><i></i>${label(s)}</span>`).join("");
  };
  legend();
  document.addEventListener("mes-lang", legend);

  function buffer(level, capacity, unbounded) {
    if (unbounded) {
      return `<div class="buffer"><span class="n">${n(level)}</span><span class="arrow">→</span><span class="n">waiting</span></div>`;
    }
    const slots = Array.from({ length: capacity }, (_, i) =>
      `<i class="${i < capacity - (level || 0) ? "" : "full"}"></i>`).join("");
    return `<div class="buffer" title="${level ?? "?"} of ${capacity} conveyor places used">
      <div class="slots">${slots}</div><span class="arrow">→</span></div>`;
  }

  function station(s, isInspection) {
    const c = s.shift || {};
    const counts = `<b>${n(c.processed)}</b> done` +
      (c.bad ? ` · <span class="bad">${n(c.bad)} NOK</span>` : "") +
      (isInspection && c.false_calls ? ` · ${n(c.false_calls)} FC` : "");
    return `<div class="station st-${esc(s.state)}" data-station="${esc(s.code)}" tabindex="0">
      <div class="tag">${esc(s.tag)}<small>${esc(duration(since(s.since)))}</small></div>
      <div class="sname">${esc(s.name)}</div>
      <div class="state">${esc(label(s.state))}</div>
      <div class="reason">${esc(s.reason_name || "")}</div>
      <div class="item">${esc(s.item || "")}</div>
      <div class="counts">${counts}</div>
    </div>`;
  }

  function renderFlow(d) {
    const cap = d.line.buffer_capacity;
    const html = d.cells.map((cell, i) => {
      let before = "";
      if (i > 0) before = buffer(cell.buffer, cap, false);
      else if (!d.line.job_line) before = buffer(cell.buffer, cap, true);
      const insp = cell.stations[0] && cell.stations[0].kind === "inspection";
      return `${before}<div class="cell">${cell.stations.map((s) => station(s, insp)).join("")}</div>`;
    }).join("");
    document.getElementById("flow").innerHTML = html;
  }

  function renderHourly(d) {
    document.getElementById("unit").textContent = `good ${d.line.output_unit}`;
    const max = Math.max(1, ...d.hourly.map((h) => Math.max(h.good, h.plan || 0)));
    const tone = (h) => (!h.plan ? "" : h.good >= 0.95 * h.plan ? " on" : h.good >= 0.85 * h.plan ? " close" : " behind");
    document.getElementById("hourly").innerHTML = d.hourly.map((h) => `
      <div class="col${h.current ? " current" : ""}${tone(h)}" title="${esc(h.label)}: ${h.good} good, ${h.bad} NOK${h.plan !== null ? `, plan ${h.plan}` : ""}">
        <span class="v">${h.good ? n(h.good) : ""}${h.plan ? `<span class="p">/${n(h.plan)}</span>` : ""}</span>
        <div class="bars">${h.plan ? `<div class="pl" style="height:${(100 * h.plan) / max}%"></div>` : ""}<div class="b" style="height:${(100 * h.good) / max}%"></div></div>
        <span class="l">${esc(h.label)}</span>
      </div>`).join("");
  }

  function renderDist(d) {
    const rows = d.cells.flatMap((c) => c.stations).map((s) => {
      const total = Object.values(s.states).reduce((a, b) => a + b, 0) || 1;
      const parts = STATES.filter((st) => s.states[st]).map((st) =>
        `<i class="st-${st}" style="width:${(100 * s.states[st]) / total}%" title="${label(st)} ${duration(s.states[st])} (${Math.round((100 * s.states[st]) / total)}%)"></i>`).join("");
      return `<div class="row"><span>${esc(s.tag)}</span><div class="stack-bar">${parts}</div></div>`;
    }).join("");
    document.getElementById("dist").innerHTML = rows || `<p class="empty">No data in this shift yet.</p>`;
  }

  function renderDefects(d) {
    document.getElementById("defects").innerHTML = d.defects.length ? `<table>
      <tr><th>Defect</th><th class="num">Found</th></tr>
      ${d.defects.map((x) => `<tr><td>${esc(x.name)}<div class="faint mono">${esc(x.code)}</div></td>
        <td class="num">${n(x.found)}</td></tr>`).join("")}</table>`
      : `<p class="empty">No defects found in this shift.</p>`;
  }

  function renderJobs(d) {
    const rows = [...d.jobs.active, ...d.jobs.queued];
    document.getElementById("jobs").innerHTML = rows.length ? `<table>
      <tr><th>Order</th><th>Material</th><th>Operation</th><th>Status</th>
        <th class="num">Released</th><th class="num">Good</th><th class="num">Scrap</th><th class="num">Repair</th></tr>
      ${rows.map((j) => `<tr>
        <td class="mono">${esc(j.order_no)}${j.id === d.loading_job_id ? ' <span class="chip">loading</span>' : ""}</td>
        <td>${esc(j.material)}</td><td>${esc(j.operation_name)}</td>
        <td>${pill(j.status === "ACTIVE" ? "RUNNING" : "NO_ORDER", j.status)}</td>
        <td class="num">${n(j.released)} / ${n(j.qty)}</td>
        <td class="num">${n(j.good)} / ${n(j.expected)} <span class="faint">${esc(j.piece)}</span></td>
        <td class="num">${n(j.scrap)}</td><td class="num">${n(j.in_repair)}</td></tr>`).join("")}</table>`
      : `<p class="empty">${d.line.job_line ? "No jobs. The line asks the MES for work every few minutes." : "Flow line – it works on whatever arrives, no jobs."}</p>`;
  }

  function renderEvents(d) {
    document.getElementById("events").innerHTML = d.events.length ? `<table>
      <tr><th>Start</th><th>Station</th><th>Type</th><th>Reason</th><th class="num">Duration</th></tr>
      ${d.events.map((e) => `<tr><td class="mono">${esc(hm(e.start))}</td>
        <td>${esc(e.station.split("-").pop())}</td><td>${pill(e.state)}</td>
        <td>${esc(e.reason_name || e.reason || "")}</td>
        <td class="num">${e.end ? esc(duration(e.duration_s)) : `<b>${esc(duration(since(e.start)))}</b> ongoing`}</td></tr>`).join("")}</table>`
      : `<p class="empty">No breakdowns, changeovers or maintenance recorded yet.</p>`;
  }

  function render(d) {
    const all = {};
    d.cells.forEach((c) => c.stations.forEach((s) => { all[s.code] = s; }));
    const jobs = {};
    [...d.jobs.active, ...d.jobs.queued].forEach((j) => { jobs[j.id] = j; });
    StationView.update(all, jobs);
    document.getElementById("line-status").innerHTML = pill(d.status);
    document.getElementById("line-detail").textContent = d.detail || "";
    document.getElementById("wip").textContent = d.wip !== null && d.wip !== undefined ? `WIP on line: ${d.wip}` : "";
    renderFlow(d);
    renderHourly(d);
    renderDist(d);
    renderDefects(d);
    renderJobs(d);
    renderEvents(d);
  }

  Floor.start().then(() => {
    const canvas = document.getElementById("line-canvas");
    if (!canvas) return;
    const stage = document.getElementById("line-stage");
    if (!Floor.layout.lines[LINE_CODE]) {                  // not in the live plant (a tuning scenario without it)
      stage.innerHTML = `<p class="muted" style="padding:16px">${MES.lang() === "sk" ? "Táto linka nie je v živom závode: beží scenár z ladenia bez nej (stránka Tuning)."
        : "This line is not in the live plant: it runs a tuning scenario without it (Tuning page)."}</p>`;
      stage.style.height = "auto";
      return;
    }
    const band = FloorView.prototype.lineBand(LINE_CODE);
    const size = () => { stage.style.height = Math.round(Math.min(Math.max(stage.clientWidth * band.h / band.w + 4, 150), 420)) + "px"; };
    size();
    window.addEventListener("resize", size);
    window.lineView = new FloorView(canvas, {
      line: LINE_CODE,
      onSelect: (hit, view, e) => { if (hit && hit.station) StationView.openAt(hit.station, e.clientX, e.clientY + 12); else StationView.close(); },
    });
  });

  MES.poll(`/api/v1/lines/${encodeURIComponent(LINE_CODE)}`, render, 2000);
})();
