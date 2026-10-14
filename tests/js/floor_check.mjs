// Runs the browser code of the live plant view against snapshots made by the simulation,
// with a fake canvas that only records that nothing throws. Used by tests/test_floor_js.py.
//   node floor_check.mjs <input.json>        input = {layout, messages: [{age, floor}, ...]}
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.resolve(here, "../../src/mes_lab/mes/web/static");
const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const problems = [];
const problem = (m) => { if (problems.length < 40) problems.push(m); };

// ---- a canvas context that accepts everything
let calls = 0;
const ctx = new Proxy({}, {
  get(target, prop) {
    if (prop === "measureText") return () => ({ width: 12 });
    if (prop in target) return target[prop];
    return (...args) => { calls++; return undefined; };
  },
  set(target, prop, value) { target[prop] = value; return true; },
});
const listeners = {};
const canvas = {
  width: 1200, height: 600, style: {},
  getContext: () => ctx,
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 600 }),
  addEventListener: (type, fn) => { listeners[type] = fn; },
  setPointerCapture() {},
};
const sandbox = {
  window: { devicePixelRatio: 1, matchMedia: () => ({ matches: true, addEventListener() {} }), MES_VERSION: "test", addEventListener() {} },
  document: { hidden: false, documentElement: {}, addEventListener() {}, createElement: () => ({}) },
  getComputedStyle: () => ({ getPropertyValue: () => "#8899aa" }),
  matchMedia: () => ({ matches: true, addEventListener() {} }),
  MES: { lang: () => "en" },
  performance: { now: () => Date.now() },
  requestAnimationFrame() {},
  setTimeout() {}, clearTimeout() {}, console,
  fetch: async (url) => ({ ok: true, json: async () => (String(url).includes("layout") ? input.layout : { age: null, floor: null }) }),
};
sandbox.window.document = sandbox.document;
const context = vm.createContext(sandbox);
for (const file of ["floor-core.js", "floor-art.js", "floor-machines.js", "floor-people.js", "floor-robots.js", "floor-boards.js", "floor-view.js"]) {
  vm.runInContext(fs.readFileSync(path.join(staticDir, file), "utf8"), context, { filename: file });
}
const get = (name) => vm.runInContext(name, context);
const Floor = get("Floor"), FloorView = get("FloorView"), FloorArt = get("FloorArt");
await Floor.start();
const L = Floor.layout;
const W = L.world;

const finite = (v) => Number.isFinite(v);
const insideStation = (code, x, y, slack = 0.5) => {
  const st = L.stations[code];
  return x >= st.x - slack && x <= st.x + st.w + slack && y >= st.y - slack && y <= st.y + st.h + slack;
};
function distanceToRoute(route, x, y) {
  let best = Infinity;
  for (let i = 1; i < route.pts.length; i++) {
    const [x1, y1] = route.pts[i - 1], [x2, y2] = route.pts[i];
    const dx = x2 - x1, dy = y2 - y1, len2 = dx * dx + dy * dy || 1;
    const t = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / len2));
    best = Math.min(best, Math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)));
  }
  return best;
}

const overview = new FloorView(canvas, { interactive: false });
const lineView = new FloorView(canvas, { interactive: false, line: "FA-01" });
const closeup = new FloorView(canvas, { interactive: false, closeup: true, station: "SMT-01-PP1" });
let frames = 0, sprites = 0;

