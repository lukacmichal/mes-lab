/* A canvas that shows the plant from above: camera (pan, zoom, fly-to), drawing order,
   hit-testing. Used for the whole plant, for one line and for the close-up of a station. */
class FloorView {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.opts = { interactive: true, onSelect: null, onHover: null, ...opts };
    this.cam = { x: 0, y: 0, k: 1 };
    this.fly = null;
    this.visible = true;
    this.hover = null;
    this.selected = null;     // {line, station}
    this.focusLine = opts.line || null;
    this.focusStation = opts.station || null;
    this.fitted = false;
    this.size = { w: 0, h: 0, dpr: 1 };
    this.lastDraw = 0;
    this.pointers = new Map();
    this.drag = null;
    this.userMoved = false;
    this.following = null;      // {kind: "person" | "piece" | "robot", id}
    if (window.IntersectionObserver) {
      new IntersectionObserver((entries) => { this.visible = entries[entries.length - 1].isIntersecting; }).observe(canvas);
    }
    if (window.ResizeObserver) new ResizeObserver(() => this.resize()).observe(canvas);
    if (this.opts.interactive) this.bind();
    Floor.views.add(this);
  }

  destroy() { Floor.views.delete(this); }

  // ---------------------------------------------------------------- size and camera
  resize() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    if (w !== this.size.w || h !== this.size.h || dpr !== this.size.dpr) {
      this.size = { w, h, dpr };
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
      if (!this.userMoved) this.fitted = false;
    }
  }

  bounds() {
    const L = Floor.layout;
    if (this.focusStation) {
      const st = L.stations[this.focusStation];
      const info = L.lines[st.line];
      const cell = info.cells[st.cell];
      const left = cell.buf ? cell.buf.x0 : st.x - 14;
      const right = st.x + st.w + 10;
      const lanes = info.cells[st.cell].stations.map((c) => L.stations[c]);
      const top = Math.min(...lanes.map((s) => s.y)) - 8, bottom = Math.max(...lanes.map((s) => s.y + s.h)) + 18;
      return { x: left, y: top, w: right - left, h: bottom - top };
    }
    if (this.focusLine) return this.lineRect(this.focusLine);
    return { x: 0, y: 0, w: L.world.w, h: L.world.h };
  }

  /* the band of the plan that belongs to a line: its machines, its belts and its aisle */
  lineBand(code) {
    const info = Floor.layout.lines[code];
    const top = info.kind === "SMT" ? info.spine - 50 : info.y1 - 18;
    const bottom = info.spine + 100;                 // the walkway and the robot lane of its aisle
    return { x: info.x1 - 70, y: top, w: info.x2 - info.x1 + 100, h: bottom - top };
  }

  fit(rect = this.bounds(), pad = 8, animate = false) {
    const { w, h } = this.size;
    const k = Math.min(w / (rect.w + pad * 2), h / (rect.h + pad * 2));
    this.flyTo({ x: rect.x + rect.w / 2, y: rect.y + rect.h / 2, k }, animate ? 700 : 0);
  }

  flyTo(target, ms = 600) {
    if (!ms) { this.cam = { ...target }; this.fly = null; return; }
    this.fly = { from: { ...this.cam }, to: target, t0: performance.now(), ms };
  }

  focusOn(rect, animate = true) {
    this.userMoved = true;
    this.fit(rect, 10, animate);
  }

  minK() { const b = Floor.layout ? { x: 0, y: 0, w: Floor.layout.world.w, h: Floor.layout.world.h } : null; return b ? Math.min(this.size.w / b.w, this.size.h / b.h) * 0.8 : 0.1; }

  toWorld(px, py) {
    const { w, h } = this.size, c = this.cam;
    return { x: (px - w / 2) / c.k + c.x, y: (py - h / 2) / c.k + c.y };
  }

  // ---------------------------------------------------------------- input
  bind() {
    const c = this.canvas;
    c.style.touchAction = "none";
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      const px = e.clientX - r.left, py = e.clientY - r.top;
      const before = this.toWorld(px, py);
      const k = Math.min(16, Math.max(this.minK(), this.cam.k * Math.exp(-e.deltaY * 0.0016)));
      this.fly = null;
      this.cam.k = k;
      this.cam.x = before.x - (px - this.size.w / 2) / k;
      this.cam.y = before.y - (py - this.size.h / 2) / k;
      this.userMoved = true;
    }, { passive: false });
    c.addEventListener("pointerdown", (e) => {
      this.following = null;
      this.zoomTo = null;
      c.setPointerCapture(e.pointerId);
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      this.drag = { x: e.clientX, y: e.clientY, cx: this.cam.x, cy: this.cam.y, moved: false, pinch: null };
      this.fly = null;
    });
    c.addEventListener("pointermove", (e) => {
      const r = c.getBoundingClientRect();
      if (this.pointers.has(e.pointerId)) this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.pointers.size === 2 && this.drag) {
        const [a, b] = [...this.pointers.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (this.drag.pinch) {
          const f = dist / this.drag.pinch;
          const mid = this.toWorld((a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
          this.cam.k = Math.min(16, Math.max(this.minK(), this.cam.k * f));
          this.cam.x = mid.x - ((a.x + b.x) / 2 - r.left - this.size.w / 2) / this.cam.k;
          this.cam.y = mid.y - ((a.y + b.y) / 2 - r.top - this.size.h / 2) / this.cam.k;
        }
        this.drag.pinch = dist; this.drag.moved = true; this.userMoved = true;
        return;
      }
      if (this.drag && this.pointers.size === 1 && (e.buttons & 1)) {
        const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) this.drag.moved = true;
        if (this.drag.moved) {
          this.cam.x = this.drag.cx - dx / this.cam.k;
          this.cam.y = this.drag.cy - dy / this.cam.k;
          this.userMoved = true;
          c.style.cursor = "grabbing";
        }
        return;
      }
      const w = this.toWorld(e.clientX - r.left, e.clientY - r.top);
      const hit = this.hit(w.x, w.y);
      const key = hit ? `${hit.line}|${hit.station || ""}|${hit.building || ""}|${hit.person || ""}|${hit.piece || ""}|${hit.pallet || ""}|${hit.truck || ""}|${hit.robot || ""}|${hit.thing ?? ""}` : "";
      this.hover = hit;
      c.style.cursor = hit ? "pointer" : "grab";
      if (this.opts.onHover) this.opts.onHover(hit, e.clientX, e.clientY, key);
    });
    const up = (e) => {
      const wasDrag = this.drag && this.drag.moved;
      this.pointers.delete(e.pointerId);
      c.style.cursor = "grab";
      if (this.drag && !wasDrag && this.pointers.size === 0) {
        const r = c.getBoundingClientRect();
        const w = this.toWorld(e.clientX - r.left, e.clientY - r.top);
        const hit = this.hit(w.x, w.y);
        this.selected = hit;
        if (this.opts.onSelect) this.opts.onSelect(hit, this, e);
      }
      if (this.pointers.size === 0) this.drag = null;
    };
    c.addEventListener("pointerup", up);
    c.addEventListener("pointercancel", up);
    c.addEventListener("pointerleave", () => { this.hover = null; if (this.opts.onHover) this.opts.onHover(null); });
    c.addEventListener("dblclick", (e) => {
      const r = c.getBoundingClientRect();
      const w = this.toWorld(e.clientX - r.left, e.clientY - r.top);
      const hit = this.hit(w.x, w.y);
      if (hit && hit.station) this.focusOn(Floor.layout.stations[hit.station], true);
      else if (hit && hit.line) this.focusOn(this.lineRect(hit.line), true);
    });
  }

  lineRect(code) { return this.lineBand(code); }

  /* keep the camera on a person or a piece and zoom in on him */
  follow(kind, id, k = 5) {
    this.following = { kind, id };
    this.userMoved = true;
    this.zoomTo = Math.max(this.cam.k, k);              // the camera zooms in while it keeps on them
  }

  /* what is at a world point: a station, the line it belongs to, or a building */
  hit(x, y) {
    const L = Floor.layout;
    if (!L) return null;
    const k = this.cam.k;
    // people first, then the pieces (when the plan is close enough to point at one), then machines
    const reach = 6 / k;
    let best = null, bestD = reach;
    for (const p of Floor.S.people.values()) {
      if (FloorPeople.hidden(p)) continue;                          // in a toilet cabin
      const d = Math.hypot(p.x - x, p.y - y);
      if (d < bestD) { best = p; bestD = d; }
    }
    if (best) return { person: best.id, line: Floor.S.layout.seats[best.rec.seat] ? Floor.S.layout.seats[best.rec.seat].line : null };
    const [rl, rw] = L.amr ? L.amr.size : [32, 21];
    for (const r of Floor.S.robots.values()) {                     // a robot: its body, turned the way it points
      const a = r.ang || 0, s = Math.max(1, 0.75 / k);
      const dx = x - r.x, dy = y - r.y;
      const u = dx * Math.cos(a) + dy * Math.sin(a), v = -dx * Math.sin(a) + dy * Math.cos(a);
      if (Math.abs(u) <= rl / 2 * s + 1 && Math.abs(v) <= rw / 2 * s + 1) return { robot: r.id };
    }
    if (k >= 1.2) {
      let nearest = null, nd = Infinity;
      for (const s of Floor.S.sprites.values()) {
        const [w, h] = FloorArt.itemSize(s.desc);
        if (Math.abs(s.x - x) <= w / 2 + 0.6 && Math.abs(s.y - y) <= h / 2 + 0.6) {
          const d = Math.hypot(s.x - x, s.y - y);
          if (d < nd) { nearest = s; nd = d; }
        }
      }
      if (nearest) return { piece: nearest.id, line: nearest.st ? Floor.S.layout.stations[nearest.st].line : null, station: nearest.st || null };
    }
    const fgw = L.buildings.find((b) => b.id === "FGW");
    if (fgw) {
      const cell = FloorArt.fgwCells(fgw, Floor.floor).cells.find((c) => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
      if (cell) return { building: "FGW", pallet: cell.sn, product: cell.product };
    }
    for (const t of Floor.trucksNow()) {
      const pose = Floor.truckPose(t);
      const bay = L.bays.find((b) => b.bay === t.bay);
      if (pose && bay && Math.abs(x - pose.x) <= bay.trailer_w / 2 && y >= pose.y && y <= pose.y + bay.trailer_l + bay.cab_l) return { truck: t.i, building: "FGW" };
    }
    for (const [code, st] of Object.entries(L.stations)) {
      if (x >= st.x && x <= st.x + st.w && y >= st.y && y <= st.y + st.h) return { line: st.line, station: code };
    }
    for (const [code, info] of Object.entries(L.lines)) {
      if (info.kind === "REPAIR") continue;
      if (x >= info.x1 - 60 && x <= info.x2 + 4 && y >= info.y1 - 12 && y <= info.y2 + 8) return { line: code };
    }
    if ((typeof FloorBoards !== "undefined") && !this.focusLine && !this.opts.closeup) {     // the hour-by-hour screen over a line
      const board = FloorBoards.hit(L, x, y);
      if (board) return { line: board, board: true };
    }
    if (k >= 0.7) {                                                   // a thing in a room: what it is
      const thing = FloorPeople.thingAt(x, y);
      if (thing !== null) return { building: "STAFF", thing };
    }
    for (const b of L.buildings) {
      if (x >= b.x && x <= b.x + b.w && y >= b.y - 6 && y <= b.y + b.h) return { building: b.id };
    }
    const d = L.depot;
    if (d && x >= d.x && x <= d.x + d.w && y >= d.y && y <= d.y + d.h) return { building: "DEPOT" };
    return null;
  }

  // ---------------------------------------------------------------- drawing
  draw(now) {
    const L = Floor.layout;
    if (!L || !this.visible) return;
    this.resize();
    const { w, h, dpr } = this.size;
    if (w < 2 || h < 2) return;
    if (!this.fitted) { this.fit(this.bounds(), this.focusStation ? 4 : 8, false); this.fitted = true; }
    if (this.fly) {
      const f = Math.min(1, (now - this.fly.t0) / this.fly.ms);
      const e = f < 0.5 ? 2 * f * f : 1 - Math.pow(-2 * f + 2, 2) / 2;
      const { from, to } = this.fly;
      // zoom in log space so that it feels even
      this.cam.k = Math.exp(Math.log(from.k) + (Math.log(to.k) - Math.log(from.k)) * e);
      this.cam.x = from.x + (to.x - from.x) * e;
      this.cam.y = from.y + (to.y - from.y) * e;
      if (f >= 1) this.fly = null;
    }
    if (this.following) {
      const kind = this.following.kind;
      const who = kind === "person" ? Floor.S.people.get(this.following.id) : kind === "robot" ? Floor.S.robots.get(this.following.id) : Floor.S.sprites.get(this.following.id);
      if (who) {
        this.fly = null;
        this.cam.x += (who.x - this.cam.x) * 0.12; this.cam.y += (who.y - this.cam.y) * 0.12;
        if (this.zoomTo) {
          this.cam.k *= Math.pow(this.zoomTo / this.cam.k, 0.1);
          if (Math.abs(this.cam.k / this.zoomTo - 1) < 0.01) this.zoomTo = null;
        }
      } else this.following = null;
    }
    const ctx = this.ctx, A = FloorArt, P = A.P, k = this.cam.k;
    const t = now / 1000;
    const lang = typeof MES !== "undefined" ? MES.lang() : "en";
    const floor = Floor.floor;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = P.floor;
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.setTransform(k * dpr, 0, 0, k * dpr, (w / 2 - this.cam.x * k) * dpr, (h / 2 - this.cam.y * k) * dpr);
    ctx.save();
    if (this.focusLine) {          // a line page shows only its own line
      const band = this.lineBand(this.focusLine);
      ctx.beginPath(); ctx.rect(band.x, band.y, band.w, band.h); ctx.clip();
    }
    const view = { x0: this.cam.x - w / 2 / k - 20, y0: this.cam.y - h / 2 / k - 20, x1: this.cam.x + w / 2 / k + 20, y1: this.cam.y + h / 2 / k + 20 };
    const seen = (r) => r.x + (r.w || 0) >= view.x0 && r.x <= view.x1 && r.y + (r.h || 0) >= view.y0 && r.y <= view.y1;

    this.drawGrid(ctx, view, k);
    if (!this.opts.closeup) for (const z of L.zones) this.drawZone(ctx, z, k, lang);

    const running = {};
    if (floor) {
      for (const [code, info] of Object.entries(L.lines)) {
        const st = info.cells.flatMap((c) => c.stations);
        running[code] = st.some((s) => floor.stations[s] && floor.stations[s].s === "RUNNING");
      }
    }
    A.drawRoutes(ctx, L, k, t, new Set(floor ? floor.tr.map((r) => r.r) : []));
    if (!this.opts.closeup) FloorRobots.drawLanes(ctx, L, k);
    A.drawLineBelts(ctx, L, k, t, running);
    const sprites = [...Floor.S.sprites.values(), ...Floor.S.dying];
    const under = (s) => s.loc && s.loc.t === "ride" && s.loc.route.startsWith("ret:");     // on the belt under the machines
    for (const s of sprites) {
      if (!under(s) || s.x < view.x0 || s.x > view.x1 || s.y < view.y0 || s.y > view.y1) continue;
      A.drawItem(ctx, { ...s, alpha: s.alpha * 0.55 }, k);
    }

    for (const b of L.buildings) {
      if (!seen({ x: b.x, y: b.y, w: b.w + 60, h: b.h })) continue;
      if (b.kind === "repair") A.drawRepairCentre(ctx, b, floor, k, lang);
      else A.drawBuilding(ctx, b, floor, k, lang);
    }
    this.drawWashers(ctx, L, k, lang);

    if (!this.opts.closeup && !this.focusLine && (typeof FloorBoards !== "undefined")) FloorBoards.draw(ctx, L, k, view);
    FloorPeople.drawWalkways(ctx, L, k);
    const staff = L.buildings.find((b) => b.id === "STAFF");
    if (staff && (seen(staff) || (L.corner && seen(L.corner)))) FloorPeople.drawStaff(ctx, L, floor, k, lang, t);
    FloorPeople.drawDock(ctx, L, floor, k, lang, { seen });
    if (!this.opts.closeup) FloorPeople.drawBoard(ctx, L, floor, k, lang);
    if (L.depot && seen(L.depot)) FloorRobots.drawDepot(ctx, L, floor, k, lang);

    const stationInfo = {};
    const visibleStations = [];
    for (const [code, st] of Object.entries(L.stations)) {
      if (!seen(st)) continue;
      stationInfo[code] = Floor.stationInfo(code);
      visibleStations.push(code);
    }
    const extraOf = (st) => ({ panels: floor && floor.panels ? floor.panels[st.line] : null });
    for (const code of visibleStations) FloorMachines.draw(ctx, L.stations[code], stationInfo[code], k, t, "base", extraOf(L.stations[code]), lang);

    FloorPeople.drawDesks(ctx, L, floor, k, t);
    FloorRobots.drawNok(ctx, L, floor, k);
    FloorRobots.drawRacks(ctx, L, floor, k);

    const onTop = [];                       // what robots carry and people hold is drawn with them
    for (const s of sprites) {
      if (under(s) || s.x < view.x0 || s.x > view.x1 || s.y < view.y0 || s.y > view.y1) continue;
      if (s.loc && (s.loc.t === "amr" || s.loc.t === "hand")) { onTop.push(s); continue; }
      A.drawItem(ctx, s, k);
    }

    for (const code of visibleStations) FloorMachines.draw(ctx, L.stations[code], stationInfo[code], k, t, "top", extraOf(L.stations[code]), lang);

    for (const r of Floor.S.robots.values()) {
      if (r.x < view.x0 - 30 || r.x > view.x1 + 30 || r.y < view.y0 - 30 || r.y > view.y1 + 30) continue;
      FloorRobots.drawPath(ctx, r, k);
      FloorRobots.drawRobot(ctx, r, k, t);
    }
    for (const s of onTop) if (s.loc.t === "amr") A.drawItem(ctx, s, k);
    for (const p of Floor.S.people.values()) {
      if (p.x < view.x0 || p.x > view.x1 || p.y < view.y0 || p.y > view.y1) continue;
      FloorPeople.drawPerson(ctx, p, k, t);
    }
    for (const s of onTop) if (s.loc.t === "hand") A.drawItem(ctx, s, k);
    A.drawCrossings(ctx, L, k);
    FloorPeople.drawLabels(ctx, L, k, view);

    if (!this.opts.closeup) this.drawLineLabels(ctx, L, k, lang, view);
    this.drawSelection(ctx, L, k);
    ctx.restore();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (!floor) {
      ctx.fillStyle = P.textDim; ctx.font = "14px sans-serif"; ctx.textAlign = "center";
      ctx.fillText(lang === "sk" ? "Čakám na údaje zo závodu…" : "Waiting for the plant…", this.canvas.width / 2, this.canvas.height / 2);
    }
  }

  drawGrid(ctx, view, k) {
    if (k < 0.3) return;
    ctx.strokeStyle = FloorArt.P.grid; ctx.lineWidth = 1 / k;
    ctx.beginPath();
    const step = 40;
    for (let x = Math.floor(view.x0 / step) * step; x < view.x1; x += step) { ctx.moveTo(x, view.y0); ctx.lineTo(x, view.y1); }
    for (let y = Math.floor(view.y0 / step) * step; y < view.y1; y += step) { ctx.moveTo(view.x0, y); ctx.lineTo(view.x1, y); }
    ctx.stroke();
  }

  drawZone(ctx, z, k, lang) {
    const A = FloorArt, P = A.P;
    A.fillRR(ctx, z.x, z.y, z.w, z.h, 8, P.zone, P.zoneLine, 1 / Math.max(k, 0.3));
    const size = Math.max(9, 12 / k);
    A.text(ctx, lang === "sk" ? z.name_sk : z.name, z.x + 12, z.y + size * 0.9, size, P.textDim, "left", "700");
  }

  drawWashers(ctx, L, k, lang) {
    if (k < 0.7) return;
    const A = FloorArt, P = A.P;
    for (const [key, r] of Object.entries(L.routes)) {
      if (r.kind !== "wash") continue;
      const [x, y] = r.pts[3];
      const n = Floor.floor ? Floor.floor.tr.filter((t) => t.r === key).length : 0;
      A.fillRR(ctx, x - 11, y - 6, 22, 9, 2, P.building, P.washer, 0.8);
      if (n) A.rect(ctx, x - 9.5, y - 4.5, 19, 6, "rgba(79,134,180,.35)");
      if (k > 1.3) A.text(ctx, (lang === "sk" ? "umývanie" : "wash") + (n ? ` ${n}` : ""), x, y - 1.5, 3.8, P.textDim, "center", "600");
    }
  }

  drawLineLabels(ctx, L, k, lang, view) {
    const A = FloorArt, P = A.P;
    const floor = Floor.floor;
    const size = Math.max(7, Math.min(22, 11 / k));
    if (k > 3.2) return;
    for (const [code, info] of Object.entries(L.lines)) {
      if (info.kind === "REPAIR") continue;
      if (info.x2 < view.x0 || info.x1 > view.x1 || info.y2 < view.y0 || info.y1 > view.y1) continue;
      const first = info.cells[0].stations[0];
      const e = floor && floor.stations[first];
      let sub = "";
      if (e && e.jm) {
        const prod = e.jm.replace("PCBA-", "");
        const side = e.jo === "SMT_BOT" ? (lang === "sk" ? " · spodná" : " · bottom") : e.jo === "SMT_TOP" && L.products[prod] && L.products[prod].two_sided ? (lang === "sk" ? " · vrchná" : " · top") : "";
        sub = prod + side;
      }
      if (info.materials && info.materials.length && !sub) sub = info.materials.join(", ");
      const x = info.x1, y = info.y1 - (k > 0.85 ? 13 : 5);
      const st = e ? e.s : "UNKNOWN";
      const noWork = info.job_line && e && e.s === "NO_ORDER";
      A.text(ctx, code, x, y - size * 0.2, size, P.label, "left", "700");
      if (sub && k > 0.5) {
        const prodKey = (sub.split(" ")[0] || "");
        const color = A.style(prodKey).pcb;
        const cw = ctx.measureText(code).width;
        A.fillRR(ctx, x + cw + size * 0.5, y - size * 0.75, size * 0.7, size * 0.7, 1, color, null);
        A.text(ctx, noWork ? (lang === "sk" ? "bez zákazky" : "no order") : sub, x + cw + size * 1.4, y - size * 0.2, size * 0.8, P.textDim, "left", "500");
      }
    }
  }

  drawSelection(ctx, L, k) {
    const A = FloorArt;
    const mark = (r, color) => {
      ctx.strokeStyle = color; ctx.lineWidth = 2 / k;
      A.rr(ctx, r.x - 3, r.y - 3, r.w + 6, r.h + 6, 5); ctx.stroke();
    };
    const sel = this.selected;
    if (sel && sel.robot && Floor.S.robots.get(sel.robot)) {
      const r = Floor.S.robots.get(sel.robot);
      ctx.strokeStyle = "#2f73e0"; ctx.lineWidth = 1.6 / k;
      ctx.beginPath(); ctx.arc(r.x, r.y, 21 * Math.max(1, 0.75 / k), 0, Math.PI * 2); ctx.stroke();
    } else if (sel && sel.person && Floor.S.people.get(sel.person)) {
      const p = Floor.S.people.get(sel.person);
      ctx.strokeStyle = "#2f73e0"; ctx.lineWidth = 1.6 / k;
      ctx.beginPath(); ctx.arc(p.x, p.y, 8.5 * Math.max(1, 0.95 / k), 0, Math.PI * 2); ctx.stroke();
    } else if (sel && sel.piece && Floor.S.sprites.get(sel.piece)) {
      const sp = Floor.S.sprites.get(sel.piece);
      const [w, h] = FloorArt.itemSize(sp.desc);
      ctx.strokeStyle = "#2f73e0"; ctx.lineWidth = 1.6 / k;
      A.rr(ctx, sp.x - w / 2 - 1.5, sp.y - h / 2 - 1.5, w + 3, h + 3, 2); ctx.stroke();
    } else if (sel && sel.station) mark(L.stations[sel.station], "#2f73e0");
    else if (sel && sel.line && !sel.person && !sel.piece && !sel.robot) {
      const i = L.lines[this.selected.line];
      mark({ x: i.x1 - 4, y: i.y1 - 4, w: i.x2 - i.x1 + 8, h: i.y2 - i.y1 + 8 }, "#2f73e0");
    }
    if (this.hover && this.hover.station && !(this.selected && this.selected.station === this.hover.station)) {
      const st = L.stations[this.hover.station];
      ctx.strokeStyle = "rgba(47,115,224,.7)"; ctx.lineWidth = 1.2 / k;
      A.rr(ctx, st.x - 1.5, st.y - 1.5, st.w + 3, st.h + 3, 4); ctx.stroke();
    }
  }
}
