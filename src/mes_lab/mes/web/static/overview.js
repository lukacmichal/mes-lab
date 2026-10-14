(() => {
  const { esc, n, pill, label, STATES } = MES;

  const legend = () => {
    document.getElementById("legend").innerHTML = STATES.map((s) =>
      `<span class="st-${s}" title="${MES.esc(MES.tr((window.MES_TEXT || {}).states?.[s]))}"><i></i>${label(s)}</span>`).join("");
  };
  legend();
  document.addEventListener("mes-lang", legend);

  function kpi(labelText, value, unit = "", alert = false) {
    return `<div class="kpi${alert ? " alert" : ""}"><div class="label">${esc(labelText)}</div>
      <div class="value">${value}${unit ? `<small>${esc(unit)}</small>` : ""}</div></div>`;
  }

  function progress(job) {
    const released = job.qty ? (100 * job.released) / job.qty : 0;
    const good = job.expected ? (100 * job.good) / job.expected : 0;
    return `<div class="progress" title="released ${job.released}/${job.qty}, good ${job.good}/${job.expected}">
      <div class="released" style="width:${Math.min(100, released)}%"></div>
      <div style="width:${Math.min(100, good)}%"></div></div>`;
  }

  function lineCard(line, area) {
    const job = line.job;
    let jobHtml = "";
    if (area.kind === "SMT" || area.kind === "FA") {
      jobHtml = job
        ? `<div class="job"><span><span class="mat">${esc(job.material)}</span>
             <span class="muted">· ${esc(job.operation_name || job.operation)}</span></span>
             <span class="mono muted">${esc(job.order_no)}</span></div>
           ${progress(job)}
           <div class="job muted"><span>${n(job.good)} / ${n(job.expected)} ${esc(job.piece)}</span>
             <span>${line.queue ? `${line.queue} queued` : "queue empty"}</span></div>`
        : `<div class="job muted"><span>No active job</span><span>${line.queue ? `${line.queue} queued` : ""}</span></div>
           <div class="progress"></div><div class="job">&nbsp;</div>`;
    }
    const o = line.output;
    return `<a class="line-card st-${esc(line.status)}" href="/lines/${encodeURIComponent(line.code)}">
      <div class="top"><span class="code">${esc(line.code)}</span>${pill(line.status)}</div>
      <div class="name">${esc(line.name)}</div>
      <div class="detail">${esc(line.detail || "")}</div>
      ${jobHtml}
      <div class="figures">
        <div><b>${n(o.this_hour)}</b><span>this hour</span></div>
        <div><b>${n(o.last_hour)}</b><span>last hour</span></div>
        <div><b>${n(o.shift)}</b><span>${esc(o.unit)} / shift</span></div>
      </div>
      <div class="strip">${line.stations.map((s) =>
        `<i class="st-${esc(s.state)}" data-station="${esc(s.code)}"></i>`).join("")}</div>
    </a>`;
  }

  function render(d) {
    const all = {};
    const jobs = {};
    d.areas.forEach((a) => a.lines.forEach((l) => {
      l.stations.forEach((s) => { all[s.code] = s; });
      if (l.job) jobs[l.job.id] = l.job;
    }));
    StationView.update(all, jobs);
    const k = d.kpis;
    const repair = d.repair || {};
    document.getElementById("kpis").innerHTML = [
      kpi("Final assembly · this shift", n(k.fa_output_shift), "units"),
      kpi("Packed · this shift", n(k.packed_shift), "units"),
      kpi("Stations down", n(k.down_stations), "", k.down_stations > 0),
      kpi("Open work orders", n(k.open_orders)),
      kpi("In repair", n((repair.queue || 0) + (repair.in_work || 0)), "units"),
    ].join("");

    document.getElementById("areas").innerHTML = d.areas.filter((a) => a.lines.length).map((a) => `
      <section class="area">
        <div class="area-head"><h2>${esc(a.name)}</h2><span class="count">${a.lines.length} line${a.lines.length > 1 ? "s" : ""}</span></div>
        <div class="lines">${a.lines.map((l) => lineCard(l, a)).join("")}</div>
      </section>`).join("");

    const sm = d.supermarket;
    document.getElementById("supermarket").innerHTML = sm.length ? sm.map((r) => {
      const max = Math.max(r.stock, r.safety * 1.6, 1);
      return `<div class="bar-row"><span>${esc(r.material.replace("PCBA-", ""))}</span>
        <div class="track"><div class="fill${r.stock < r.safety ? " low" : ""}" style="width:${(100 * r.stock) / max}%"></div>
        <div class="mark" style="left:${(100 * r.safety) / max}%"></div></div>
        <span class="num">${n(r.stock)}</span></div>`;
    }).join("") : `<p class="empty">No data yet.</p>`;

    document.getElementById("repair").innerHTML = `<table>
      <tr><td>Waiting for repair</td><td class="num">${n(repair.queue)}</td></tr>
      <tr><td>On a repair bench</td><td class="num">${n(repair.in_work)}</td></tr>
      <tr><td>Panels being washed (SPI)</td><td class="num">${n(d.washing)}</td></tr></table>`;

    document.getElementById("warehouse").innerHTML = d.warehouse.length ? `<table>
      <tr><th>Material</th><th class="num">Units</th><th class="num">KLT</th><th class="num">Pallets</th></tr>
      ${d.warehouse.map((w) => `<tr><td>${esc(w.material)}</td><td class="num">${n(w.units)}</td>
        <td class="num">${n(w.klt)}</td><td class="num">${n(w.pallets)}</td></tr>`).join("")}</table>`
      : `<p class="empty">No data yet.</p>`;
  }

  MES.poll("/api/v1/plant/overview", render, 2000);
})();
