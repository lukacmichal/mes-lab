/* Shared helpers: plant clock, polling, formatting. */
const MES = (() => {
  const clock = { ms: null, fetchedAt: 0, speed: 1, connected: false, offsetMin: 0, shift: null };

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STATE_LABEL = {
    RUNNING: "Running", STARVED: "Starved", BLOCKED: "Blocked", DOWN: "Down",
    MINOR_STOP: "Minor stop", CHANGEOVER: "Changeover", MAINTENANCE: "Maintenance",
    NO_ORDER: "No order", NO_MATERIAL: "No material", IDLE: "Idle", UNKNOWN: "Unknown", OFF: "Out of operation",
  };
  const STATES = ["RUNNING", "MINOR_STOP", "STARVED", "BLOCKED", "DOWN", "CHANGEOVER", "MAINTENANCE", "NO_ORDER"];
  const label = (s) => STATE_LABEL[s] || s;

  function offsetOf(iso) {
    const m = /([+-])(\d\d):(\d\d)$/.exec(iso || "");
    return m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 0;
  }

  /* the live plant runs a scenario of the tuning game: say so on every page (what differs from the plant
     as configured), with the way back */
  const KNOB_SHORT = {
    smt_lines: ["SMT lines", "linky SMT"], robots: ["robots", "roboty"], maintenance_day: ["maintenance (day)", "údržba (deň)"],
    maintenance_night: ["maintenance (night)", "údržba (noc)"], relief: ["relief operators", "záskoky"],
    kit_bins: ["bins at the loader", "zásobníky pri nakladači"], fa_buffer: ["places between cells", "miesta medzi bunkami"],
  };
  let scenarioKey = null;
  function scenarioBanner(knobs) {
    const el = document.getElementById("scenario-banner");
    if (!el) return;
    const key = JSON.stringify(knobs || null) + lang();
    if (key === scenarioKey) return;
    scenarioKey = key;
    if (!knobs) { el.classList.remove("show"); el.innerHTML = ""; return; }
    const T2 = (en, sk) => (lang() === "sk" ? sk : en);
    const parts = Object.entries(knobs).map(([k, v]) => `${esc((KNOB_SHORT[k] || [k, k])[lang() === "sk" ? 1 : 0])} ${esc(v)}`).join(" · ");
    el.innerHTML = `<b>${T2("The live plant runs a tuning scenario:", "Živý závod beží podľa scenára z ladenia:")}</b> ${parts}
      · <a href="/tuning">${T2("Tuning page (back to the plant as it is)", "stránka Tuning (späť na závod, aký je)")}</a>`;
    el.classList.add("show");
  }

  function setClock(c) {
    if (!c || !c.plant_time) return;
    scenarioBanner(c.scenario);
    clock.ms = Date.parse(c.plant_time);
    clock.offsetMin = offsetOf(c.plant_time);
    clock.fetchedAt = Date.now();
    clock.control = c.control || {};
    // paused: the clock stands; running ahead: it jumps with every answer instead of guessing
    clock.speed = clock.control.paused || clock.control.goto ? 0 : (c.speed || 1);
    simControls(c);
    clock.connected = !!c.connected;
    clock.shift = c.shift;
    const sel = document.getElementById("speed-select");
    if (sel && document.activeElement !== sel && c.desired_speed) sel.value = String(c.desired_speed);
    const badge = document.getElementById("shift-badge");
    if (badge && c.shift) badge.textContent = `Shift ${c.shift.code} · ${c.shift.name}`;
    const dot = document.getElementById("live-dot");
    if (dot) {
      dot.className = "dot " + (c.connected ? "live" : "stale");
      dot.title = c.connected ? `Simulation live at ${c.speed}×` : "Simulation not reporting";
    }
    const banner = document.getElementById("offline-banner");
    if (banner) banner.classList.toggle("show", !c.connected);
    const errors = (c.mes_errors && c.mes_errors.count) || 0;
    const errBanner = document.getElementById("mes-error-banner");
    if (errBanner) {
      errBanner.classList.toggle("show", errors > 0);
      errBanner.querySelectorAll(".mes-err-n").forEach((el) => { el.textContent = String(errors); });
    }
  }

  function nowMs() {
    if (clock.ms === null) return null;
    return clock.connected ? clock.ms + (Date.now() - clock.fetchedAt) * clock.speed : clock.ms;
  }

  // plant-local wall time parts, independent of the browser's time zone
  function localParts(ms) {
    const d = new Date(ms + clock.offsetMin * 60000);
    const p = (n) => String(n).padStart(2, "0");
    return {
      time: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`,
      hm: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`,
      date: `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`,
    };
  }
  const hm = (iso) => (iso ? localParts(Date.parse(iso)).hm : "–");
  // <input type="datetime-local"> values in plant time
  const localInput = (ms) => { const p = localParts(ms); return `${p.date}T${p.hm}`; };
  const fromLocalInput = (value) => {
    if (!value) return null;
    const sign = clock.offsetMin < 0 ? "-" : "+";
    const abs = Math.abs(clock.offsetMin);
    const pad = (x) => String(x).padStart(2, "0");
    return `${value.length === 16 ? value + ":00" : value}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
  };
  const dt = (iso) => {
    if (!iso) return "–";
    const p = localParts(Date.parse(iso));
    return `${p.date.slice(5)} ${p.hm}`;
  };

  function tick() {
    const ms = nowMs();
    if (ms === null) return;
    const parts = localParts(ms);
    const t = document.getElementById("plant-clock");
    const d = document.getElementById("plant-date");
    if (t) t.textContent = parts.time;
    if (d) d.textContent = `${parts.date} · plant time`;
  }
  setInterval(tick, 200);

  function duration(sec) {
    if (sec === null || sec === undefined || isNaN(sec)) return "–";
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
    const h = Math.floor(m / 60);
    if (h < 48) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
    return `${Math.floor(h / 24)}d ${h % 24}h`;
  }
  const since = (iso) => {
    const now = nowMs();
    return iso && now ? (now - Date.parse(iso)) / 1000 : null;
  };
  const n = (v) => (v ?? 0).toLocaleString("en-US");
  const pill = (state, text) => `<span class="pill st-${esc(state)}">${esc(text || label(state))}</span>`;

  async function poll(url, render, everyMs = 2000) {
    const run = async () => {
      try {
        const r = await fetch(url, { cache: "no-store" });
        if (r.ok) {
          const data = await r.json();
          setClock(data.clock);
          render(data);
          tick();
        }
      } catch (e) {
        console.warn("poll failed", e);
      }
      setTimeout(run, everyMs);
    };
    run();
  }

  // ---- pause and "go to a time" (signed in): the whole plant stops, or runs ahead as fast as it can
  const T = (en, sk) => (lang() === "sk" ? sk : en);
  const pauseBtn = document.getElementById("sim-pause");
  const gotoBtn = document.getElementById("sim-goto");
  const pop = document.getElementById("goto-pop");
  const simBanner = document.getElementById("sim-banner");

  function simControls(c) {
    const ctl = c.control || {};
    if (pauseBtn) {
      pauseBtn.classList.toggle("on", !!ctl.paused);
      if (window.MES_CAN_ACT) pauseBtn.title = ctl.paused ? T("Run again", "Spustiť znova") : T("Pause: the plant stands still (machines, people, robots)", "Pauza: závod zastane (stroje, ľudia, roboty)");
    }
    if (gotoBtn) gotoBtn.classList.toggle("on", !!ctl.goto);
    if (!simBanner) return;
    if (ctl.goto) {
      const from = Date.parse(ctl.goto.from), to = Date.parse(ctl.goto.until), now = Date.parse(c.plant_time);
      const pct = Math.max(0, Math.min(100, Math.round(100 * (now - from) / Math.max(1, to - from))));
      const at = localParts(to);
      const stop = window.MES_CAN_ACT;
      simBanner.innerHTML = `<div class="bi"><p lang="en"><b>The plant runs ahead to ${at.date} ${at.hm}</b> (${pct} %), as fast as the MES keeps up; ${ctl.goto.then === "pause" ? "there it stops, so you can look around." : "then it goes on at its speed."}
          ${stop ? '<button type="button" class="btn small" data-goto-cancel>Stop here</button>' : ""}</p>
        <p lang="sk"><b>Závod beží dopredu na ${at.date} ${at.hm}</b> (${pct} %), tak rýchlo, ako MES stíha; ${ctl.goto.then === "pause" ? "tam zastane, aby si sa mohol poobzerať." : "potom pokračuje svojou rýchlosťou."}
          ${stop ? '<button type="button" class="btn small" data-goto-cancel>Zastaviť tu</button>' : ""}</p></div>
        <div class="bar"><div style="width:${pct}%"></div></div>`;
      simBanner.classList.add("show");
    } else if (ctl.paused) {
      const left = Math.ceil((ctl.pause_left_s || 0) / 60);
      const can = window.MES_CAN_ACT;
      simBanner.innerHTML = `<div class="bi"><p lang="en"><b>The plant is paused.</b> Machines, people, robots and pieces stand where they are; click anything to look at it. It runs again by itself in ${left} min.
          ${can ? '<button type="button" class="btn small primary" data-resume>Run again</button>' : ""}</p>
        <p lang="sk"><b>Závod je pozastavený.</b> Stroje, ľudia, roboty aj kusy stoja, kde sú; na čokoľvek klikni a pozri sa. Sám sa znova spustí o ${left} min.
          ${can ? '<button type="button" class="btn small primary" data-resume>Spustiť</button>' : ""}</p></div>`;
      simBanner.classList.add("show");
    } else {
      simBanner.classList.remove("show");
    }
  }

  async function refreshClock() {
    try {
      const r = await fetch("/api/v1/clock", { cache: "no-store" });
      if (r.ok) { setClock((await r.json()).clock); tick(); }
    } catch (e) { /* the next poll */ }
  }

  async function setPaused(paused) {
    try { await post("/api/v1/sim/pause", { paused }); } catch (e) { alert(e.message); }
    refreshClock();
  }

  if (pauseBtn) pauseBtn.addEventListener("click", () => setPaused(!(clock.control && clock.control.paused)));
  document.addEventListener("click", async (e) => {
    if (e.target.closest("[data-resume]")) setPaused(false);
    if (e.target.closest("[data-goto-cancel]")) {
      try { await fetch("/api/v1/sim/goto", { method: "DELETE" }); } catch (err) { /* later */ }
      refreshClock();
    }
    if (pop && !pop.hidden && !pop.contains(e.target) && !e.target.closest("#sim-goto")) pop.hidden = true;
  });

  /* the next time of day hh:mm after the plant's clock, as a plant-local input value */
  function nextAt(hh, mm) {
    const now = nowMs();
    if (now === null) return "";
    const p = localParts(now);
    let ms = Date.parse(fromLocalInput(`${p.date}T${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`));
    if (ms <= now + 60000) ms += 24 * 3600 * 1000;
    return localInput(ms);
  }

  function renderGoto(error) {
    const now = nowMs();
    const plus = (h) => (now === null ? "" : localInput(now + h * 3600 * 1000));
    const shifts = [["06:00", T("morning shift", "ranná zmena")], ["14:00", T("afternoon shift", "poobedná zmena")], ["22:00", T("night shift", "nočná zmena")]];
    pop.innerHTML = `<b>${T("Go to a time", "Posunúť čas")}</b>
      <p class="muted" style="margin:0">${T("The plant cannot go back in time; it runs ahead as fast as the MES keeps up (an hour of plant time takes roughly half a minute).", "Závod nevie ísť späť v čase; pobeží dopredu tak rýchlo, ako MES stíha (hodina závodu trvá zhruba pol minúty).")}</p>
      <label class="field">${T("Plant time", "Čas závodu")}<input type="datetime-local" id="goto-at" value="${plus(1)}" step="60"></label>
      <div class="quick">
        <button type="button" class="btn small" data-goto-set="${plus(1)}">+1 h</button>
        <button type="button" class="btn small" data-goto-set="${plus(4)}">+4 h</button>
        ${shifts.map(([t, name]) => `<button type="button" class="btn small" data-goto-set="${nextAt(+t.slice(0, 2), 0)}" title="${esc(name)}">${t}</button>`).join("")}
      </div>
      <label class="check"><input type="checkbox" id="goto-pause" checked> ${T("stop there (pause)", "zastaviť tam (pauza)")}</label>
      ${error ? `<div class="err">${esc(error)}</div>` : ""}
      <div style="display:flex;gap:8px"><button type="button" class="btn small primary" id="goto-go">${T("Go", "Ísť")}</button>
        <button type="button" class="btn small" id="goto-close">${T("Close", "Zavrieť")}</button></div>`;
  }

  if (gotoBtn && pop) {
    gotoBtn.addEventListener("click", () => {
      pop.hidden = !pop.hidden;
      if (!pop.hidden) renderGoto();
    });
    pop.addEventListener("click", async (e) => {
      const set = e.target.closest("[data-goto-set]");
      if (set) { document.getElementById("goto-at").value = set.dataset.gotoSet; return; }
      if (e.target.closest("#goto-close")) { pop.hidden = true; return; }
      if (e.target.closest("#goto-go")) {
        const value = document.getElementById("goto-at").value;
        const then = document.getElementById("goto-pause").checked ? "pause" : "run";
        try {
          await post("/api/v1/sim/goto", { until: fromLocalInput(value), then });
          pop.hidden = true;
          refreshClock();
        } catch (err) { renderGoto(err.message); }
      }
    });
  }

  const sel = document.getElementById("speed-select");
  if (sel) sel.addEventListener("change", async () => {
    await fetch("/api/v1/sim/speed", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed: Number(sel.value) }),
    });
    sel.blur();
  });

  // close the lines menu when clicking elsewhere
  document.addEventListener("click", (e) => {
    document.querySelectorAll(".nav details[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
  });

  // ---- language of the explanations (UI labels stay English)
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };
  const lang = () => document.documentElement.dataset.lang || "en";
  document.querySelectorAll(".lang button").forEach((b) => b.addEventListener("click", () => {
    document.documentElement.dataset.lang = b.dataset.lang;
    store.set("mes-lang", b.dataset.lang);
    document.dispatchEvent(new CustomEvent("mes-lang"));
  }));
  // pick the text of the current language from an {en, sk} pair or [en, sk] array
  const tr = (pair) => (pair ? (Array.isArray(pair) ? pair[lang() === "sk" ? 1 : 0] : pair[lang()] || pair.en) : "");

  // help boxes remember whether they were closed
  document.querySelectorAll("details.help").forEach((d) => {
    if (store.get("mes-" + d.id) === "closed") d.open = false;
    d.addEventListener("toggle", () => store.set("mes-" + d.id, d.open ? "open" : "closed"));
  });

  const logout = document.getElementById("logout");
  if (logout) logout.addEventListener("click", async () => {
    await fetch("/api/v1/auth/logout", { method: "POST" });
    location.href = "/login";
  });

  async function post(url, body) {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) throw new Error((data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.statusText);
    return data;
  }

  const upill = (status, text) => `<span class="pill us-${esc(status)}">${esc(text || String(status).replace("_", " "))}</span>`;

  setInterval(refreshClock, 5000);       // pages that do not poll still see a pause or a jump
  return { esc, label, STATES, poll, setClock, duration, since, n, pill, upill, hm, dt, nowMs, lang, tr, post, store,
    localInput, fromLocalInput, refreshClock };
})();
