/* The live world: floor plan, snapshots of the plant, simulation time and the moving
   pieces. No drawing here (see floor-art.js, floor-machines.js, floor-view.js).

   The plant reports once a second where every piece is (in a station, in the queue in
   front of it, on a conveyor) and how far each machine has got with its cycle. This
   module follows every piece by its id: a piece that moves to another place glides
   there instead of jumping, and machine progress is extrapolated with the measured
   simulation speed, so what you see never runs ahead of what the plant says.

   The picture runs a little behind the plant (DELAY_WALL, a bit more than one look at it): every
   snapshot is shown when the clock of the picture reaches its time, and by then the next one has
   told how everybody got from there to here. People and robots come with their walk of the moment
   and the walks just before it, so they are drawn along the real way at their real speed, even at
   60x where a whole walk begins and ends between two looks; pieces ride the conveyors they really
   rode (`trh`: the rides that just ended) and get onto a robot, into a rack or into somebody's hands
   at the moment they really did (`at`). Pieces that leave a line for repair are never in the air
   either: in the NOK tote at the machine, on a robot, in a rack of the repair centre or in the hands
   of a repair person. Nothing glides far in a straight line: a jump the plant cannot explain (a jump
   in time, a speed change) fades out and in instead of flying through the walls. */
const Floor = (() => {
  const S = {
    layout: null, floor: null, ready: false, age: null, run: null,
    snapSim: 0, snapWall: 0, rate: 1, sim: 0, speed: 1,
    newest: null, queue: [], floorT: 0, timed: new Map(),
    sprites: new Map(), dying: [], frame: 0, people: new Map(), peopleFrame: 0, robots: new Map(),
    routes: {}, stationList: [], lineOfStation: {},
  };
  const views = new Set();
  const listeners = new Set();
  const GLIDE_S = 0.32;      // time constant of a piece gliding to its new place (wall seconds)
  const POLL_MS = 1000;
  const DELAY_WALL = 1.3;    // the picture shows the plant this many wall seconds late (one look + its jitter)
  const FAR_PIECE = 30;      // a piece never glides farther than this straight: it fades out and in
  const FAR_PERSON = 40;

  // ------------------------------------------------------------------ geometry helpers
  function prepareRoute(route) {
    const pts = route.pts;
    const cum = [0];
    for (let i = 1; i < pts.length; i++) {
      cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    }
    route.cum = cum;
    route.total = cum[cum.length - 1] || 1;
    return route;
  }

  /* point at distance s along a route, with the direction and the length of its segment */
  function alongRoute(route, s) {
    const { pts, cum } = route;
    if (s <= 0 || pts.length < 2) {
      const seg = pts.length > 1 ? Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]) || 1 : 1;
      return { x: pts[0][0], y: pts[0][1], dx: pts.length > 1 ? (pts[1][0] - pts[0][0]) / seg : 1, dy: pts.length > 1 ? (pts[1][1] - pts[0][1]) / seg : 0, len: seg };
    }
    for (let i = 1; i < pts.length; i++) {
      if (s <= cum[i]) {
        const seg = cum[i] - cum[i - 1] || 1;
        const f = (s - cum[i - 1]) / seg;
        return { x: pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f,
                 y: pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f,
                 dx: (pts[i][0] - pts[i - 1][0]) / seg, dy: (pts[i][1] - pts[i - 1][1]) / seg, len: seg };
      }
    }
    const n = pts.length - 1;
    const seg = Math.hypot(pts[n][0] - pts[n - 1][0], pts[n][1] - pts[n - 1][1]) || 1;
    return { x: pts[n][0], y: pts[n][1], dx: (pts[n][0] - pts[n - 1][0]) / seg, dy: (pts[n][1] - pts[n - 1][1]) / seg, len: seg };
  }

  /* position of a ride at fraction f (0..1) of its time; washing stops at the washer */
  function ridePoint(route, f) {
    f = Math.min(1, Math.max(0, f));
    let g = f;
    if (route.dwell) {
      const [a, b, at] = route.dwell;
      if (f < a) g = (f / a) * at;
      else if (f <= b) g = at;
      else g = at + ((f - b) / (1 - b)) * (1 - at);
    }
    return alongRoute(route, g * route.total);
  }

  // ------------------------------------------------------------------ places of pieces
  const SLOT_X = { in: 0.22, w: 0.5, out: 0.78 };

  function stationPlace(code, slot, k = 0, n = 1) {
    const st = S.layout.stations[code];
    if (!st) return null;
    const spread = n > 1 ? (k - (n - 1) / 2) * Math.min(7, (st.h - 8) / n) : 0;
    return { x: st.x + st.w * SLOT_X[slot], y: st.cy + spread };
  }

  function queuePlace(line, cell, k) {
    const info = S.layout.lines[line];
    if (!info) return null;
    const c = info.cells[cell];
    const slot = S.layout.geometry.SLOT;
    if (c && c.buf) return { x: c.buf.x1 - (k + 0.5) * slot, y: info.spine };
    if (info.kind === "REPAIR") {
      const tray = S.layout.buildings.find((b) => b.id === "RPR").tray;
      const cols = Math.max(1, Math.floor(tray.w / slot));
      return { x: tray.x + (k % cols + 0.5) * slot, y: tray.y + (Math.floor(k / cols) + 0.5) * slot };
    }
    return { x: info.x1 - (k + 0.5) * slot - 2, y: info.spine };
  }

  function feedPlace(line, k) {
    const info = S.layout.lines[line];
    return { x: info.x1 - (k + 0.5) * S.layout.geometry.SLOT, y: info.spine };
  }

  /* a slot of a rack: `cols` across, rows from the top */
  function rackSlot(r, k, rows) {
    const cols = r.cols || Math.max(1, Math.floor(r.w / 12));
    const cw = r.w / cols, rh = r.h / rows;
    return { x: r.x + (k % cols + 0.5) * cw, y: r.y + (Math.floor(k / cols) % rows + 0.5) * rh };
  }

  /* the pieces the robots and the repair centre hold */
  function heldPlace(loc) {
    const L = S.layout;
    const rpr = L.buildings.find((b) => b.id === "RPR");
    switch (loc.t) {
      case "box": {
        if (loc.place.startsWith("gap:")) {                          // the red NOK tote at the exit of a cell
          const n = L.nok[loc.place.slice(4)];
          if (!n) return null;
          const k = Math.min(loc.k, 4);
          return { x: n.x + n.w / 2 - k * 0.4, y: n.y + n.h / 2 + (k - 1.5) * 0.9, s: 0.38, a: Math.PI / 2 };
        }
        if (loc.place.startsWith("drop:") && L.racks[loc.place]) {   // the return shelf of a rack at the line: empty bins
          const r = L.racks[loc.place];
          return { x: r.x + r.w / 2, y: r.y + r.h + 2.5 + Math.min(loc.k, 3) * 0.8, s: 0.55 };
        }
        if (loc.place === "gi") {                                    // the dispatch rack of the goods-in warehouse
          const r = L.racks.gi;
          return { ...rackSlot({ ...r, cols: 8 }, loc.k, 2), s: 0.7 };
        }
        if (loc.place === "rpr:out") return { ...rackSlot(rpr.outrack, loc.k, 2), s: 0.8 };
        if (loc.place === "rpr:quar") return { ...rackSlot({ ...rpr.quarantine, h: 14, cols: 7 }, loc.k, 1), s: 0.62 };   // at the hatch
        return null;
      }
      case "rack": return { ...rackSlot(rpr.inrack, loc.k, 3), s: 0.82 };
      case "cage": {
        const c = rpr.quarantine;
        return { ...rackSlot({ x: c.x + 2, y: c.y + 16, w: c.w - 4, h: c.h - 18, cols: 7 }, loc.k, 3), s: 0.62 };
      }
      case "amr": {
        const r = S.robots.get(loc.r);
        if (!r) return null;
        const k = loc.k % 4, a = r.ang || 0;
        const fx = (k % 2 ? 6.6 : -6.6), fy = (k < 2 ? -4.4 : 4.4);           // the four totes on the top module
        return { x: r.x + fx * Math.cos(a) - fy * Math.sin(a), y: r.y + fx * Math.sin(a) + fy * Math.cos(a), s: 0.6, a };
      }
      case "hand": {
        const p = S.people.get(loc.p);
        if (!p) return null;
        return { x: p.x + p.dx * 6.2, y: p.y + p.dy * 6.2, s: 0.72, a: Math.atan2(p.dy, p.dx) };
      }
      default: return null;
    }
  }

  /* where a piece is right now, from its location record */
  function locate(loc) {
    switch (loc.t) {
      case "slot": return stationPlace(loc.code, loc.slot, loc.k, loc.n);
      case "oven": {
        const st = S.layout.stations[loc.code];
        const type = S.layout.types[st.type];
        const f = Math.min(1, Math.max(0, loc.p0 + (loc.moving ? (S.sim - S.floorT) / (type.transit_s || 1) : 0)));
        return { x: st.x + st.w * (0.1 + 0.8 * f), y: st.cy + loc.lane };
      }
      case "queue": return queuePlace(loc.line, loc.cell, loc.k);
      case "feed": return feedPlace(loc.line, loc.k);
      case "ride": {
        const route = S.routes[loc.route];
        if (!route) return null;
        return ridePoint(route, (S.sim - loc.t0) / loc.d);
      }
      default: return heldPlace(loc);
    }
  }

  // ------------------------------------------------------------------ moves along the belts of a line
  function polyLength(pts) {
    let n = 0;
    for (let i = 1; i < pts.length; i++) n += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    return n;
  }

  /* the point at fraction f (0..1) of the length of a polyline */
  function alongPoly(pts, f) {
    const total = polyLength(pts);
    let want = f * total;
    for (let i = 1; i < pts.length; i++) {
      const seg = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      if (want <= seg && seg > 0) {
        const g = want / seg;
        return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * g, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * g];
      }
      want -= seg;
    }
    return pts[pts.length - 1];
  }

  function lineOfLoc(loc) {
    if (!loc) return null;
    if (loc.t === "slot" || loc.t === "oven") return S.lineOfStation[loc.code] || null;
    if (loc.t === "queue" || loc.t === "feed") return loc.line;
    return null;
  }

  /* A piece that moves on within its line follows the conveyors: from a machine of a double cell it is
     lifted onto the main conveyor in the middle (the spine), rides along it and is lifted into the next
     machine. A straight glide would cut across the machines (a piece flying from the screwdriver into the
     upper flasher). Moves inside one machine and out of the line are not belt moves. */
  function beltPath(from, to, x, y, at) {
    const la = lineOfLoc(from), lb = lineOfLoc(to);
    if (!la || la !== lb) return null;
    if (from.t === "slot" && to.t === "slot" && from.code === to.code) return null;
    const info = S.layout.lines[la];
    if (!info || info.kind === "REPAIR") return null;
    const spine = info.spine;
    const pts = [[x, y]];
    if (Math.abs(y - spine) > 0.6) pts.push([x, spine]);
    if (Math.abs(at.y - spine) > 0.6) pts.push([at.x, spine]);
    pts.push([at.x, at.y]);
    return pts;
  }

  // ------------------------------------------------------------------ snapshot -> locations
  function placements(f) {
    const out = [];
    const L = S.layout;
    for (const [code, e] of Object.entries(f.stations)) {
      if (e.it) {
        const slot = e.ph === "in" ? "in" : "w";
        out.push({ id: e.it.i, desc: e.it, loc: { t: "slot", code, slot, k: 0, n: 1 }, st: code });
      }
      if (e.out) {
        e.out.forEach((d, k) => out.push({ id: d.i, desc: d, loc: { t: "slot", code, slot: "out", k, n: e.out.length }, st: code }));
      }
      if (e.ov) {
        const lane = 0;
        const moving = e.belt !== 0 ? 1 : 0;
        e.ov.forEach(([d, frac], k) => out.push({ id: d.i, desc: d, loc: { t: "oven", code, p0: frac, moving, lane }, st: code }));
      }
    }
    for (const [line, cells] of Object.entries(f.bufs)) {
      cells.forEach((q, cell) => q.it.forEach((d, k) =>
        out.push({ id: d.i, desc: d, loc: { t: "queue", line, cell, k } })));
    }
    for (const [line, q] of Object.entries(f.feed || {})) {
      q.it.forEach((d, k) => out.push({ id: d.i, desc: d, loc: { t: "feed", line, k } }));
    }
    for (const r of f.tr || []) {
      out.push({ id: r.it.i, desc: r.it, loc: { t: "ride", route: r.r, t0: r.t, d: r.d } });
    }
    for (const [place, box] of Object.entries(f.boxes || {})) {
      box.forEach((d, k) => out.push({ id: d.i, desc: d, loc: { t: "box", place, k } }));
    }
    for (const r of f.amr || []) (r.ld || []).forEach((d, k) => out.push({ id: d.i, desc: d, loc: { t: "amr", r: r.i, k } }));
    (f.rack || []).forEach((d, k) => out.push({ id: d.i, desc: d, loc: { t: "rack", k } }));
    (f.cage || []).forEach((d, k) => out.push({ id: d.i, desc: d, loc: { t: "cage", k } }));
    for (const p of f.people || []) if (p.h) out.push({ id: p.h.i, desc: p.h, loc: { t: "hand", p: p.i } });
    return out;
  }

  function locKey(loc) {
    switch (loc.t) {
      case "slot": return `s:${loc.code}:${loc.slot}:${loc.k}`;
      case "oven": return `o:${loc.code}`;
      case "queue": return `q:${loc.line}:${loc.cell}:${loc.k}`;
      case "feed": return `f:${loc.line}:${loc.k}`;
      case "ride": return `r:${loc.route}:${loc.t0}`;
      case "box": return `b:${loc.place}:${loc.k}`;
      case "amr": return `a:${loc.r}:${loc.k}`;
      case "rack": return `k:${loc.k}`;
      case "cage": return `c:${loc.k}`;
      case "hand": return `h:${loc.p}`;
      default: return "?";
    }
  }

  function updateSprites(list) {
    S.frame++;
    for (const p of list) {
      let s = S.sprites.get(p.id);
      if (!s) {
        s = { id: p.id, x: 0, y: 0, ox: 0, oy: 0, alpha: 0, fresh: true, key: "", partial: null };
        S.sprites.set(p.id, s);
      }
      s.desc = p.desc;
      s.loc = p.loc;
      s.st = p.st || null;
      s.seen = S.frame;
    }
    for (const [id, s] of S.sprites) {
      if (s.seen !== S.frame) {
        S.sprites.delete(id);
        s.dyingFor = 0;
        S.dying.push(s);
      }
    }
  }

  // ------------------------------------------------------------------ people
  const clamp01 = (v) => Math.min(1, Math.max(0, v));

  /* the walks of a person or a robot: the ones just before and the one of the moment */
  function legsOf(rec) {
    if (rec._legs) return rec._legs;
    const legs = [...(rec.wh || []), ...(rec.w ? [rec.w] : [])];
    for (const w of legs) {
      if (w.cum) continue;
      w.cum = [0];
      for (let i = 1; i < w.p.length; i++) w.cum.push(w.cum[i - 1] + Math.hypot(w.p[i][0] - w.p[i - 1][0], w.p[i][1] - w.p[i - 1][1]));
      w.total = w.cum[w.cum.length - 1] || 1;
      w.pts = w.p;
    }
    rec._legs = legs;
    return legs;
  }

  /* where somebody walking these legs is at time t: on a leg, waiting between two, or done (null) */
  function onLegs(legs, t) {
    if (!legs.length) return null;
    let i = legs.length - 1;
    while (i >= 0 && t < legs[i].t) i--;
    if (i < 0) {                                                   // the page is behind: still at the start
      const a = alongRoute(legs[0], 0);
      return { ...a, walking: false };
    }
    const w = legs[i];
    if (t < w.t + w.d) return { ...alongRoute(w, clamp01((t - w.t) / w.d) * w.total), walking: true };
    if (i < legs.length - 1 || w === legs[legs.length - 1] && legs.current) return { ...alongRoute(w, w.total), walking: false };
    return null;
  }

  /* where a person is right now, and which way he looks */
  function personExact(p) {
    const rec = p.rec;
    const legs = legsOf(rec);
    legs.current = !!rec.w;
    const on = onLegs(legs, S.sim);
    if (on) return { x: on.x, y: on.y, dx: on.dx, dy: on.dy, walking: on.walking };
    let dx = p.dx, dy = p.dy;
    const st = rec.s && S.layout.stations[rec.s];
    if (st) { const a = Math.hypot(st.cx - rec.x, st.cy - rec.y) || 1; dx = (st.cx - rec.x) / a; dy = (st.cy - rec.y) / a; }
    else if (rec.f !== undefined) { dx = Math.cos(rec.f * Math.PI / 180); dy = Math.sin(rec.f * Math.PI / 180); }   // faces the screen / the table
    return { x: rec.x, y: rec.y, dx, dy, walking: false };
  }

  /* when the last walk of a record ends (-inf: no walk) */
  function legsEnd(rec) {
    let end = -Infinity;
    for (const w of legsOf(rec)) end = Math.max(end, w.t + w.d);
    return end;
  }

  function updatePeople(list) {
    S.peopleFrame++;
    for (const rec of list) {
      let p = S.people.get(rec.i);
      if (!p) { p = { id: rec.i, x: 0, y: 0, ox: 0, oy: 0, dx: 0, dy: 1, key: "", alpha: 0, fresh: true }; S.people.set(rec.i, p); }
      p.rec = rec;
      p.seen = S.peopleFrame;
      p.leaving = false;
    }
    // gone from the newest snapshot: on the way out (the picture is a moment behind), drawn until the walk ends
    for (const [id, p] of S.people) {
      if (p.seen === S.peopleFrame) continue;
      if (!p.leaving) { p.leaving = true; p.leftAt = legsEnd(p.rec); }
      if (!(p.leftAt > S.sim)) S.people.delete(id);
    }
  }

  function tickPeople(dt, k) {
    for (const [id, p] of S.people) {
      if (p.leaving && !(p.leftAt > S.sim)) { S.people.delete(id); continue; }      // out of the door
      const at = personExact(p);
      const key = p.rec.w ? `w${p.rec.w.t}` : (p.rec.wh && p.rec.wh.length ? `w${p.rec.wh[p.rec.wh.length - 1].t}` : `s${p.rec.x},${p.rec.y}`);
      if (p.fresh) { p.x = at.x; p.y = at.y; p.fx = p.fy = 0; p.fresh = false; p.key = key; }
      else if (key !== p.key) {
        p.ox = (p.bx ?? p.x) - at.x; p.oy = (p.by ?? p.y) - at.y; p.key = key;
        if (Math.hypot(p.ox, p.oy) > FAR_PERSON) { p.ox = p.oy = 0; p.alpha = 0; }   // unexplained jump: fade, never fly
      }
      p.ox *= k; p.oy *= k;
      p.bx = at.x + p.ox; p.by = at.y + p.oy;
      if (at.dx || at.dy) { p.dx = at.dx; p.dy = at.dy; }
      p.walking = at.walking;
      p.alpha = Math.min(1, p.alpha + dt * 4);
    }
    spreadPeople(k);
  }

  /* people who stand at the same place (a queue at the ESD gate, two at one machine) do not
     stand inside each other: they stand in a small ring around it */
  function spreadPeople(k) {
    const groups = new Map();
    for (const p of S.people.values()) {
      if (p.walking) continue;
      const key = `${Math.round(p.bx * 2)},${Math.round(p.by * 2)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(p);
    }
    const target = new Map();
    for (const list of groups.values()) {
      if (list.length < 2) continue;
      list.sort((a, b) => a.id - b.id);
      const n = list.length, r = n <= 2 ? 3.8 : n <= 5 ? 5.6 : 8.2;
      list.forEach((p, i) => {
        const a = (i / n) * Math.PI * 2 + 0.6;
        target.set(p, [Math.cos(a) * r, Math.sin(a) * r * 0.8]);
      });
    }
    for (const p of S.people.values()) {
      const [tx, ty] = target.get(p) || [0, 0];
      p.fx = (p.fx || 0) + (tx - (p.fx || 0)) * (1 - k);
      p.fy = (p.fy || 0) + (ty - (p.fy || 0)) * (1 - k);
      p.x = p.bx + p.fx; p.y = p.by + p.fy;
    }
  }

  // ------------------------------------------------------------------ robots (AMR)
  function updateRobots(list) {
    const seen = new Set();
    for (const rec of list) {
      let r = S.robots.get(rec.i);
      if (!r) { r = { id: rec.i, x: 0, y: 0, ox: 0, oy: 0, ang: null, key: "", fresh: true }; S.robots.set(rec.i, r); }
      r.rec = rec;
      seen.add(rec.i);
    }
    for (const id of [...S.robots.keys()]) if (!seen.has(id)) S.robots.delete(id);
  }

  /* where a robot is, and the way it points: it drives forward along the lanes and shifts sideways
     (omnidirectional wheels) on the short bits to and from a dock */
  function robotExact(r) {
    const rec = r.rec;
    const legs = legsOf(rec);
    legs.current = !!rec.w;
    const on = onLegs(legs, S.sim);
    if (on) {
      const turn = on.walking && on.len > 45;
      return { x: on.x, y: on.y, ang: turn ? Math.atan2(on.dy, on.dx) : null, moving: on.walking };
    }
    return { x: rec.x, y: rec.y, ang: rec.f !== undefined ? rec.f * Math.PI / 180 : null, moving: false };
  }

  function tickRobots(dt, k) {
    for (const r of S.robots.values()) {
      const at = robotExact(r);
      if (at.x === undefined) continue;
      const key = r.rec.w ? `w${r.rec.w.t}` : (r.rec.wh && r.rec.wh.length ? `w${r.rec.wh[r.rec.wh.length - 1].t}` : `s${r.rec.x},${r.rec.y}`);
      if (r.fresh) { r.x = at.x; r.y = at.y; r.ang = at.ang ?? 0; r.fresh = false; r.key = key; r.ox = r.oy = 0; }
      else if (key !== r.key) {
        r.ox = r.x - at.x; r.oy = r.y - at.y; r.key = key;
        if (Math.hypot(r.ox, r.oy) > FAR_PERSON) r.ox = r.oy = 0;
      }
      r.ox *= k; r.oy *= k;
      r.x = at.x + r.ox; r.y = at.y + r.oy;
      r.moving = at.moving;
      if (at.ang !== null) {                                     // turn towards the way, not in an instant
        let d = at.ang - r.ang;
        while (d > Math.PI) d -= 2 * Math.PI;
        while (d < -Math.PI) d += 2 * Math.PI;
        const step = Math.min(Math.abs(d), dt * 5);
        r.ang += Math.sign(d) * step;
      }
    }
  }

  /* a truck: the point under its rear, driving in and out or parked */
  function truckPose(t) {
    const bay = S.layout.bays.find((b) => b.bay === t.bay);
    if (!bay) return null;
    if (t.ph === "load") return { x: bay.x, y: bay.rear_y };
    const route = S.routes[`truck_${t.ph}:${t.bay}`];
    if (!route) return { x: bay.x, y: bay.rear_y };
    return ridePoint(route, (S.sim - t.t) / t.d);
  }

  /* the trucks at the time of the picture: what the newest look says once its phase has begun, the rest as
     the snapshot on screen has them */
  function trucksNow() {
    const shown = (S.floor && S.floor.trucks) || [];
    const fresh = (S.newest && S.newest.trucks) || [];
    const out = [];
    const ids = new Set();
    for (const t of fresh) {
      if (t.t <= S.sim) { out.push(t); ids.add(t.i); }
    }
    for (const t of shown) if (!ids.has(t.i)) out.push(t);
    return out;
  }

  // ------------------------------------------------------------------ station information
  function stationInfo(code) {
    const e = S.floor && S.floor.stations[code];
    if (!e) return null;
    let p = 0;
    if (e.w) {
      const [total, left] = e.w;
      const running = e.s === "RUNNING";
      const now = Math.max(0, left - (running ? (S.sim - S.floorT) : 0));
      p = total > 0 ? 1 - now / total : 1;
      p = Math.min(1, Math.max(0, p));
    }
    return { ...e, p, code };
  }

  /* partial results: a piece in a machine shows what that machine has done so far */
  function markPartials() {
    for (const s of S.sprites.values()) {
      s.partial = null;
      if (s.loc.t === "slot" && s.loc.slot === "w" && s.st) {
        const info = stationInfo(s.st);
        const st = S.layout.stations[s.st];
        if (info && st && (info.ph === "work")) s.partial = { tag: st.tag, type: st.type, p: info.p, state: info.s };
      }
    }
  }

  // ------------------------------------------------------------------ moves with a known time
  /* What the newest snapshot says happened to pieces at a known moment: the rides (running and just
     ended) and the places that tell since when a piece is there (robot, tote, rack, cage, hands). Until
     the picture gets to that moment the piece is where the snapshot on screen has it. */
  function indexTimed(f) {
    const timed = new Map();
    const add = (id, rec) => { if (!timed.has(id)) timed.set(id, []); timed.get(id).push(rec); };
    for (const r of f.tr || []) add(r.it.i, { t0: r.t, loc: { t: "ride", route: r.r, t0: r.t, d: r.d }, desc: r.it });
    for (const r of f.trh || []) add(r.i, { t0: r.t, loc: { t: "ride", route: r.r, t0: r.t, d: r.d } });
    for (const p of placements(f)) if (p.desc.at !== undefined && p.loc.t !== "ride") add(p.id, { t0: p.desc.at, loc: p.loc, desc: p.desc });
    S.timed = timed;
  }

  /* where a piece is at the time of the picture */
  function locNow(s) {
    const recs = S.timed.get(s.id);
    if (!recs) return s.loc;
    let best = null;
    for (const r of recs) if (r.t0 > S.floorT && r.t0 <= S.sim && (!best || r.t0 > best.t0)) best = r;
    return best ? best.loc : s.loc;
  }

  // ------------------------------------------------------------------ time and frames
  function simRunning() {
    return S.floor && S.age !== null && (performance.now() - S.snapWall) / 1000 < 12;
  }

  /* how far behind the plant the picture runs (simulation seconds) */
  function delay() {
    return S.rate > 0 ? DELAY_WALL * Math.max(S.speed || 0, 0.1) : 0;
  }

  /* a snapshot goes on screen: the stations, the queues, the pieces */
  function apply(f) {
    S.floor = f;
    S.floorT = f.t;
    if (S.layout) updateSprites(placements(f));
  }

  /* The clock of the picture. `snapWall` is the wall moment the plant was at `snapSim` (the age of the
     snapshot is taken off), so the target does not jump when the page and the plant poll out of step.
     The picture runs DELAY_WALL behind, at the plant's rate, and is nudged towards the target by at most
     15 % of that rate: nobody sprints to catch up. Only a big gap (a hidden tab, a jump in time) is closed
     at once. Every snapshot the clock passes goes on screen. */
  function tick(dt, nowMs) {
    if (!S.ready || !S.floor) return;
    const live = simRunning();
    if (live) {
      const ahead = Math.min(Math.max(0, (nowMs - S.snapWall) / 1000), 3);
      const target = S.snapSim + ahead * S.rate - delay();
      const err = target - S.sim;
      if (S.rate <= 0) {
        S.sim += err * Math.min(1, dt * 4);                      // paused: settle on the plant's time
      } else if (Math.abs(err) > Math.max(2, 2.5 * S.rate)) {
        S.sim = target;
      } else {
        const nudge = Math.max(-0.15, Math.min(0.15, (err / S.rate) * 0.6));
        S.sim += dt * S.rate * (1 + nudge);
      }
    }
    while (S.queue.length && (S.queue[0].t <= S.sim + 1e-6 || S.queue.length > 6)) apply(S.queue.shift());
    markPartials();
    const k = Math.exp(-dt / GLIDE_S);
    tickPeople(dt, k);
    tickRobots(dt, k);
    for (const s of S.sprites.values()) {
      const loc = locNow(s);
      const at = locate(loc);
      if (!at) continue;
      s.scale = at.s || 1;
      s.rot = at.a ?? null;
      const key = locKey(loc);
      if (s.fresh) {
        s.x = at.x; s.y = at.y; s.ox = s.oy = 0; s.key = key; s.fresh = false; s.keyLoc = loc;
      } else if (key !== s.key) {
        const path = beltPath(s.keyLoc, loc, s.x, s.y, at);
        if (path) {
          s.path = path; s.pathT = 0; s.pathDur = Math.min(0.9, Math.max(0.25, 0.18 + polyLength(path) / 260));
          s.ox = s.oy = 0;
        } else {
          s.path = null; s.ox = s.x - at.x; s.oy = s.y - at.y;
          if (Math.hypot(s.ox, s.oy) > FAR_PIECE) { s.ox = s.oy = 0; s.alpha = 0; }   // never through the walls
        }
        s.key = key; s.keyLoc = loc;
      }
      if (s.path) {                                               // along the conveyors, never across
        s.pathT += dt;
        const f = Math.min(1, s.pathT / s.pathDur);
        s.path[s.path.length - 1] = [at.x, at.y];
        const p = alongPoly(s.path, f * f * (3 - 2 * f));
        s.x = p[0]; s.y = p[1];
        if (f >= 1) s.path = null;
      } else {
        s.ox *= k; s.oy *= k;
        s.x = at.x + s.ox; s.y = at.y + s.oy;
      }
      s.alpha = Math.min(1, s.alpha + dt * 5);
    }
    for (let i = S.dying.length - 1; i >= 0; i--) {
      const s = S.dying[i];
      s.dyingFor += dt;
      s.alpha = Math.max(0, 1 - s.dyingFor * 4);
      if (s.alpha <= 0) S.dying.splice(i, 1);
    }
  }

  let last = performance.now();
  function loop(now) {
    const dt = Math.min(0.25, Math.max(0, (now - last) / 1000));
    last = now;
    if (!document.hidden) {
      tick(dt, now);
      for (const v of views) v.draw(now);
    }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  // ------------------------------------------------------------------ loading
  function reset() {
    S.sprites.clear();
    S.people.clear();
    S.robots.clear();
    S.dying.length = 0;
    S.floor = null;
    S.newest = null;
    S.queue.length = 0;
    S.timed = new Map();
  }

  function ingest(msg, wallNow) {
    S.age = msg.age;
    const f = msg.floor;
    if (!f) { S.floor = null; S.newest = null; S.queue.length = 0; return; }
    if (f.run !== S.run) { reset(); S.run = f.run; newRun(f.run); }
    if (relayout) return;                                      // the plan of the new run is on its way
    const at = wallNow - Math.max(0, msg.age || 0) * 1000;     // when the plant was at f.t
    const paused = !!(f.ctl && f.ctl.paused);
    const prev = S.newest;
    if (paused) {
      S.rate = 0;
    } else if (prev && f.t > prev.t && S.rate > 0) {
      const dw = (at - S.snapWall) / 1000;
      if (dw > 0.4) {
        const measured = (f.t - prev.t) / dw;
        const nominal = f.speed || measured;
        // the plant keeps its nominal speed unless it cannot (a slow MES, a jump in time): trust the
        // measurement only when it is clearly off, and then smoothly
        const off = Math.abs(measured - nominal) / nominal > 0.25;
        S.rate = off ? S.rate * 0.7 + Math.min(measured, nominal * 1.5) * 0.3 : S.rate * 0.8 + nominal * 0.2;
      }
    } else {
      S.rate = f.speed || 1;
    }
    S.speed = f.speed || S.rate || 1;
    if (prev && f.t <= prev.t) { S.age = msg.age; return; }    // the same look again
    S.newest = f;
    S.snapSim = f.t;
    S.snapWall = at;
    if (!S.floor) {                                            // the first look: on screen at once
      S.sim = f.t - delay();
      apply(f);
    } else {
      S.queue.push(f);
    }
    if (S.layout) {
      updatePeople(f.people || []);
      updateRobots(f.amr || []);
      indexTimed(f);
      S.ready = true;
    }
    for (const fn of listeners) fn(f);
  }

  /* put everything the plant has told on screen now (tests, and a page that was hidden) */
  function settle() {
    if (!S.newest) return;
    S.sim = Math.max(S.sim, S.newest.t);
    while (S.queue.length) apply(S.queue.shift());
  }

  async function poll() {
    if (!document.hidden) {
      try {
        const r = await fetch("/api/v1/floor", { cache: "no-store" });
        if (r.ok) ingest(await r.json(), performance.now());
      } catch (e) {
        console.warn("floor poll failed", e);
      }
    }
    setTimeout(poll, POLL_MS);
  }

  /* the floor plan of the live plant (a scenario of the tuning game may have fewer lines: every run of the
     plant has its own plan) */
  const layoutListeners = new Set();
  function useLayout(layout) {
    for (const route of Object.values(layout.routes)) prepareRoute(route);
    S.layout = layout;
    S.routes = layout.routes;
    S.stationList = Object.keys(layout.stations);
    S.lineOfStation = {};
    for (const [code, line] of Object.entries(layout.lines)) {
      line.cells.forEach((c) => c.stations.forEach((st) => { S.lineOfStation[st] = code; }));
    }
    for (const fn of layoutListeners) fn(layout);
  }
  async function fetchLayout(run) {
    const r = await fetch(`/api/v1/floor/layout?v=${window.MES_VERSION || ""}${run ? `&run=${encodeURIComponent(run)}` : ""}`, { cache: "no-store" });
    return r.json();
  }
  let relayout = null;
  function newRun(run) {
    if (!S.layout || !S.layout.run || S.layout.run === run || relayout) return;
    relayout = fetchLayout(run).then((layout) => {
      reset();
      S.run = null;                                            // the next look fills the new plan
      useLayout(layout);
    }).catch((e) => console.warn("floor plan", e)).finally(() => { relayout = null; });
  }

  let started = null;
  function start() {
    if (started) return started;
    started = (async () => {
      useLayout(await fetchLayout(null));
      poll();
      return S.layout;
    })();
    return started;
  }

  return {
    S, start, views, listeners, layoutListeners, stationInfo, locate, locNow, ridePoint, alongRoute, ingest, tick, settle, truckPose, trucksNow,
    legsOf, onLegs, beltPath,
    get people() { return S.people; },
    get robots() { return S.robots; },
    get layout() { return S.layout; },
    get floor() { return S.floor; },
    get sim() { return S.sim; },
    simRunning,
  };
})();
