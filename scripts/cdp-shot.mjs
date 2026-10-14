// Drives a headless Chrome over the DevTools protocol (no dependencies, Node 22+): load a page,
// wait, run JavaScript, click in the plan, take screenshots, all without a visible browser window.
//
//   node scripts/cdp-shot.mjs steps.json
//
// steps.json = {"url": "...", "width": 1500, "height": 900, "steps": [ ... ]}, steps:
//   {"wait": ms}                     {"eval": "js", "save": "name"}     {"evalFile": "file.js", "pre": "js"}
//   {"shot": "file.png|.jpg", "clip": [x, y, w, h]}   {"scheme": "light|dark"}   {"hover": [x, y]} {"click": [x, y]}
//   {"wheel": [x, y, deltaY]}        {"drag": [x1, y1, x2, y2]}
//   {"clickWorld": [0, 0], "code": "SMT-01-PP1", "dbl": false}   click on a station of the floor plan
//   {"clickEval": "js"} {"hoverEval": "js"}   click / hover at the client point [x, y] the expression returns
//   {"hoverWorld": [wx, wy]}         (world coordinates, or "code" of a station; "view" = global name of the FloorView)
// Minimal headless-Chrome driver over CDP (no dependencies; Node 22+ has global WebSocket).
// usage: node cdp.mjs <script.json>   where script = {url,width,height,steps:[{wait:ms}|{eval:"js", save:"name"}|{shot:"file.png"}|{hover:[x,y]}|{click:[x,y]}]}
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const script = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const port = 9300 + Math.floor(Math.random() * 500);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "cdp-"));
const chrome = spawn("C:/Program Files/Google/Chrome/Application/chrome.exe", [
  "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--proxy-server=direct://", "--proxy-bypass-list=*",
  `--window-size=${script.width || 1400},${script.height || 900}`, "about:blank"], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function connect() {
  for (let i = 0; i < 80; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch (e) { /* not up yet */ }
    await sleep(250);
  }
  throw new Error("chrome did not start");
}
const ws = new WebSocket(await connect());
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map(); const logs = [];
ws.onmessage = (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
  else if (d.method === "Runtime.consoleAPICalled") logs.push(`[console.${d.params.type}] ` + d.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
  else if (d.method === "Runtime.exceptionThrown") logs.push("[exception] " + (d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text));
};
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
await send("Page.enable"); await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: script.width || 1400, height: script.height || 900, deviceScaleFactor: script.dpr || 1, mobile: false });
await send("Page.navigate", { url: script.url });
const out = {};
for (const step of script.steps) {
  if (step.wait) await sleep(step.wait);
  if (step.evalFile) {
    const src = (step.pre || "") + fs.readFileSync(step.evalFile, "utf8");
    const r = await send("Runtime.evaluate", { expression: src, awaitPromise: true, returnByValue: true });
    out[step.save || "evalFile" + Object.keys(out).length] = r.result?.result?.value ?? r.result?.exceptionDetails?.exception?.description ?? r.result?.exceptionDetails?.text;
  }
  if (step.scheme) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: step.scheme }] });
  if (step.eval) {
    const r = await send("Runtime.evaluate", { expression: step.eval, awaitPromise: true, returnByValue: true });
    const v = r.result?.result?.value ?? r.result?.exceptionDetails?.exception?.description ?? r.result?.result?.description;
    out[step.save || "eval" + Object.keys(out).length] = v;
  }
  for (const key of ["clickWorld", "hoverWorld"]) {
    if (!step[key]) continue;
    const [wx, wy] = Array.isArray(step[key]) ? step[key] : [0, 0];
    const code = step.code ? `Floor.layout.stations[${JSON.stringify(step.code)}]` : null;
    const expr = `(()=>{const v=${step.view || "floorView"}; ${code ? `const st=${code}; const wx=st.cx, wy=st.cy;` : `const wx=${wx}, wy=${wy};`} v.canvas.scrollIntoView({block:"center"}); const r=v.canvas.getBoundingClientRect(); const k=v.cam.k; return JSON.stringify({x:Math.round(r.left+(v.size.w/2+(wx-v.cam.x)*k)), y:Math.round(r.top+(v.size.h/2+(wy-v.cam.y)*k))})})()`;
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    const pt = JSON.parse(r.result.result.value);
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pt.x, y: pt.y });
    if (key === "clickWorld") {
      for (const n of step.dbl ? [1, 2] : [1]) {
        await send("Input.dispatchMouseEvent", { type: "mousePressed", x: pt.x, y: pt.y, button: "left", clickCount: n });
        await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pt.x, y: pt.y, button: "left", clickCount: n });
      }
    }
    out[key + ":" + (step.code || wx + "," + wy)] = JSON.stringify(pt);
  }
  for (const key of ["clickEval", "hoverEval"]) {
    if (!step[key]) continue;                     // the point is computed in the page: "(() => [clientX, clientY])()"
    const r = await send("Runtime.evaluate", { expression: `JSON.stringify(${step[key]})`, awaitPromise: true, returnByValue: true });
    const pt = JSON.parse(r.result.result.value);
    if (!pt) { out[key] = "no point"; continue; }
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pt[0], y: pt[1] });
    if (key === "clickEval") {
      await send("Input.dispatchMouseEvent", { type: "mousePressed", x: pt[0], y: pt[1], button: "left", clickCount: 1 });
      await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pt[0], y: pt[1], button: "left", clickCount: 1 });
    }
    out[key + ":" + (step.save || "")] = JSON.stringify(pt);
  }
  if (step.wheel) await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: step.wheel[0], y: step.wheel[1], deltaX: 0, deltaY: step.wheel[2] });
  if (step.drag) {
    const [x1, y1, x2, y2] = step.drag;
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x1, y: y1 });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: x1, y: y1, button: "left", buttons: 1, clickCount: 1 });
    for (let i = 1; i <= 8; i++) await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x1 + (x2 - x1) * i / 8, y: y1 + (y2 - y1) * i / 8, button: "left", buttons: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x2, y: y2, button: "left", buttons: 0, clickCount: 1 });
  }
  if (step.hover) await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: step.hover[0], y: step.hover[1] });
  if (step.click) {
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: step.click[0], y: step.click[1] });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: step.click[0], y: step.click[1], button: "left", clickCount: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: step.click[0], y: step.click[1], button: "left", clickCount: 1 });
  }
  if (step.shot) {
    const jpeg = step.shot.endsWith(".jpg");
    const clip = step.clip ? { x: step.clip[0], y: step.clip[1], width: step.clip[2], height: step.clip[3], scale: 1 } : undefined;
    const r = await send("Page.captureScreenshot", { format: jpeg ? "jpeg" : "png", quality: 84, captureBeyondViewport: !!step.full, clip });
    fs.writeFileSync(step.shot, Buffer.from(r.result.data, "base64"));
    out["shot:" + step.shot] = "saved";
  }
}
console.log(JSON.stringify(out, null, 1));
if (logs.length) console.log("LOGS:\n" + logs.slice(-30).join("\n"));
ws.close(); chrome.kill();
try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
process.exit(0);
