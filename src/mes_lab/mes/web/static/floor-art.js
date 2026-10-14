/* Drawing of the pieces, conveyors and buildings of the plant (top view, canvas).
   Everything is derived from what the plant reports: what a piece looks like comes
   from the stations it has passed, not from a picture chosen in advance. */
const FloorArt = (() => {
  const P = {};   // current palette (follows the light / dark setting of the page)

  const LIGHT = {
    floor: "#e8ecf1", grid: "#dde3ea", zone: "rgba(30,60,100,.045)", zoneLine: "rgba(30,60,100,.16)",
    body: "#cdd5e0", bodyEdge: "#8d99ab", deck: "#aab5c5", belt: "#6f7b8d", rail: "#97a3b5", stripe: "#8f9bad",
    glass: "rgba(90,140,200,.10)", glassEdge: "rgba(60,100,150,.35)", text: "#2a3547", textDim: "#5b6a80",
    rework: "#c0892a", washer: "#7fb4dd", building: "#d7dee8", buildingEdge: "#9aa7b9", shelf: "#bcc7d6",
    shelfEmpty: "#c3ccd9", queue: "#b9c4d4", shadow: "rgba(20,30,50,.22)", rackEdge: "#8996aa", person: "#3d4b60",
    label: "#1f2a3b", chipText: "#fff",
  };
  const DARK = {
    floor: "#0e1520", grid: "#141d2b", zone: "rgba(120,160,220,.045)", zoneLine: "rgba(130,170,230,.18)",
    body: "#2b3649", bodyEdge: "#4a5b77", deck: "#18212f", belt: "#0a0f17", rail: "#51617c", stripe: "#2f4160",
    glass: "rgba(140,190,255,.07)", glassEdge: "rgba(150,190,240,.28)", text: "#c4d0e2", textDim: "#8696ae",
    rework: "#b9852e", washer: "#4f86b4", building: "#1a2433", buildingEdge: "#42536d", shelf: "#2a374c",
    shelfEmpty: "#223049", queue: "#2a374c", shadow: "rgba(0,0,0,.35)", rackEdge: "#4f6080", person: "#aebbd0",
    label: "#e1e9f5", chipText: "#0b111b",
  };
  const STATE_VARS = {
    RUNNING: "--s-running", STARVED: "--s-starved", BLOCKED: "--s-blocked", DOWN: "--s-down",
    MINOR_STOP: "--s-minor", CHANGEOVER: "--s-changeover", MAINTENANCE: "--s-maintenance",
    NO_ORDER: "--s-noorder", UNKNOWN: "--s-unknown",
  };

  function loadPalette() {
    const dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
    Object.assign(P, dark ? DARK : LIGHT);
    P.dark = dark;
    const cs = getComputedStyle(document.documentElement);
    P.state = {};
    for (const [state, name] of Object.entries(STATE_VARS)) P.state[state] = cs.getPropertyValue(name).trim() || "#888";
  }
  loadPalette();
  if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", loadPalette);

  // ---------------------------------------------------------------- products
  const PRODUCTS = {
    "BCM-200":    { pcb: "#2f9e63", edge: "#1c6a42", name: "BCM" },
    "LED-DRV-40": { pcb: "#e9eef5", edge: "#9aa6b6", name: "LED" },
    "BMS-12S":    { pcb: "#3b7dd8", edge: "#2455a0", name: "BMS" },
    "PDC-8":      { pcb: "#d9544f", edge: "#9c2f2b", name: "PDC" },
  };
  const FALLBACK = { pcb: "#8a94a6", edge: "#5d6778", name: "?" };
  const style = (p) => PRODUCTS[p] || FALLBACK;

  // ---------------------------------------------------------------- primitives
  function rr(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
  function fillRR(ctx, x, y, w, h, r, fill, stroke, lw = 0.6) {
    rr(ctx, x, y, w, h, r);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.stroke(); }
  }
  function rect(ctx, x, y, w, h, fill) { ctx.fillStyle = fill; ctx.fillRect(x, y, w, h); }
  function line(ctx, x1, y1, x2, y2, stroke, lw = 1) {
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
    ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.stroke();
  }
  function circle(ctx, x, y, r, fill, stroke, lw = 0.5) {
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.stroke(); }
  }
  function text(ctx, str, x, y, size, fill, align = "left", weight = "") {
    ctx.font = `${weight} ${size}px "Segoe UI", system-ui, sans-serif`.trim();
    ctx.fillStyle = fill; ctx.textAlign = align; ctx.textBaseline = "middle";
    ctx.fillText(str, x, y);
  }
  /* text that shrinks until it fits `maxW` */
  function fitText(ctx, str, x, y, size, maxW, fill, align = "left", weight = "") {
    ctx.font = `${weight} ${size}px "Segoe UI", system-ui, sans-serif`.trim();
    const w = ctx.measureText(str).width;
    if (w > maxW) size = size * maxW / w;
    text(ctx, str, x, y, size, fill, align, weight);
  }
  function person(ctx, x, y, s = 1, facing = 0, busy = 0) {
    // seen from above: shoulders and a head
    ctx.save(); ctx.translate(x, y); ctx.rotate(facing); ctx.scale(s, s);
    ctx.fillStyle = P.person;
    ctx.beginPath(); ctx.ellipse(0, 0, 2.2, 4.2, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#e4b78c"; ctx.beginPath(); ctx.arc(0.8, 0, 1.9, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = P.person; ctx.lineWidth = 1.1; ctx.lineCap = "round";
    const sway = Math.sin(busy * 7) * 1.1;
    ctx.beginPath(); ctx.moveTo(0.5, -3.6); ctx.lineTo(3.8 + sway, -3 + sway * 0.4);
    ctx.moveTo(0.5, 3.6); ctx.lineTo(3.8 - sway, 3 - sway * 0.4); ctx.stroke();
    ctx.restore();
  }

  const has = (d, tag) => !!(d.d && d.d.includes(tag));
  const frac = (x) => x - Math.floor(x);

  // ---------------------------------------------------------------- sizes of the products
  // the plan delivers the display sizes of every product: panel, unit, KLT box, pallet
  const DEFAULT_SIZES = { panel: [10, 7.9], grid: [2, 2], unit: [10, 8], klt: [11, 7.4], pallet: [20, 13.3], pallet_grid: [3, 2, 5] };
  function sizes(product) {
    const pr = typeof Floor !== "undefined" && Floor.layout && Floor.layout.products[product];
    return pr ? { ...DEFAULT_SIZES, ...pr.sizes } : DEFAULT_SIZES;
  }

  // ---------------------------------------------------------------- SMT panel
  const COMPS = [[0.5, 0.45, 1.0, 0.7], [2.1, 0.5, 0.8, 0.8], [3.1, 1.5, 0.9, 0.7], [0.7, 2.3, 1.2, 0.7], [2.2, 2.5, 0.9, 0.8]];
  const PASTE = [[0.6, 0.6], [2.4, 0.6], [3.2, 1.8], [1.0, 2.6], [2.6, 2.8]];

  function drawPanel(ctx, s, k) {
    const d = s.desc, pr = s.partial;
    const z = sizes(d.p);
    const [w, h] = z.panel;
    const [cols, rows] = z.grid;
    const n = d.n || cols * rows;
    const st = style(d.p);
    if (k < 0.95) {
      rect(ctx, -w / 2, -h / 2, w, h, d.f ? "#e8453c" : st.pcb);
      if (d.x) rect(ctx, -w / 2, -h / 2, w, 1.6, "#7c2a2a");
      return;
    }
    fillRR(ctx, -w / 2, -h / 2, w, h, 0.7, P.dark ? "#56637a" : "#8794a8", null);
    const m = 0.6, cw = (w - 2 * m) / cols, ch = (h - 2 * m) / rows;
    const fx = cw / 4.4, fy = ch / 3.8;
    const cells = [];
    for (let i = 0; i < n; i++) {
      const cx = -w / 2 + m + (i % cols) * cw, cy = -h / 2 + m + Math.floor(i / cols) * ch;
      cells.push([cx, cy]);
      fillRR(ctx, cx, cy, cw - 0.4, ch - 0.4, 0.4, d.x && d.x.includes(i) ? "#7c2a2a" : st.pcb, null);
    }
    if (k < 1.6) {
      if (d.f) { ctx.strokeStyle = "#e8453c"; ctx.lineWidth = 0.8; rr(ctx, -w / 2, -h / 2, w, h, 0.7); ctx.stroke(); }
      return;
    }
    // what the machines of the line have done to it so far
    const showPaste = has(d, "PRN") || (pr && pr.tag === "PRN");
    const showChips = has(d, "PP1") || (pr && pr.tag === "PP1");
    const showIc = has(d, "PP2") || (pr && pr.tag === "PP2");
    const soldered = has(d, "OVN");
    cells.forEach(([cx, cy], i) => {
      if (d.x && d.x.includes(i)) {
        line(ctx, cx + 0.5, cy + 0.4, cx + cw - 0.9, cy + ch - 0.8, "#ff6b61", 0.5);
        line(ctx, cx + cw - 0.9, cy + 0.4, cx + 0.5, cy + ch - 0.8, "#ff6b61", 0.5);
        return;
      }
      const upTo = (i + 1) / n;
      const sweep = pr ? pr.p : 1;
      if (showPaste && !has(d, "PP1")) {
        const live = pr && pr.tag === "PRN";
        if (!live || upTo <= sweep + 0.12) {
          for (const [px, py] of PASTE) rect(ctx, cx + px * 0.75 * fx, cy + py * 0.7 * fy, 0.6 * fx, 0.5 * fy, "#c8ced6");
        }
      }
      if (showChips) {
        const live = pr && pr.tag === "PP1";
        const count = live ? Math.floor(sweep * COMPS.length * n) - i * COMPS.length : COMPS.length;
        COMPS.forEach(([x, y, cw0, ch0], j) => {
          if (j < count) rect(ctx, cx + x * 0.78 * fx, cy + y * 0.78 * fy, cw0 * 0.62 * fx, ch0 * 0.62 * fy, soldered ? "#d8dde6" : "#1d2128");
        });
      }
      if (showIc) {
        const live = pr && pr.tag === "PP2";
        const count = live ? Math.floor(sweep * 3 * n) - i * 3 : 3;
        [[1.4, 1.4], [0.5, 1.3], [3.0, 0.5]].forEach(([x, y], j) => {
          if (j < count) rect(ctx, cx + x * 0.8 * fx, cy + y * 0.8 * fy, 1.1 * fx, 1.0 * fy, "#0e1013");
        });
      }
      if (soldered) rect(ctx, cx, cy, cw - 0.4, ch - 0.4, "rgba(255,230,160,.10)");
    });
    if (has(d, "LSR")) rect(ctx, w / 2 - 1.8, -h / 2 + 0.2, 1.2, 0.8, "#f1f5fa");
    if (d.s === 2) { ctx.fillStyle = "#e6b422"; ctx.fillRect(-w / 2, h / 2 - 0.5, 2.5, 0.5); }
    if (d.f) { ctx.strokeStyle = "#e8453c"; ctx.lineWidth = 0.8; rr(ctx, -w / 2, -h / 2, w, h, 0.7); ctx.stroke(); }
  }

  function drawBoard(ctx, s, k) {
    const d = s.desc, st = style(d.p);
    const z = sizes(d.p);
    const w = Math.max(4, (z.panel[0] - 1.2) / z.grid[0] * 1.5), h = Math.max(3.2, (z.panel[1] - 1.2) / z.grid[1] * 1.5);
    if (k < 0.95) { rect(ctx, -w / 2, -h / 2, w, h, d.x ? "#7c2a2a" : st.pcb); return; }
    fillRR(ctx, -w / 2, -h / 2, w, h, 0.5, d.x ? "#7c2a2a" : st.pcb, st.edge, 0.3);
    if (k > 2) {
      const fx = w / 7.2, fy = h / 5.4;
      COMPS.forEach(([x, y, cw, ch]) => rect(ctx, -w / 2 + 0.4 + x * 1.5 * fx, -h / 2 + 0.4 + y * 1.1 * fy, cw * fx, ch * 0.8 * fy, "#d8dde6"));
      rect(ctx, -0.7 * fx, -0.5 * fy, 1.6 * fx, 1.4 * fy, "#0e1013");
    }
    if (d.x) {
      line(ctx, -w * 0.36, -h * 0.33, w * 0.36, h * 0.33, "#ff6b61", 0.6);
      line(ctx, w * 0.36, -h * 0.33, -w * 0.36, h * 0.33, "#ff6b61", 0.6);
    }
    if (d.f) { ctx.strokeStyle = "#e8453c"; ctx.lineWidth = 0.7; rr(ctx, -w / 2, -h / 2, w, h, 0.5); ctx.stroke(); }
  }

  // ---------------------------------------------------------------- final assembly unit
  // reference sizes of the drawings below; a unit is drawn scaled to the real size of its product
  const UNIT_REF = { "BCM-200": [10, 8.4], "LED-DRV-40": [9.2, 9.2], "BMS-12S": [12.4, 5.2], "PDC-8": [8.8, 8.0] };

  function unitBase(ctx, d, pr, k) {
    // one drawing per product; stages come from the stations the unit has passed
    const part = (tag) => has(d, tag) || (pr && pr.tag === tag);
    const live = (tag) => pr && pr.tag === tag ? pr.p : 1;
    const p = d.p;
    if (p === "LED-DRV-40") {
      fillRR(ctx, -4.6, -4.6, 9.2, 9.2, 1.2, "#aeb8c6", "#6d7888", 0.4);
      for (const y of [-3.4, -2.2]) line(ctx, -4, y, 4, y, "#8f9aab", 0.35);
      fillRR(ctx, -3.3, -1.4, 6.6, 5.2, 0.6, "#e9eef5", "#8995a6", 0.3);          // the cup with the board
      if (part("POT")) {
        const f = live("POT");
        ctx.fillStyle = has(d, "CUR") ? "#8a5217" : "#d6903a";
        ctx.fillRect(-3.3, 3.8 - 5.2 * f, 6.6, 5.2 * f);
      }
      if (has(d, "LSU")) rect(ctx, -3.5, -3.6, 2.2, 1.0, "#f1f5fa");
      return;
    }
    if (p === "BMS-12S") {
      fillRR(ctx, -6.2, -2.6, 12.4, 5.2, 0.5, "#3b7dd8", "#2455a0", 0.35);
      for (let i = 0; i < 12; i++) rect(ctx, -5.4 + i * 0.95, -2.5, 0.5, 0.9, "#d9b84a");
      for (const [x, y, w, h] of [[-4.4, 0, 1.2, 1.0], [-1.6, -0.2, 1.8, 1.4], [1.6, 0.2, 1.2, 1.0], [3.4, -0.3, 1.5, 1.2]]) rect(ctx, x, y, w, h, "#161a20");
      if (part("CTR")) {
        const f = live("CTR");
        ctx.fillStyle = has(d, "UVC") ? "rgba(150,225,255,.40)" : "rgba(120,200,255,.28)";
        ctx.fillRect(-6.2, -2.6, 12.4 * f, 5.2);
      }
      if (has(d, "FLS")) circle(ctx, 5.2, 1.6, 0.5, "#3ddc84");
      if (has(d, "LSU")) rect(ctx, 4.2, -0.3, 1.6, 0.9, "#f1f5fa");
      return;
    }
    if (p === "PDC-8") {
      fillRR(ctx, -3.8, -3.2, 7.6, 6.4, 0.5, "#d9544f", "#9c2f2b", 0.3);
      rect(ctx, -2.4, -1.8, 1.8, 1.2, "#161a20"); rect(ctx, 0.2, -1.4, 1.6, 1.4, "#161a20");
      if (part("SCR")) {
        const f = live("SCR");
        fillRR(ctx, -4.4, -4.0, 8.8, 8.0, 1.0, "#3a424f", "#1e232b", 0.3);
        for (let i = 0; i < 4; i++) circle(ctx, i % 2 ? 3.3 : -3.3, i < 2 ? -3.0 : 3.0, 0.6, i / 4 < f ? "#d6dde6" : "#69727f");
        for (let i = 0; i < 4; i++) circle(ctx, -3.0 + i * 2.0, 4.0, 0.7, "#0e1013", "#8d97a6", 0.2);
      }
      if (has(d, "FLS")) circle(ctx, 3.0, 2.2, 0.5, "#3ddc84");
      if (has(d, "LSU")) rect(ctx, -2.8, 0.8, 2.0, 1.0, "#f1f5fa");
      return;
    }
    // BCM-200 (and anything else): board + connector + cover
    const st = style(p);
    fillRR(ctx, -4.6, -3.4, 9.0, 6.8, 0.5, st.pcb, st.edge, 0.3);
    COMPS.forEach(([x, y, w, h]) => rect(ctx, -4.0 + x * 1.5, -2.8 + y * 1.3, w, h * 0.8, "#d8dde6"));
    rect(ctx, -1.2, -0.8, 1.8, 1.6, "#0e1013");
    if (part("PRS")) {
      const f = live("PRS");
      const x = 4.4 - 1.6 + (1 - f) * 3;
      fillRR(ctx, x, -2.6, 3.4, 5.2, 0.4, "#3c4350", "#9a8420", 0.3);
      for (let i = 0; i < 4; i++) rect(ctx, x + 2.6, -2.1 + i * 1.3, 0.8, 0.5, "#e6c34a");
    }
    if (part("CTR")) {
      const f = live("CTR");
      ctx.fillStyle = has(d, "UVC") ? "rgba(140,225,255,.42)" : "rgba(110,200,255,.30)";
      ctx.fillRect(-4.6, -3.4, 9.0 * f, 6.8);
    }
    if (part("SCR")) {
      const f = live("SCR");
      fillRR(ctx, -5.0, -4.2, 10.0, 8.4, 1.0, "#3a424f", "#1e232b", 0.3);
      rect(ctx, 3.6, -2.0, 1.6, 4.0, "#3c4350");
      for (let i = 0; i < 4; i++) circle(ctx, i % 2 ? 3.2 : -3.6, i < 2 ? -3.0 : 3.0, 0.65, i / 4 < f ? "#d6dde6" : "#69727f");
    }
    if (has(d, "FLS")) circle(ctx, -3.5, 0, 0.5, "#3ddc84");
    if (has(d, "LSU")) rect(ctx, -2.2, 1.5, 2.2, 1.0, "#f1f5fa");
  }

  function drawUnit(ctx, s, k) {
    const d = s.desc, st = style(d.p);
    const [uw, uh] = sizes(d.p).unit;
    if (k < 1.2) {
      const col = d.f ? "#e8453c" : d.p === "LED-DRV-40" ? "#aeb8c6" : st.pcb;
      if (k < 0.95) rect(ctx, -uw / 2, -uh / 2, uw, uh, col);
      else fillRR(ctx, -uw / 2, -uh / 2, uw, uh, 0.8, col, st.edge, 0.4);
      return;
    }
    const ref = UNIT_REF[d.p] || UNIT_REF["BCM-200"];
    ctx.save();
    ctx.scale(uw / ref[0], uh / ref[1]);
    unitBase(ctx, d, s.partial, k);
    if (d.f) { ctx.strokeStyle = "#e8453c"; ctx.lineWidth = 0.7; rr(ctx, -ref[0] / 2 - 0.6, -ref[1] / 2 - 0.6, ref[0] + 1.2, ref[1] + 1.2, 1); ctx.stroke(); }
    ctx.restore();
  }

  // ---------------------------------------------------------------- boxes and pallets
  function drawKlt(ctx, s, k) {
    const d = s.desc, st = style(d.p);
    const [w, h] = sizes(d.p).klt;
    if (k < 0.95) { rect(ctx, -w / 2, -h / 2, w, h, "#3f78bd"); rect(ctx, -w / 2 + 1.2, -h / 2 + 1.2, w - 2.4, h - 2.4, st.pcb); return; }
    fillRR(ctx, -w / 2, -h / 2, w, h, 0.9, P.dark ? "#3b6fb3" : "#3f78bd", P.dark ? "#1f3f6b" : "#27508c", 0.4);
    const fill = d.c ? Math.min(1, d.n / d.c) : 0;
    const cols = 4, rowsN = Math.max(2, Math.round(h / 3.6));
    const total = cols * rowsN;
    const cubes = Math.ceil(fill * total);
    const cw = (w - 2) / cols, ch = (h - 2) / rowsN;
    for (let i = 0; i < total; i++) {
      fillRR(ctx, -w / 2 + 1 + (i % cols) * cw + 0.2, -h / 2 + 1 + Math.floor(i / cols) * ch + 0.2, cw - 0.4, ch - 0.4, 0.3,
        i < cubes ? st.pcb : "rgba(0,0,0,.28)", null);
    }
    if (k > 2) { rect(ctx, w / 2 - 3.4, h / 2 - 1.5, 2.8, 1.1, "#f4f6f9"); }
  }

  function drawPallet(ctx, s, k) {
    const d = s.desc, st = style(d.p);
    const z = sizes(d.p);
    const [w, h] = z.pallet;
    if (k < 0.95) { rect(ctx, -w / 2, -h / 2, w, h, "#a47a46"); rect(ctx, -w / 2 + 2, -h / 2 + 2, w - 4, h - 4, st.pcb); return; }
    fillRR(ctx, -w / 2, -h / 2, w, h, 0.6, "#a47a46", "#6b4d27", 0.4);
    for (let i = 1; i < 4; i++) line(ctx, -w / 2 + 0.5, -h / 2 + i * h / 4, w / 2 - 0.5, -h / 2 + i * h / 4, "#7f5c30", 0.4);
    const [across, down, layers] = z.pallet_grid;
    const perLayer = across * down;
    const n = d.n || 0, c = d.c || 1;
    const shown = n >= perLayer ? perLayer : n;                 // the first layer fills box by box
    const bw = (w - 1.6) / across, bh = (h - 1.6) / down;
    for (let i = 0; i < perLayer; i++) {
      const x = -w / 2 + 0.8 + (i % across) * bw, y = -h / 2 + 0.8 + Math.floor(i / across) * bh;
      if (i < shown) {
        fillRR(ctx, x + 0.2, y + 0.2, bw - 0.4, bh - 0.4, 0.4, "#3f78bd", "#27508c", 0.25);
        rect(ctx, x + bw * 0.25, y + bh * 0.25, bw * 0.5, bh * 0.5, st.pcb);
      } else {
        rect(ctx, x + 0.2, y + 0.2, bw - 0.4, bh - 0.4, "rgba(0,0,0,.16)");
      }
    }
    // the height of the stack
    rect(ctx, -w / 2, h / 2 + 0.6, w, 0.9, "rgba(0,0,0,.28)");
    rect(ctx, -w / 2, h / 2 + 0.6, w * Math.min(1, n / c), 0.9, st.pcb);
    if (k > 3) text(ctx, `${n}/${c}`, 0, 0, 3.2, "#fff", "center", "600");
  }

  function drawMag(ctx, s, k) {
    if (k < 0.95) { rect(ctx, -5, -4, 10, 8, "#7e8ea6"); return; }
    fillRR(ctx, -5, -4, 10, 8, 0.8, P.dark ? "#566882" : "#7e8ea6", P.dark ? "#33425a" : "#566682", 0.4);
    for (let i = 0; i < 4; i++) rect(ctx, -3.8, -3 + i * 1.6, 7.6, 0.9, P.dark ? "#2d6b4d" : "#3c8a64");
    if (k > 2.5) text(ctx, String(s.desc.n || ""), 0, 0, 3, "#fff", "center", "600");
  }

  /* a bin of parts for final assembly (a blue KLT with the parts of one product in it; empty: grey inside) */
  function drawBin(ctx, s, k) {
    const d = s.desc, st = style(d.p);
    fillRR(ctx, -5.5, -4, 11, 8, 0.8, P.dark ? "#3b6fb3" : "#3f78bd", P.dark ? "#1f3f6b" : "#27508c", 0.4);
    if (!d.n) { rect(ctx, -4.3, -2.8, 8.6, 5.6, P.dark ? "#22344e" : "#9fb3cf"); return; }
    rect(ctx, -4.3, -2.8, 8.6, 5.6, P.dark ? "#1d2c42" : "#2f5b92");
    if (k < 1.2) { rect(ctx, -3.6, -2.1, 7.2, 4.2, st.pcb); return; }
    for (let i = 0; i < 6; i++) fillRR(ctx, -3.9 + (i % 3) * 2.7, -2.4 + Math.floor(i / 3) * 2.5, 2.3, 2.1, 0.4, st.pcb, st.edge, 0.2);
  }

  /* a stack of empty KLT boxes for packing */
  function drawStack(ctx, s, k) {
    for (let i = 2; i >= 0; i--) fillRR(ctx, -5.5 + i * 0.7, -4 - i * 0.7, 11, 8, 0.8, P.dark ? "#3b6fb3" : "#3f78bd", P.dark ? "#1f3f6b" : "#27508c", 0.4);
    rect(ctx, -4.3, -5.4, 8.6, 5.6, P.dark ? "#22344e" : "#9fb3cf");
    if (k > 2.5) text(ctx, String(s.desc.n || ""), 0, -2.6, 3, P.dark ? "#d7e0ee" : "#27508c", "center", "700");
  }

  function drawItem(ctx, s, k, opts = {}) {
    ctx.save();
    ctx.globalAlpha = s.alpha;
    ctx.translate(s.x, s.y);
    const rot = opts.rot ?? s.rot;
    if (rot) ctx.rotate(rot);
    if (s.scale && s.scale !== 1) ctx.scale(s.scale, s.scale);       // in a tote, on a robot, in the hands
    switch (s.desc.k) {
      case "P": drawPanel(ctx, s, k); break;
      case "B": drawBoard(ctx, s, k); break;
      case "U": drawUnit(ctx, s, k); break;
      case "K": drawKlt(ctx, s, k); break;
      case "L": drawPallet(ctx, s, k); break;
      case "G": drawMag(ctx, s, k); break;
      case "N": drawBin(ctx, s, k); break;
      case "S": drawStack(ctx, s, k); break;
      default: circle(ctx, 0, 0, 2, "#999");
    }
    ctx.restore();
  }

  /* the size of a piece on the plan, for pointing at it */
  function itemSize(desc) {
    const z = sizes(desc.p);
    switch (desc.k) {
      case "P": return z.panel;
      case "B": return [Math.max(4, (z.panel[0] - 1.2) / z.grid[0] * 1.5), Math.max(3.2, (z.panel[1] - 1.2) / z.grid[1] * 1.5)];
      case "U": return z.unit;
      case "K": return z.klt;
      case "L": return z.pallet;
      case "N": case "S": return [11, 8];
      default: return [10, 8];
    }
  }

  // ---------------------------------------------------------------- conveyors
  const BELT = { supply: 1, sm: 1, feed: 1, pack: 1, wh: 1, ret: 1, ship: 1 };
  const BELT_WIDTH = { wh: [20, 16.4], ship: [26, 22] };            // roller conveyors for pallets

  function tracePath(ctx, pts) {
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  }

  function drawRoutes(ctx, layout, k, t, active) {
    ctx.lineJoin = "round"; ctx.lineCap = "butt";
    ctx.setLineDash([]);
    for (const r of Object.values(layout.routes)) {
      if (r.kind === "wash") {
        tracePath(ctx, r.pts);
        ctx.strokeStyle = P.washer; ctx.globalAlpha = 0.5; ctx.lineWidth = 2.6; ctx.stroke(); ctx.globalAlpha = 1;
      }
    }
    for (const [key, r] of Object.entries(layout.routes)) {
      if (!BELT[r.kind]) continue;
      const [railW, beltW] = BELT_WIDTH[r.kind] || [8.4, 6.2];
      if (r.kind === "ret") {                                       // the return belt runs under the machines
        tracePath(ctx, r.pts);
        ctx.strokeStyle = P.rail; ctx.globalAlpha = 0.55; ctx.lineWidth = 6; ctx.stroke(); ctx.globalAlpha = 1;
        continue;
      }
      tracePath(ctx, r.pts);
      ctx.strokeStyle = P.rail; ctx.lineWidth = railW; ctx.stroke();
      tracePath(ctx, r.pts);
      ctx.strokeStyle = P.belt; ctx.lineWidth = beltW; ctx.stroke();
      if (k > 0.45) {
        tracePath(ctx, r.pts);
        ctx.setLineDash([1.6, 5.4]);
        ctx.lineDashOffset = -(active && active.has(key) ? t * 6 : 0);   // a belt runs while it carries something
        ctx.strokeStyle = P.stripe; ctx.lineWidth = beltW - 1.4; ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }

  /* belts between the cells of a line and the forks in front of double cells */
  function drawLineBelts(ctx, layout, k, t, running) {
    for (const info of Object.values(layout.lines)) {
      if (info.kind === "REPAIR") continue;
      for (const c of info.cells) {
        if (c.buf) {
          const { x0, x1, y } = c.buf;
          rect(ctx, x0, y - 4.2, x1 - x0, 8.4, P.rail);
          rect(ctx, x0, y - 3.1, x1 - x0, 6.2, P.belt);
          if (k > 0.45) {
            ctx.save();
            ctx.beginPath(); ctx.rect(x0, y - 3.1, x1 - x0, 6.2); ctx.clip();
            ctx.setLineDash([1.6, 5.4]); ctx.lineDashOffset = -(running[info.code] ? t * 6 : 0);
            ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y);
            ctx.strokeStyle = P.stripe; ctx.lineWidth = 4.8; ctx.stroke();
            ctx.restore();
          }
        }
        if (c.stations.length > 1) {
          const ys = c.stations.map((s) => layout.stations[s].cy);
          const x = layout.stations[c.stations[0]].x;
          for (const [bx, dir] of [[x - 4, 1], [x + c.w + 4, -1]]) {
            rect(ctx, bx - 1.5, Math.min(...ys) - 1.5, 3, Math.max(...ys) - Math.min(...ys) + 3, P.rail);
            for (const y of ys) rect(ctx, Math.min(bx, bx + dir * 4) - (dir > 0 ? 0 : 0), y - 2, 4, 4, P.belt);
          }
        }
      }
    }
  }

  /* Where a walkway or an AMR lane passes a conveyor, the conveyor goes over it on a bridge: drawn over the
     people and the robots, with its posts on both sides of the aisle. The belts into the trucks have a lift
     gate instead (yellow and black). */
  function drawCrossings(ctx, layout, k) {
    for (const c of layout.crossings || []) {
      const [railW, beltW] = c.wide ? [20, 16.4] : [8.4, 6.2];
      const half = c.len / 2 + 3;
      const a = Math.atan2(c.dy, c.dx);
      ctx.save(); ctx.translate(c.x, c.y); ctx.rotate(a);
      if (c.kind === "gate") {
        rect(ctx, -half, -railW / 2, half * 2, railW, "#1e242d");
        ctx.fillStyle = "#f1c40f";
        for (let x = -half; x < half; x += 4) { ctx.beginPath(); ctx.moveTo(x, -railW / 2); ctx.lineTo(x + 2, -railW / 2); ctx.lineTo(x + 2 + railW * 0.3, railW / 2); ctx.lineTo(x + railW * 0.3, railW / 2); ctx.closePath(); ctx.fill(); }
        ctx.restore();
        continue;
      }
      rect(ctx, -half + 1.6, -railW / 2 + 2.2, half * 2, railW, P.shadow);                     // shadow on the floor
      rect(ctx, -half, -railW / 2 - 0.6, half * 2, railW + 1.2, P.dark ? "#3c4a60" : "#7d8aa0");   // the bridge
      rect(ctx, -half, -beltW / 2, half * 2, beltW, P.belt);
      for (const x of [-half + 1, half - 3]) for (const y of [-railW / 2 - 1.8, railW / 2 + 0.6]) rect(ctx, x, y, 2, 1.2, "#1e242d");   // posts
      ctx.restore();
    }
  }

  // ---------------------------------------------------------------- buildings
  function shelfGrid(ctx, x, y, w, h, cols, rows, filled, color, edge) {
    const cw = w / cols, ch = h / rows;
    ctx.beginPath();
    for (let i = filled; i < cols * rows; i++) {
      const col = i % cols, row = Math.floor(i / cols);
      ctx.rect(x + col * cw + 0.5, y + (rows - 1 - row) * ch + 0.5, cw - 1, ch - 1);
    }
    ctx.fillStyle = P.shelfEmpty; ctx.fill();
    ctx.beginPath();
    for (let i = 0; i < Math.min(filled, cols * rows); i++) {
      const col = i % cols, row = Math.floor(i / cols);
      ctx.rect(x + col * cw + 0.5, y + (rows - 1 - row) * ch + 0.5, cw - 1, ch - 1);
    }
    ctx.fillStyle = color; ctx.fill();
  }

  /* the pallets of the finished goods warehouse, one cell each (oldest bottom left, next to the dock) */
  const FGW_COL = 24, FGW_DOCK = 28;
  function fgwCells(b, f) {
    const out = { cells: [], bands: [] };
    const fg = (f && f.fgw) || {};
    for (const rk of b.racks || []) {
      const z = sizes(rk.product);
      const ph = z.pallet[1] + 3.5;
      const y0 = Math.max(b.y + 20, rk.y0), y1 = Math.min(b.y + b.h - FGW_DOCK, rk.y1 - 4);
      const cols = Math.max(1, Math.floor((b.w - 12) / FGW_COL));
      const rows = Math.max(1, Math.floor((y1 - y0 - 12) / ph));
      const list = fg[rk.product] || [];
      out.bands.push({ product: rk.product, x: b.x + 4, y: y0, w: b.w - 8, h: y1 - y0, cols, rows, cap: cols * rows, count: list.length, ph });
      list.slice(0, cols * rows).forEach((sn, i) => {
        out.cells.push({ x: b.x + 6 + (i % cols) * FGW_COL, y: y1 - (Math.floor(i / cols) + 1) * ph, w: FGW_COL - 2, h: ph - 1.5,
          sn, product: rk.product });
      });
    }
    return out;
  }

  function drawFgw(ctx, b, f, k, lang) {
    const T = (en, sk) => (lang === "sk" ? sk : en);
    fillRR(ctx, b.x, b.y, b.w, b.h, 6, P.building, P.buildingEdge, 1);
    const ls = Math.max(5.5, Math.min(13, 10 / k));
    fitText(ctx, lang === "sk" ? b.name_sk : b.name, b.x + b.w / 2, b.y + 4 + ls * 0.55, ls, b.w - 8, P.label, "center", "600");
    const { cells, bands } = fgwCells(b, f);
    for (const band of bands) {
      const st = style(band.product);
      rect(ctx, band.x, band.y, band.w, band.h, P.shelf);
      ctx.beginPath();                                                  // the empty places
      for (let i = band.count; i < band.cap; i++) {
        ctx.rect(b.x + 6 + (i % band.cols) * FGW_COL, band.y + band.h - (Math.floor(i / band.cols) + 1) * band.ph, FGW_COL - 2, band.ph - 1.5);
      }
      ctx.fillStyle = P.shelfEmpty; ctx.fill();
      if (k > 0.5) {
        text(ctx, st.name, band.x + 3, band.y + 5, 5.6, P.label, "left", "700");
        text(ctx, T(`${band.count} pallets`, `${band.count} paliet`), band.x + band.w - 3, band.y + 5, 4.8, P.textDim, "right");
      }
    }
    for (const c of cells) {
      const pk = Floor.layout.products[c.product];
      const pallet = { x: c.x + c.w / 2, y: c.y + c.h / 2, alpha: 1, desc: { k: "L", p: c.product, n: pk.klt_per_pallet, c: pk.klt_per_pallet }, partial: null };
      drawItem(ctx, pallet, Math.min(k, 2.2));
      if (k > 4.5) text(ctx, c.sn.slice(-6), c.x + c.w / 2, c.y + c.h + 1.4, 2.6, P.textDim, "center");
    }
    // the dock doors, in front of the belts to the trucks
    for (const bay of Floor.layout.bays || []) {
      rect(ctx, bay.x - 15, b.y + b.h - 6, 30, 6, P.dark ? "#0b1018" : "#7d8798");
    }
  }

  function drawBuilding(ctx, b, f, k, lang) {
    if (b.kind === "fg_warehouse") { drawFgw(ctx, b, f, k, lang); return; }
    if (b.kind === "staff") return;                       // drawn with the people (floor-people.js)
    const T = (en, sk) => (lang === "sk" ? sk : en);
    fillRR(ctx, b.x, b.y, b.w, b.h, 6, P.building, P.buildingEdge, 1);
    const ls = Math.max(5.5, Math.min(13, 10 / k));
    fitText(ctx, lang === "sk" ? b.name_sk : b.name, b.x + b.w / 2, b.y + 4 + ls * 0.55, ls, b.w - 8, P.label, "center", "600");
    if (b.kind === "goods_in") {
      const blocks = Math.max(1, Math.floor((b.h - 40) / 124));
      for (let r = 0; r < blocks; r++) {
        const y = b.y + 24 + r * ((b.h - 40) / blocks);
        const hh = (b.h - 40) / blocks - 12;
        rect(ctx, b.x + 8, y, b.w - 26, hh, P.shelf);
        ctx.beginPath();
        for (let i = 0; i < 14; i++) if (i % 5 !== 3) ctx.rect(b.x + 10, y + 3 + i * (hh - 6) / 14, b.w - 30, (hh - 6) / 14 - 1.6);
        ctx.fillStyle = "#6b86b0"; ctx.fill();
      }
      if (k > 0.7) text(ctx, T("bare panels · parts", "holé panely · súčiastky"), b.x + b.w / 2, b.y + b.h - 26, 5, P.textDim, "center");
      const r = Floor.layout.racks && Floor.layout.racks.gi;
      if (r) {                                                        // the rack where robots take bins and box stacks
        fillRR(ctx, r.x, r.y, r.w, r.h, 1, P.shelf, P.rackEdge, 0.5);
        if (k > 1.2) text(ctx, T("for the robots", "pre roboty"), r.x + r.w / 2, r.y - 2.5, 3.6, P.textDim, "center", "600");
      }
      return;
    }
    const sm = f && f.sm, fg = f && f.fg;
    for (const rk of b.racks || []) {
      const prod = rk.product, st = style(prod);
      const y0 = Math.max(b.y + 18, rk.y0), y1 = Math.min(b.y + b.h - 8, rk.y1);
      const h = y1 - y0 - 12;
      if (b.kind === "supermarket") {
        const stock = (sm && sm[rk.pcba]) || 0;
        const safety = (Floor.layout.products[prod] || {}).safety_stock || 1;
        const cap = Math.max(stock, safety * 1.6, 1);
        const cols = 6, rows = Math.max(3, Math.round(h / 11));
        shelfGrid(ctx, b.x + 8, y0 + 12, b.w - 16, h, cols, rows, Math.round(cols * rows * stock / cap), st.pcb, st.edge);
        const my = y0 + 12 + h - h * safety / cap;
        line(ctx, b.x + 6, my, b.x + b.w - 6, my, "#e8453c", 0.6);
        if (k > 0.5) {
          text(ctx, st.name, b.x + 9, y0 + 6, 5.6, P.label, "left", "700");
          text(ctx, String(stock), b.x + b.w - 9, y0 + 6, 5.6, stock < safety ? "#e8453c" : P.textDim, "right", "600");
        }
      } else {
        const stock = fg && fg[prod] ? fg[prod] : { pallets: 0, units: 0 };
        const cap = Math.max(24, Math.ceil(stock.pallets * 1.25));
        const cols = 6, rows = Math.max(2, Math.round(h / 12));
        shelfGrid(ctx, b.x + 8, y0 + 12, b.w - 16, h, cols, rows, Math.min(cols * rows, Math.round(cols * rows * stock.pallets / cap)), "#a47a46", "#6b4d27");
        if (k > 0.5) {
          text(ctx, st.name, b.x + 9, y0 + 6, 5.6, P.label, "left", "700");
          text(ctx, T(`${stock.pallets} pallets`, `${stock.pallets} paliet`), b.x + b.w - 9, y0 + 6, 5, P.textDim, "right");
        }
      }
    }
    if (b.kind === "supermarket" && k > 0.5) text(ctx, T("boards in stock · red line = safety stock", "dosky na sklade · červená čiara = poistná zásoba"), b.x + b.w / 2, b.y + b.h - 6, 3.6, P.textDim, "center");
  }

  /* The repair centre is a room: its door in the north wall, the pass-through racks in that wall (a robot
     puts pieces in from the aisle, the repair people take them out inside), the quarantine cage with its
     hatch, the scrap bin and the benches (drawn as machines). */
  function drawRepairCentre(ctx, b, f, k, lang) {
    const T = (en, sk) => (lang === "sk" ? sk : en);
    fillRR(ctx, b.x, b.y, b.w, b.h, 2, P.building, null);
    const wall = P.buildingEdge, lw = 1.6;
    const gaps = [[b.door[0] - b.door_w / 2, b.door[0] + b.door_w / 2]];
    for (const key of ["outrack", "inrack"]) gaps.push([b[key].x, b[key].x + b[key].w]);
    const q = b.quarantine;
    gaps.push([q.x + 30, q.x + 62]);                                         // the hatch of the cage
    gaps.sort((g, h) => g[0] - h[0]);
    let x = b.x;
    for (const [g0, g1] of gaps) { line(ctx, x, b.y, g0, b.y, wall, lw); x = g1; }
    line(ctx, x, b.y, b.x + b.w, b.y, wall, lw);
    line(ctx, b.x, b.y, b.x, b.y + b.h, wall, lw);
    line(ctx, b.x + b.w, b.y, b.x + b.w, b.y + b.h, wall, lw);
    line(ctx, b.x, b.y + b.h, b.x + b.w, b.y + b.h, wall, lw);
    if (k > 0.8) {                                                           // the door leaf, open
      ctx.strokeStyle = wall; ctx.lineWidth = 0.6;
      ctx.beginPath(); ctx.arc(b.door[0] - b.door_w / 2, b.y, b.door_w, 0, Math.PI / 2); ctx.stroke();
    }
    const rs = Math.max(5.5, Math.min(13, 10 / k));
    text(ctx, lang === "sk" ? b.name_sk : b.name, b.x + 10, b.y + b.h - rs * 0.9, rs, P.label, "left", "600");
    // pass-through racks
    for (const [key, cols, rows, label] of [["inrack", b.inrack.cols, 3, T("IN · waiting for repair", "VSTUP · čaká na opravu")],
                                          ["outrack", b.outrack.cols, 2, T("OUT · for the robot", "VÝSTUP · pre robota")]]) {
      const r = b[key];
      fillRR(ctx, r.x, r.y, r.w, r.h, 1.5, P.shelf, P.rackEdge, 0.6);
      const cw = r.w / cols, rh = r.h / rows;
      ctx.strokeStyle = P.rackEdge; ctx.lineWidth = 0.3;
      ctx.beginPath();
      for (let i = 1; i < cols; i++) { ctx.moveTo(r.x + i * cw, r.y + 1); ctx.lineTo(r.x + i * cw, r.y + r.h - 1); }
      for (let j = 1; j < rows; j++) { ctx.moveTo(r.x + 1, r.y + j * rh); ctx.lineTo(r.x + r.w - 1, r.y + j * rh); }
      ctx.stroke();
      if (k > 0.8) text(ctx, label, r.x + r.w / 2, r.y - 4, 4, P.textDim, "center", "600");
    }
    // the quarantine cage: wire mesh, locked; its hatch to the aisle
    fillRR(ctx, q.x, q.y, q.w, q.h, 1.5, P.dark ? "rgba(217,130,43,.08)" : "rgba(217,130,43,.10)", "#d9822b", 1);
    if (k > 1.1) {
      ctx.save(); ctx.beginPath(); ctx.rect(q.x, q.y, q.w, q.h); ctx.clip();
      ctx.strokeStyle = "rgba(217,130,43,.35)"; ctx.lineWidth = 0.3; ctx.beginPath();
      for (let d = -q.h; d < q.w; d += 4) { ctx.moveTo(q.x + d, q.y); ctx.lineTo(q.x + d + q.h, q.y + q.h); ctx.moveTo(q.x + d + q.h, q.y); ctx.lineTo(q.x + d, q.y + q.h); }
      ctx.stroke(); ctx.restore();
    }
    line(ctx, q.x + 2, q.y + 15, q.x + q.w - 2, q.y + 15, "#d9822b", 0.5);    // the shelf at the hatch: released pieces
    const sc = b.scrap;
    fillRR(ctx, sc.x, sc.y, sc.w, sc.h, 2, P.dark ? "#3a4352" : "#8d97a8", P.rackEdge, 0.8);
    rect(ctx, sc.x + 3, sc.y + 3, sc.w - 6, sc.h - 6, P.dark ? "#262e3a" : "#6f7a8c");
    const nq = f ? (f.cage_n ?? f.quar ?? 0) : 0;
    const ns = f ? Object.values(f.scrap || {}).reduce((a, b2) => a + b2, 0) : 0;
    for (let i = 0; i < Math.min(ns, 20); i++) rect(ctx, sc.x + 5 + (i % 10) * 8.2, sc.y + 6 + Math.floor(i / 10) * 11, 6, 7, "#7a8496");
    if (k > 0.8) {
      text(ctx, `${T("quarantine", "karanténa")} ${nq}`, q.x + q.w / 2, q.y + q.h + 4.5, 4.2, "#d9822b", "center", "600");
      text(ctx, `${T("scrap", "šrot")} ${ns}`, sc.x + sc.w / 2, sc.y + sc.h + 5, 4.2, P.textDim, "center");
    }
  }

  return { P, style, PRODUCTS, sizes, itemSize, fgwCells, rr, fillRR, rect, line, circle, text, fitText, person, has, frac, drawItem, drawRoutes,
    drawLineBelts, drawBuilding, drawRepairCentre, drawCrossings, loadPalette };
})();
