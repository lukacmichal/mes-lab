/* The robots (AMR): what they carry (the pieces of the repair loop, bins of parts and stacks of empty
   boxes), their lanes, their depot with the chargers, the red NOK totes at the exits of the cells and the
   racks at the lines. Everything comes from the snapshot: where a robot is, where it drives, what it
   carries, its battery, what lies in the racks; nothing here moves on its own. */
const FloorRobots = (() => {
  const A = FloorArt;
  const { P, fillRR, rect, line, circle, text } = A;
  const T = (en, sk) => (typeof MES !== "undefined" && MES.lang() === "sk" ? sk : en);

  const STATE = {
    idle: ["waiting on its charger", "čaká na nabíjačke"],
    charging: ["charging", "nabíja sa"],
    to_pick: ["on the way to pick up", "ide vyzdvihnúť"],
    loading: ["taking the tote", "berie prepravku"],
    to_drop: ["on the way to deliver", "vezie"],
    unloading: ["handing over", "odovzdáva"],
    to_charger: ["back to its charger", "vracia sa na nabíjačku"],
    waiting: ["waiting for a free stop", "čaká na voľnú zastávku"],
    yield: ["waiting until the way is free (people or a robot cross it)", "čaká, kým bude cesta voľná (križujú ju ľudia alebo robot)"],
  };
  const LIGHT = { idle: "#2fbf71", charging: "#3ddc84", to_pick: "#4a8fd8", to_drop: "#4a8fd8", to_charger: "#4a8fd8",
    loading: "#f1a33c", unloading: "#f1a33c", waiting: "#f1a33c", yield: "#f1a33c" };

  /* a place a robot stops at, for people: "FA-01-SCR", "repair centre (in)" */
  function placeName(key) {
    if (!key) return "";
    if (key.startsWith("gap:")) {
      const [, line, cell] = key.split(":");
      const info = Floor.layout.lines[line];
      const c = info && info.cells[+cell];
      return `${line} · ${T("after", "za")} ${c ? c.type : cell} (${T("NOK tote", "NOK prepravka")})`;
    }
    if (key.startsWith("drop:SM")) return T("SMT supermarket", "SMT supermarket");
    if (key === "gi") return T("goods-in warehouse", "sklad materiálu");
    if (key.startsWith("drop:")) {
      const line = key.split(":")[1];
      const info = Floor.layout.lines[line];
      const rack = Floor.layout.racks[key];
      const what = rack ? (rack.kind === "kit" ? T("rack of parts", "regál dielov") : T("empty boxes", "prázdne boxy")) : (info ? info.cells[0].type : "");
      return `${line} · ${what}`;
    }
    return { "rpr:in": T("repair centre · inbound rack", "opravovňa · vstupný regál"),
      "rpr:out": T("repair centre · outbound rack", "opravovňa · výstupný regál"),
      "rpr:quar": T("quarantine cage", "karanténna klietka") }[key] || key.replace("charge:", T("charger ", "nabíjačka "));
  }

  // ---------------------------------------------------------------- a robot from above
  function drawRobot(ctx, r, k, t) {
    const rec = r.rec;
    const [Lr, Wr] = Floor.layout.amr.size;
    const s = Math.max(1, 0.75 / k);
    const light = LIGHT[rec.st] || "#4a8fd8";
    ctx.save();
    ctx.translate(r.x, r.y);
    ctx.rotate(r.ang || 0);
    ctx.scale(s, s);
    fillRR(ctx, -Lr / 2 + 1.2, -Wr / 2 + 1.4, Lr, Wr, 4, P.shadow, null);                              // shadow
    fillRR(ctx, -Lr / 2, -Wr / 2, Lr, Wr, 4, P.dark ? "#c9d2de" : "#eef1f5", P.dark ? "#8796ad" : "#7d8799", 0.6);
    // the light strip along both sides: the state; it runs while the robot drives
    ctx.strokeStyle = light; ctx.lineWidth = 1.2; ctx.lineCap = "butt";
    if (r.moving) { ctx.setLineDash([3, 2]); ctx.lineDashOffset = -t * 14; }
    ctx.beginPath(); ctx.moveTo(-Lr / 2 + 3, -Wr / 2 + 0.7); ctx.lineTo(Lr / 2 - 3, -Wr / 2 + 0.7);
    ctx.moveTo(-Lr / 2 + 3, Wr / 2 - 0.7); ctx.lineTo(Lr / 2 - 3, Wr / 2 - 0.7); ctx.stroke();
    ctx.setLineDash([]);
    // the top module: four places for totes
    fillRR(ctx, -Lr / 2 + 2.4, -Wr / 2 + 2.2, Lr - 4.8, Wr - 4.4, 2, P.dark ? "#566377" : "#a3adbd", null);
    if (k > 0.9) {
      const load = (rec.ld || []).length;
      for (let i = 0; i < 4; i++) {
        const x = (i % 2 ? 6.6 : -6.6), y = (i < 2 ? -4.4 : 4.4);
        fillRR(ctx, x - 6, y - 3.9, 12, 7.8, 0.8, i < load ? (P.dark ? "#2f4b6e" : "#3f6aa5") : (P.dark ? "#6a7689" : "#c4ccd8"), P.dark ? "#3a4659" : "#8994a6", 0.3);
      }
    }
    // the laser scanners at two corners, the front with its arrow
    circle(ctx, Lr / 2 - 2.4, -Wr / 2 + 2.4, 1.5, "#1e242d");
    circle(ctx, -Lr / 2 + 2.4, Wr / 2 - 2.4, 1.5, "#1e242d");
    if (k > 1.6) {
      ctx.fillStyle = light;
      ctx.beginPath(); ctx.moveTo(Lr / 2 - 0.8, 0); ctx.lineTo(Lr / 2 - 3.4, -2); ctx.lineTo(Lr / 2 - 3.4, 2); ctx.closePath(); ctx.fill();
    }
    ctx.restore();
    if (k > 1.3) {
      const b = rec.b ?? 100;                                                   // the battery
      const w = 14 * s;
      fillRR(ctx, r.x - w / 2, r.y + (Wr / 2 + 3) * s, w, 2.2 * s, 0.6, "rgba(0,0,0,.35)", null);
      rect(ctx, r.x - w / 2, r.y + (Wr / 2 + 3) * s, w * b / 100, 2.2 * s, b < 25 ? "#e0453a" : b < 50 ? "#f1a33c" : "#2fbf71");
    }
    if (k > 2.2) text(ctx, rec.n, r.x, r.y + (Wr / 2 + 9) * s, 3.4, P.textDim, "center", "700");
  }

  /* where a driving robot is headed: the rest of its way, dashed (what the fleet manager planned) */
  function drawPath(ctx, r, k) {
    const w = r.rec.w;
    if (!w || k < 0.6) return;
    const done = Math.max(0, Math.min(1, (Floor.sim - w.t) / w.d)) * (w.total || 0);
    let i = 1;
    while (i < w.p.length && w.cum && w.cum[i] < done) i++;
    if (i >= w.p.length) return;
    ctx.save();
    ctx.strokeStyle = LIGHT[r.rec.st] || "#4a8fd8"; ctx.globalAlpha = 0.45; ctx.lineWidth = 1.1 / Math.max(k, 0.8) * 1.4;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(r.x, r.y);
    for (; i < w.p.length; i++) ctx.lineTo(w.p[i][0], w.p[i][1]);
    ctx.stroke();
    const end = w.p[w.p.length - 1];
    ctx.setLineDash([]); ctx.globalAlpha = 0.7;
    circle(ctx, end[0], end[1], 2.2, null, LIGHT[r.rec.st] || "#4a8fd8", 0.8);
    ctx.restore();
  }

  // ---------------------------------------------------------------- the lanes, the depot, the totes
  function drawLanes(ctx, L, k) {
    if (k < 0.7 || !L.amr_lanes) return;
    ctx.save();
    ctx.strokeStyle = P.dark ? "rgba(90,170,255,.16)" : "rgba(40,110,200,.16)";
    ctx.lineWidth = 0.9; ctx.setLineDash([8, 6]);
    ctx.beginPath();
    for (const [[x1, y1], [x2, y2]] of L.amr_lanes) { ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); }
    ctx.stroke();
    ctx.restore();
  }

  function drawDepot(ctx, L, floor, k, lang) {
    const d = L.depot;
    if (!d || !d.w) return;
    fillRR(ctx, d.x, d.y, d.w, d.h, 3, P.dark ? "rgba(255,255,255,.03)" : "rgba(0,0,0,.035)", null);
    ctx.save();                                                               // the yellow and black floor marking
    ctx.strokeStyle = "#e0b100"; ctx.lineWidth = 1.2; ctx.setLineDash([4, 4]);
    A.rr(ctx, d.x, d.y, d.w, d.h, 3); ctx.stroke();
    ctx.strokeStyle = "#1e242d"; ctx.lineDashOffset = 4; A.rr(ctx, d.x, d.y, d.w, d.h, 3); ctx.stroke();
    ctx.restore();
    for (const c of d.chargers) {
      fillRR(ctx, c.x - 9, c.y - 2, 18, 5, 1, "#3a4350", "#1e242d", 0.4);
      if (k > 1.2) { ctx.fillStyle = "#f1c40f"; ctx.beginPath(); ctx.moveTo(c.x + 0.6, c.y - 1.4); ctx.lineTo(c.x - 1.6, c.y + 0.8); ctx.lineTo(c.x, c.y + 0.8); ctx.lineTo(c.x - 0.6, c.y + 2.6); ctx.lineTo(c.x + 1.6, c.y + 0.2); ctx.lineTo(c.x, c.y + 0.2); ctx.closePath(); ctx.fill(); }
    }
    if (k > 0.6) text(ctx, T("AMR depot · chargers", "Depo AMR · nabíjačky"), d.x + d.w / 2, d.y + d.h - 6, Math.max(4.6, 6 / k), P.textDim, "center", "700");
  }

  /* the red NOK tote at the exit of every cell: shown close up, and always when something lies in it */
  function drawNok(ctx, L, floor, k) {
    const boxes = (floor && floor.boxes) || {};
    for (const [code, n] of Object.entries(L.nok)) {
      const full = boxes[`gap:${code}`];
      if (!full && k < 1.3) continue;
      // an empty tote is a quiet red outline; one with something in it is bright, with the count
      fillRR(ctx, n.x, n.y, n.w, n.h, 0.8, full ? "#d64036" : (P.dark ? "#3a2a2c" : "#d9c5c5"), full ? "#7d1f18" : "#b5534b", 0.35);
      rect(ctx, n.x + 0.8, n.y + 0.8, n.w - 1.6, n.h - 1.6, full ? "#a52a22" : (P.dark ? "#2a2022" : "#e8dada"));
      if (full && k > 0.6) {
        circle(ctx, n.x + n.w, n.y, 2.4, "#e0453a", "#fff", 0.4);
        text(ctx, String(full.length), n.x + n.w, n.y + 0.1, 2.8, "#fff", "center", "700");
      }
    }
  }

  /* The racks at the lines: the bins of parts beside the loader of final assembly (the open one, the full
     ones, the ones on the way) and the stacks of empty boxes at the scan station of packing. */
  function drawRacks(ctx, L, floor, k) {
    if (k < 0.9 || !L.racks) return;
    const ls = (floor && floor.ls) || {};
    for (const [key, r] of Object.entries(L.racks)) {
      if (r.kind === "gi") continue;                               // drawn with the warehouse
      const line = key.split(":")[1];
      const st = ls[line];
      fillRR(ctx, r.x, r.y, r.w, r.h, 1, P.shelf, P.rackEdge, 0.5);
      if (!st) continue;
      const [kind, parts, full, cap, onWay, per] = st;
      const sh = (r.h - 1.6) / cap;
      for (let i = 0; i < cap; i++) {
        const y = r.y + 0.8 + i * sh;
        // slot 0 is the open bin / stack, the next ones are the full ones waiting
        const filled = i === 0 ? parts > 0 : i <= full;
        const level = i === 0 ? parts / per : 1;
        fillRR(ctx, r.x + 1, y + 0.3, r.w - 2, sh - 0.6, 0.5, P.dark ? "#22344e" : "#9fb3cf", P.dark ? "#1f3f6b" : "#27508c", 0.25);
        if (filled) rect(ctx, r.x + 1.6, y + 0.8, (r.w - 3.2) * level, sh - 1.6, kind === "kit" ? "#3f78bd" : "#6b8fc4");
      }
      if (k > 1.6) {
        const have = parts + full * per;
        const low = have <= per;
        const label = kind === "kit" ? T(`parts ${have}`, `diely ${have}`) : T(`boxes ${have}`, `boxy ${have}`);
        text(ctx, label, r.x + r.w / 2, r.y - 2.2, 2.8, low ? "#e0453a" : P.textDim, "center", "700");
        if (onWay && k > 2.4) text(ctx, T(`+${onWay} on the way`, `+${onWay} na ceste`), r.x + r.w / 2, r.y + r.h + 8, 2.6, P.textDim, "center");
      }
    }
  }

  return { drawRobot, drawPath, drawLanes, drawDepot, drawNok, drawRacks, placeName, STATE, LIGHT };
})();
