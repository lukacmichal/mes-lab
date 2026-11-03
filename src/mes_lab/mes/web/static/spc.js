/* The SPC page: the list of characteristics under control, the X-bar and R charts of the chosen one
   (SVG), and the alarms with what the MES did about them. */
(() => {
  const { esc } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const NAMES = {
    paste_volume_pct: ["paste volume", "objem pasty"], peak_temp_c: ["peak temperature", "špičková teplota"],
    press_force_kn: ["press-fit force", "sila lisovania"], coating_weight_mg: ["coating weight", "váha laku"],
    torque_nm: ["screw torque", "moment skrutky"], supply_current_ma: ["supply current", "odber prúdu"],
  };
  const RULE = { R1: ["one point beyond 3σ", "jeden bod za 3σ"], R2: ["two of three beyond 2σ", "dva z troch za 2σ"], R5: ["seven in a row rising or falling", "sedem za sebou stúpa alebo klesá"] };
  const nameOf = (n) => T(...(NAMES[n] || [n, n]));
  /* what happened after the adjustment (the MES stores a short code) */
  function noteOf(note) {
    if (!note) return "";
    const [code, a, b] = note.split(":");
    if (code === "NO_HOLD") return T("nothing to contain: the parts were inside the specification", "nebolo čo blokovať: kusy boli v tolerancii");
    if (code === "RELEASED") return T(`all ${a} held parts measured inside the specification: released by data`, `všetkých ${a} zablokovaných kusov bolo v tolerancii: uvoľnené podľa dát`);
    if (code === "WAITS") return T(`${a} of ${b} outside the specification: the hold waits for a person`, `${a} z ${b} mimo tolerancie: blokácia čaká na človeka`);
    return note;
  }
  let list = null, chart = null;
  let pick = null;
  try { pick = JSON.parse(MES.store.get("mes-spc-pick") || "null"); } catch (e) { pick = null; }

  const fmt = (v, d = 3) => (v === null || v === undefined ? "–" : Number(v).toFixed(d).replace(/\.?0+$/, ""));

  /* one chart: points with the centre line, the control limits (a green band) and, far out, the spec */
  function svgChart(points, key, lim, spec, fired) {
    const W = 640, H = 190, L = 54, R = 12, Tp = 10, B = 24;
    const vals = points.map((p) => p[key]);
    let lo = Math.min(lim.lcl, ...vals), hi = Math.max(lim.ucl, ...vals);
    if (spec) { lo = Math.min(lo, spec.lsl); hi = Math.max(hi, spec.usl); }
    const pad = (hi - lo) * 0.08 || 1;
    lo -= pad; hi += pad;
    const x = (i) => L + (points.length <= 1 ? 0 : (i * (W - L - R)) / (points.length - 1));
    const y = (v) => Tp + (H - Tp - B) * (1 - (v - lo) / (hi - lo));
    const bad = new Set();
    for (const f of fired) for (let i = f.from; i <= f.to; i++) if (key === "x") bad.add(i);
    const line = (v, cls, label) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="${cls}"/><text x="${L - 4}" y="${y(v) + 3}" text-anchor="end" class="lbl">${esc(label)}</text>`;
    let out = `<svg viewBox="0 0 ${W} ${H}" class="spc-svg" role="img">`;
    out += `<rect x="${L}" y="${y(lim.ucl)}" width="${W - L - R}" height="${Math.max(0, y(lim.lcl) - y(lim.ucl))}" class="band"/>`;
    if (spec) out += line(spec.usl, "spec", `USL ${fmt(spec.usl)}`) + line(spec.lsl, "spec", `LSL ${fmt(spec.lsl)}`);
    out += line(lim.ucl, "lim", fmt(lim.ucl)) + line(lim.cl, "cl", fmt(lim.cl)) + (lim.lcl > lo ? line(lim.lcl, "lim", fmt(lim.lcl)) : "");
    out += `<polyline class="path" points="${points.map((p, i) => `${x(i)},${y(p[key])}`).join(" ")}"/>`;
    points.forEach((p, i) => {
      out += `<circle cx="${x(i)}" cy="${y(p[key])}" r="${bad.has(i) ? 4.2 : 3}" class="${bad.has(i) ? "pt bad" : "pt"}"><title>${esc(p.t.slice(11, 16))}: ${fmt(p[key])}${key === "x" ? ` (${p.values.map((v) => fmt(v)).join(", ")})` : ""}</title></circle>`;
    });
    const step = Math.max(1, Math.ceil(points.length / 8));
    points.forEach((p, i) => { if (i % step === 0) out += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle" class="lbl">${esc(p.t.slice(11, 16))}</text>`; });
    return out + "</svg>";
  }

  function renderChart() {
    const el = document.getElementById("spc-chart");
    if (!chart) return;
    const c = chart;
    const last = c.points[c.points.length - 1];
    const fired = c.fired.filter((f) => f.to === c.points.length - 1);
    el.innerHTML = `<div class="what"><b>${esc(c.station)}</b> · ${esc(nameOf(c.name))} <span class="faint">[${esc(c.unit)}]</span></div>
      <div class="muted" style="font-size:12.5px">${T("target", "cieľ")} ${fmt(c.mean)} · σ ${fmt(c.sd)} · ${T("specification", "tolerancia")} ${fmt(c.lsl)}–${fmt(c.usl)} · ${T(`subgroup of ${c.subgroup} every ${c.sample_min} min`, `podskupina ${c.subgroup} ks každých ${c.sample_min} min`)}</div>
      ${fired.length ? `<p class="tune-verdict" style="border-color:color-mix(in srgb,var(--s-down) 40%,transparent);background:color-mix(in srgb,var(--s-down) 10%,var(--panel))">${T("Signal now", "Signál teraz")}: ${fired.map((f) => `<b>${f.rule}</b> ${esc(T(...RULE[f.rule]))}`).join(" · ")}</p>` : ""}
      <h3 style="margin-top:10px">X̄ <span class="faint">${T("average of the subgroup", "priemer podskupiny")}</span></h3>
      ${c.points.length ? svgChart(c.points, "x", c.x, { lsl: c.lsl, usl: c.usl }, c.fired) : `<p class="muted">${T("Not enough pieces yet: a point needs five pieces after a whole hour.", "Zatiaľ málo kusov: bod potrebuje päť kusov po celej hodine.")}</p>`}
      <h3>R <span class="faint">${T("range of the subgroup", "rozpätie podskupiny")}</span></h3>
      ${c.points.length ? svgChart(c.points, "r", c.r, null, []) : ""}
      ${last ? `<div class="muted" style="font-size:12.5px">${T("last subgroup", "posledná podskupina")} ${esc(last.t.slice(11, 16))}: ${last.values.map((v) => fmt(v)).join(", ")}</div>` : ""}`;
  }

  function renderList() {
    if (!list) return;
    const rows = list.chars.map((c) => {
      const a = c.alarm && c.alarm.status !== "CLOSED" ? c.alarm : null;
      const state = a ? `<span class="pill st-MAINTENANCE">${T("adjusting", "nastavuje sa")}</span>` : c.signal ? `<span class="pill st-DOWN">${T("signal", "signál")}</span>`
        : c.points ? `<span class="pill st-RUNNING">${T("in control", "v regulácii")}</span>` : `<span class="faint">–</span>`;
      const on = pick && pick.station === c.station && pick.name === c.name;
      return `<tr data-pick="${esc(c.station)}|${esc(c.name)}" style="cursor:pointer${on ? ";background:var(--panel-2)" : ""}"><td><b>${esc(c.station)}</b></td><td>${esc(nameOf(c.name))}</td><td class="num">${fmt(c.last)} <span class="faint">${esc(c.unit)}</span></td><td>${state}</td></tr>`;
    }).join("");
    document.getElementById("spc-list").innerHTML = `<table><tr><th>${T("Station", "Stanica")}</th><th>${T("Characteristic", "Charakteristika")}</th><th class="num">${T("last X̄", "posledné X̄")}</th><th></th></tr>${rows}</table>`;
    const alarms = list.alarms.map((a) => `<tr data-pick="${esc(a.station)}|${esc(a.name)}" style="cursor:pointer"><td>${esc((a.at || "").slice(5, 16).replace("T", " "))}</td><td><b>${esc(a.station)}</b> · ${esc(nameOf(a.name))}</td>
      <td><b>${esc(a.rule)}</b> ${esc(T(...(RULE[a.rule] || [a.rule, a.rule])))}</td>
      <td>${a.status === "CLOSED" ? T("adjusted", "nastavené") : T("adjusting", "nastavuje sa")}${a.hold_id ? ` · <a href="/holds">${T("hold", "blokácia")} #${a.hold_id}</a>` : ""}${a.note ? `<div class="faint">${esc(noteOf(a.note))}</div>` : ""}</td></tr>`).join("");
    document.getElementById("spc-alarms").innerHTML = alarms ? `<table><tr><th>${T("When", "Kedy")}</th><th>${T("Where", "Kde")}</th><th>${T("Rule", "Pravidlo")}</th><th>${T("What happened", "Čo sa stalo")}</th></tr>${alarms}</table>`
      : `<p class="muted">${T("No alarm yet.", "Zatiaľ žiadny alarm.")}</p>`;
  }

  async function loadChart() {
    if (!pick) return;
    try {
      const r = await fetch(`/api/v1/spc/chart?station=${encodeURIComponent(pick.station)}&name=${encodeURIComponent(pick.name)}`, { cache: "no-store" });
      if (r.ok) { chart = await r.json(); renderChart(); }
    } catch (e) { /* the next try */ }
  }

  document.addEventListener("click", (e) => {
    const row = e.target.closest("[data-pick]");
    if (!row) return;
    const [station, name] = row.dataset.pick.split("|");
    pick = { station, name };
    MES.store.set("mes-spc-pick", JSON.stringify(pick));
    renderList();
    loadChart();
  });
  document.addEventListener("mes-lang", () => { renderList(); renderChart(); });
  MES.poll("/api/v1/spc", (d) => {
    list = d;
    if (!pick && d.chars.length) pick = { station: d.chars.find((c) => c.name === "torque_nm")?.station || d.chars[0].station, name: d.chars.find((c) => c.name === "torque_nm") ? "torque_nm" : d.chars[0].name };
    renderList();
    loadChart();
  }, 10000);
})();
