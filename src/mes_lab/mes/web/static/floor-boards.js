/* The hour-by-hour screens hanging over the aisles, one per job line: the plan of the hour from the MES
   next to what the line really made, and the hours of the shift as small bars (the MES computes them,
   see mes/hourly.py; this module only asks for them every few seconds and draws them). */
const FloorBoards = (() => {
  const A = FloorArt;
  const { P, fillRR, rect, text } = A;
  const POLL_MS = 10000;
  let data = null;
  let started = false;
  const T = (en, sk) => (typeof MES !== "undefined" && MES.lang() === "sk" ? sk : en);

  async function poll() {
    if (!document.hidden) {
      try {
        const r = await fetch("/api/v1/boards", { cache: "no-store" });
        if (r.ok) data = await r.json();
      } catch (e) { /* the next look */ }
    }
    setTimeout(poll, POLL_MS);
  }
  function start() {
    if (started) return;
    started = true;
    poll();
  }

  /* where the screen of a line hangs: over the aisle above it, right of the name of the line */
  function rectOf(info) {
    return { x: info.x1 + 175, y: info.y1 - 50, w: 146, h: 36 };
  }

  /* green: on plan (at least 95 %), amber: close (85 %), red: behind */
  function colorOf(plan, actual) {
    if (!plan) return "#6b7686";
    const r = actual / plan;
    return r >= 0.95 ? "#2fbf71" : r >= 0.85 ? "#f1a33c" : "#e0453a";
  }

  function current(board) {
    const hours = board && board.hours;
    return hours && hours.length ? hours[hours.length - 1] : null;
  }

  function draw(ctx, L, k, view) {
    if (!data || k < 0.45) return;
    for (const [code, info] of Object.entries(L.lines)) {
      const board = data.lines[code];
      if (!board) continue;
      const r = rectOf(info);
      if (r.x > view.x1 || r.x + r.w < view.x0 || r.y > view.y1 || r.y + r.h < view.y0) continue;
      const now = current(board);
      const color = now ? colorOf(now.plan, now.actual) : "#6b7686";
      ctx.save();
      ctx.globalAlpha = 0.96;
      fillRR(ctx, r.x + 1.2, r.y + 1.6, r.w, r.h, 2.5, "rgba(0,0,0,.18)", null);         // its shadow on the floor
      fillRR(ctx, r.x, r.y, r.w, r.h, 2.5, "#0d131c", "#3a4659", 0.8);                     // the bezel
      rect(ctx, r.x + 2, r.y + 2, r.w - 4, r.h - 4, "#121b28");
      if (k < 0.8) {                                                                        // far out: only how it goes
        rect(ctx, r.x + 4, r.y + r.h / 2 - 3, r.w - 8, 6, color);
        ctx.restore();
        continue;
      }
      const sh = board.shift;
      const h0 = now ? +now.label : 0;
      text(ctx, `${T("HOUR", "HODINA")} ${now ? `${now.label}–${String((h0 + 1) % 24).padStart(2, "0")}` : "–"}`, r.x + 5, r.y + 6.5, 4, "#8fa3bf", "left", "700");
      text(ctx, T("plan / done", "plán / hotovo"), r.x + 5, r.y + 12.5, 3.2, "#6f8098", "left");
      if (now) {
        text(ctx, `${now.plan}`, r.x + 5, r.y + 22, 8, "#c9d6e8", "left", "700");
        ctx.font = `700 8px "Segoe UI", system-ui, sans-serif`;
        const pw = ctx.measureText(`${now.plan}`).width;
        text(ctx, `/ ${now.actual}`, r.x + 7 + pw, r.y + 22, 8, color, "left", "700");
        const d = now.actual - now.plan;
        text(ctx, `${d >= 0 ? "+" : "−"}${Math.abs(d)}`, r.x + 5, r.y + 31, 4, color, "left", "700");
      }
      text(ctx, `${T("shift", "zmena")} ${sh.actual}/${sh.plan}`, r.x + 24, r.y + 31, 3.4, "#8fa3bf", "left", "600");
      // the hours of the shift: the plan as a frame, the output filled in
      const hours = board.hours.slice(-8);
      const bx = r.x + 74, bw = 7.6, gap = 1, top = r.y + 5, hh = 23;
      const max = Math.max(1, ...hours.map((h) => Math.max(h.plan, h.actual)));
      hours.forEach((h, i) => {
        const x = bx + i * (bw + gap);
        const ph = hh * h.plan / max, ah = hh * h.actual / max;
        rect(ctx, x, top + hh - ah, bw, ah, colorOf(h.plan, h.actual));
        ctx.strokeStyle = "#c9d6e8"; ctx.lineWidth = 0.35;
        ctx.strokeRect(x, top + hh - ph, bw, ph);
        text(ctx, h.label, x + bw / 2, top + hh + 3.3, 2.6, "#6f8098", "center");
      });
      ctx.restore();
    }
  }

  /* the screen of a line at a point of the plan */
  function hit(L, x, y) {
    if (!data) return null;
    for (const [code, info] of Object.entries(L.lines)) {
      if (!data.lines[code]) continue;
      const r = rectOf(info);
      if (x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) return code;
    }
    return null;
  }

  /* the tip over a screen: the hours of the shift as a small table */
  function tip(code, esc) {
    const board = data && data.lines[code];
    if (!board) return "";
    const rows = board.hours.map((h) => `<tr><td>${esc(h.label)}:00</td><td class="num">${h.plan}</td><td class="num" style="color:${colorOf(h.plan, h.actual)}"><b>${h.actual}</b></td></tr>`).join("");
    return `<b>${esc(code)} · ${T("hour by hour", "hodina po hodine")}</b>
      <table style="margin-top:4px"><tr><th></th><th class="num">${T("plan", "plán")}</th><th class="num">${T("done", "hotovo")}</th></tr>${rows}
      <tr><td>${T("shift", "zmena")}</td><td class="num">${board.shift.plan}</td><td class="num"><b>${board.shift.actual}</b></td></tr></table>
      <span class="muted">${T(`Plan = ideal rate of the constraint × ${Math.round(data.efficiency * 100)} %, while the line has a job.`, `Plán = ideálny takt úzkeho hrdla × ${Math.round(data.efficiency * 100)} %, kým má linka zákazku.`)}</span>`;
  }

  return { start, draw, hit, tip, get data() { return data; } };
})();