let wall = 1000;
for (const [n, msg] of input.messages.entries()) {
  wall += 1000;
  Floor.ingest(msg, wall);
  const f = msg.floor;
  Floor.settle();                                          // the picture runs a moment late: show this look now
  Floor.tick(0.016, wall);
  for (let i = 0; i < 40; i++) Floor.tick(0.1, wall);      // let every piece glide to its place
  for (const s of Floor.S.sprites.values()) {
    sprites++;
    const at = Floor.locate(s.loc);
    if (!at || !finite(at.x) || !finite(at.y)) { problem(`snapshot ${n}: piece ${s.id} has no place (${s.loc.t})`); continue; }
    if (Math.hypot(s.x - at.x, s.y - at.y) > 0.5) problem(`snapshot ${n}: piece ${s.id} did not arrive (${s.x.toFixed(1)},${s.y.toFixed(1)} vs ${at.x.toFixed(1)},${at.y.toFixed(1)})`);
    if (at.x < -5 || at.x > W.w + 5 || at.y < -5 || at.y > W.h + 5) problem(`snapshot ${n}: piece ${s.id} outside the plant (${at.x}, ${at.y}) in ${s.loc.t}`);
    if (s.loc.t === "slot" && !insideStation(s.loc.code, at.x, at.y)) problem(`snapshot ${n}: piece ${s.id} not inside ${s.loc.code}`);
    if (s.loc.t === "ride") {
      const route = L.routes[s.loc.route];
      if (!route) { problem(`no route ${s.loc.route}`); continue; }
      const d = distanceToRoute(route, at.x, at.y);
      if (d > 0.6) problem(`snapshot ${n}: piece ${s.id} is ${d.toFixed(1)} off its conveyor ${s.loc.route}`);
    }
  }
  // the number of pieces the page follows equals what the plant reports
  const reported = new Set();
  for (const e of Object.values(f.stations)) {
    if (e.it) reported.add(e.it.i);
    (e.out || []).forEach((d) => reported.add(d.i));
    (e.ov || []).forEach(([d]) => reported.add(d.i));
  }
  Object.values(f.bufs).forEach((cells) => cells.forEach((c) => c.it.forEach((d) => reported.add(d.i))));
  Object.values(f.feed).forEach((q) => q.it.forEach((d) => reported.add(d.i)));
  f.tr.forEach((r) => reported.add(r.it.i));
  Object.values(f.boxes || {}).forEach((box) => box.forEach((d) => reported.add(d.i)));
  (f.amr || []).forEach((r) => (r.ld || []).forEach((d) => reported.add(d.i)));
  (f.rack || []).forEach((d) => reported.add(d.i));
  (f.cage || []).forEach((d) => reported.add(d.i));
  (f.people || []).forEach((p) => { if (p.h) reported.add(p.h.i); });
  if (reported.size !== Floor.S.sprites.size) problem(`snapshot ${n}: plant reports ${reported.size} pieces, page follows ${Floor.S.sprites.size}`);

  // the people: one figure for every person the plant reports, on a place inside the plant
  if (Floor.S.people.size !== f.people.length) problem(`snapshot ${n}: plant reports ${f.people.length} people, page follows ${Floor.S.people.size}`);
  for (const p of Floor.S.people.values()) {
    if (!finite(p.x) || !finite(p.y) || p.x < -5 || p.x > W.w + 5 || p.y < -5 || p.y > W.h + 5) problem(`snapshot ${n}: person ${p.id} stands outside the plant (${p.x}, ${p.y})`);
    if (p.rec.w && Math.abs(p.ox) + Math.abs(p.oy) > 0.5) problem(`snapshot ${n}: person ${p.id} did not arrive`);
  }
  // a walking person is on one of the walks the plant reported, never on a short cut between two
  for (const p of Floor.S.people.values()) {
    const legs = Floor.legsOf(p.rec);
    if (!legs.length) continue;
    let best = p.rec.x !== undefined ? Math.hypot(p.bx - p.rec.x, p.by - p.rec.y) : Infinity;
    for (const w of legs) best = Math.min(best, distanceToRoute({ pts: w.p }, p.bx, p.by));
    if (best > 0.8) problem(`snapshot ${n}: person ${p.id} is ${best.toFixed(1)} off the way they walk`);
  }
  // the robots: one for every robot the plant reports, on the floor of the plant, and what they carry with them
  if (Floor.S.robots.size !== (f.amr || []).length) problem(`snapshot ${n}: plant reports ${(f.amr || []).length} robots, page follows ${Floor.S.robots.size}`);
  for (const r of Floor.S.robots.values()) {
    if (!finite(r.x) || !finite(r.y) || r.x < 0 || r.x > W.w || r.y < 0 || r.y > W.h) problem(`snapshot ${n}: robot ${r.id} is outside the plant (${r.x}, ${r.y})`);
    for (const code of Object.keys(L.stations)) if (insideStation(code, r.x, r.y, -1)) problem(`snapshot ${n}: robot ${r.id} stands inside ${code}`);
  }
  for (const s of Floor.S.sprites.values()) {
    if (s.loc.t === "amr") {
      const r = Floor.S.robots.get(s.loc.r);
      if (!r || Math.hypot(s.x - r.x, s.y - r.y) > 12) problem(`snapshot ${n}: piece ${s.id} is not on its robot`);
    }
    if (s.loc.t === "hand") {
      const p = Floor.S.people.get(s.loc.p);
      if (!p || Math.hypot(s.x - p.x, s.y - p.y) > 9) problem(`snapshot ${n}: piece ${s.id} is not in the hands of its person`);
    }
  }
  // nobody stands inside somebody else (a queue at the ESD gate fans out)
  const standing = [...Floor.S.people.values()].filter((p) => !p.walking);
  for (let i = 0; i < standing.length; i++) {
    for (let j = i + 1; j < standing.length; j++) {
      if (Math.hypot(standing[i].x - standing[j].x, standing[i].y - standing[j].y) < 1.2) problem(`snapshot ${n}: persons ${standing[i].id} and ${standing[j].id} stand on one spot`);
    }
  }
  // the trucks: on the road of their bay, with a pose that is a real place
  for (const t of f.trucks) {
    const pose = Floor.truckPose(t);
    if (!pose || !finite(pose.x) || !finite(pose.y)) { problem(`snapshot ${n}: truck ${t.i} has no pose`); continue; }
    if (pose.x < 0 || pose.x > W.w || pose.y < 0 || pose.y > W.h + 200) problem(`snapshot ${n}: truck ${t.i} is outside the plant (${pose.x}, ${pose.y})`);
  }
  // the warehouse shows the pallets it holds
  const fgw = L.buildings.find((b) => b.id === "FGW");
  const cells = FloorArt.fgwCells(fgw, f);
  const held = Object.values(f.fgw).reduce((a, l) => a + l.length, 0);
  const shown = cells.bands.reduce((a, b) => a + Math.min(b.count, b.cap), 0);
  if (cells.cells.length !== shown) problem(`snapshot ${n}: warehouse draws ${cells.cells.length} pallets, expected ${shown}`);
  if (shown > held) problem(`snapshot ${n}: warehouse shows more pallets than it holds`);

  // machine progress never leaves 0..1
  for (const code of Object.keys(f.stations)) {
    const info = Floor.stationInfo(code);
    if (!(info.p >= 0 && info.p <= 1)) problem(`${code}: progress ${info.p}`);
  }
  // draw it all: whole plant, one line, a close-up, light and dark, near and far
  for (const dark of [true, false]) {
    sandbox.window.matchMedia = () => ({ matches: dark, addEventListener() {} });
    for (const [view, zoom] of [[overview, 0.4], [overview, 1.5], [overview, 6], [lineView, 1.8], [closeup, 6]]) {
      view.fitted = true; view.userMoved = true;
      const b = view.bounds();
      view.cam = { x: b.x + b.w / 2, y: b.y + b.h / 2, k: zoom };
      try { view.draw(wall); frames++; Floor.tick(0.016, wall + 16); }
      catch (e) { problem(`draw failed (snapshot ${n}, zoom ${zoom}, ${dark ? "dark" : "light"}): ${e.stack.split("\n").slice(0, 3).join(" | ")}`); }
    }
  }
}
// ---- playback at 60x: the snapshots of one minute after another (a whole walk begins and ends between two
// of them) come once a wall second; the picture runs a moment late and draws everybody on the way they
// really walk, frame by frame, never on a short cut
const minutes = input.messages.filter((m) => m.minute_series);
let onWay = 0, offWay = 0;
const worst = [];
if (minutes.length) {
  wall += 10000;
  minutes.forEach((m, i) => {
    const f = { ...m.floor, run: "playback", speed: 60 };
    Floor.ingest({ age: 0, floor: f }, wall);
    for (let j = 0; j < 20; j++) {
      wall += 50;
      Floor.tick(0.05, wall);
      if (i < 2) continue;                                   // the clock settles first
      for (const p of Floor.S.people.values()) {
        if (!p.walking || p.alpha < 0.99) continue;
        let best = Infinity;
        for (const w of Floor.legsOf(p.rec)) best = Math.min(best, distanceToRoute({ pts: w.p }, p.bx, p.by));
        if (best > 1.5) { offWay++; if (worst.length < 5) worst.push(`${p.id}: ${best.toFixed(1)}`); } else onWay++;
      }
    }
  });
  if (offWay) problem(`playback: ${offWay} of ${onWay + offWay} drawn walkers off their way (${worst.join(", ")})`);
  if (onWay < 50) problem(`playback: only ${onWay} walkers seen`);
}
console.log(JSON.stringify({ ok: problems.length === 0, problems, frames, sprites, drawCalls: calls, playback: { onWay, offWay } }));
