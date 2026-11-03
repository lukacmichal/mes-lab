/* Shared helpers: plant clock, polling, formatting. */
const MES = (() => {
  const clock = { ms: null, fetchedAt: 0, speed: 1, connected: false, offsetMin: 0, shift: null };

  const can = (perm) => (window.MES_PERMS || []).includes(perm);
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
    const when = document.querySelector(".clock .plant-time");      // the shift also where a narrow header has no badge
    if (when && c.shift) when.title = `Plant time · Shift ${c.shift.code} · ${c.shift.name}`;
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

  /* the UTC offset (minutes) of the plant's time zone at a moment: the rules of summer time come from Intl, so a
     time on the other side of a change is right too; without them, the offset of the plant clock */
  let zoneFormat = null;
  const zoneCache = new Map();                       // per hour: summer time changes on a whole hour
  function zoneOffset(ms) {
    const hour = Math.floor(ms / 3600000);
    const known = zoneCache.get(hour);
    if (known !== undefined) return known;
    try {
      if (!zoneFormat) {
        zoneFormat = new Intl.DateTimeFormat("en-US", { timeZone: window.MES_TZ, hourCycle: "h23", year: "numeric",
          month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
      }
      const v = {};
      for (const part of zoneFormat.formatToParts(new Date(ms))) v[part.type] = part.value;
      const wall = Date.UTC(+v.year, +v.month - 1, +v.day, +v.hour % 24, +v.minute, +v.second);
      const off = Math.round((wall - Math.floor(ms / 1000) * 1000) / 60000);
      if (zoneCache.size > 5000) zoneCache.clear();
      zoneCache.set(hour, off);
      return off;
    } catch (e) {
      return clock.offsetMin;
    }
  }
  const zoned = () => !!window.MES_TZ && typeof Intl !== "undefined";

  // plant-local wall time parts, independent of the browser's time zone
  function localParts(ms) {
    const d = new Date(ms + (zoned() ? zoneOffset(ms) : clock.offsetMin) * 60000);
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
    const local = value.length === 16 ? value + ":00" : value;
    let off = clock.offsetMin;
    if (zoned()) {                                   // the offset at that wall time (twice: right next to a change, too)
      const wall = Date.parse(local + "Z");
      off = zoneOffset(wall - off * 60000);
      off = zoneOffset(wall - off * 60000);
    }
    const sign = off < 0 ? "-" : "+";
    const abs = Math.abs(off);
    const pad = (x) => String(x).padStart(2, "0");
    return `${local}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
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
    if (d) d.textContent = parts.date;
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
      if (can("plant")) pauseBtn.title = ctl.paused ? T("Run again", "Spustiť znova") : T("Pause: the plant stands still (machines, people, robots)", "Pauza: závod zastane (stroje, ľudia, roboty)");
    }
    if (gotoBtn) gotoBtn.classList.toggle("on", !!ctl.goto);
    if (!simBanner) return;
    if (ctl.goto) {
      const from = Date.parse(ctl.goto.from), to = Date.parse(ctl.goto.until), now = Date.parse(c.plant_time);
      const pct = Math.max(0, Math.min(100, Math.round(100 * (now - from) / Math.max(1, to - from))));
      const at = localParts(to);
      const stop = can("plant");
      simBanner.innerHTML = `<div class="bi"><p lang="en"><b>The plant runs ahead to ${at.date} ${at.hm}</b> (${pct} %), as fast as the MES keeps up; ${ctl.goto.then === "pause" ? "there it stops, so you can look around." : "then it goes on at its speed."}
          ${stop ? '<button type="button" class="btn small" data-goto-cancel>Stop here</button>' : ""}</p>
        <p lang="sk"><b>Závod beží dopredu na ${at.date} ${at.hm}</b> (${pct} %), tak rýchlo, ako MES stíha; ${ctl.goto.then === "pause" ? "tam zastane, aby si sa mohol poobzerať." : "potom pokračuje svojou rýchlosťou."}
          ${stop ? '<button type="button" class="btn small" data-goto-cancel>Zastaviť tu</button>' : ""}</p></div>
        <div class="bar"><div style="width:${pct}%"></div></div>`;
      simBanner.classList.add("show");
    } else if (ctl.paused) {
      const left = Math.ceil((ctl.pause_left_s || 0) / 60);
      const may = can("plant");
      simBanner.innerHTML = `<div class="bi"><p lang="en"><b>The plant is paused.</b> Machines, people, robots and pieces stand where they are; click anything to look at it. It runs again by itself in ${left} min.
          ${may ? '<button type="button" class="btn small primary" data-resume>Run again</button>' : ""}</p>
        <p lang="sk"><b>Závod je pozastavený.</b> Stroje, ľudia, roboty aj kusy stoja, kde sú; na čokoľvek klikni a pozri sa. Sám sa znova spustí o ${left} min.
          ${may ? '<button type="button" class="btn small primary" data-resume>Spustiť</button>' : ""}</p></div>`;
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
    // a click outside closes the pop-up (the path of the click, not the target: the pop-up may have redrawn meanwhile)
    if (pop && !pop.hidden && !e.composedPath().includes(pop) && !e.target.closest("#sim-goto")) pop.hidden = true;
  });

  /* "Go to a time". The plant keeps a calendar of its own: it ran faster than reality before, so its date can be
     months ahead of the real one. The day is picked among the next days of the plant and the time of day apart
     from it (a date picker invited the real date, which the plant had long left behind); a time that is over in
     the plant or too far is refused here, before anything is sent, and every answer of the MES is shown. */
  const DAY_MS = 24 * 3600 * 1000;
  const MAX_AHEAD_MS = 7 * DAY_MS;
  const WEEKDAY = { en: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], sk: ["ne", "po", "ut", "st", "št", "pi", "so"] };
  const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const ymd = (y, m, d) => { const t = new Date(Date.UTC(y, m - 1, d)); return t.toISOString().slice(0, 10); };

  /* "2027-03-30" the way people write it: 30. 3. 2027 / 30 Mar 2027, with the weekday if asked */
  function humanDate(iso, weekday) {
    const [y, m, d] = iso.split("-").map(Number);
    const sk = lang() === "sk";
    const text = sk ? `${d}. ${m}. ${y}` : `${d} ${MONTH[m - 1]} ${y}`;
    return weekday ? `${WEEKDAY[sk ? "sk" : "en"][new Date(Date.UTC(y, m - 1, d)).getUTCDay()]} ${text}` : text;
  }
  const realToday = () => { const d = new Date(); return ymd(d.getFullYear(), d.getMonth() + 1, d.getDate()); };
  const daysApart = (a, b) => Math.round((Date.parse(`${b}T12:00:00Z`) - Date.parse(`${a}T12:00:00Z`)) / DAY_MS);
  const plantDay = (now, days) => { const [y, m, d] = localParts(now).date.split("-").map(Number); return ymd(y, m, d + days); };
  /* day and time of day in the plant at a moment */
  const dayTime = (ms) => { const p = localParts(ms); return { day: p.date, time: p.hm }; };

  /* the next hh:00 after the plant's clock */
  function nextAt(now, hh) {
    let ms = Date.parse(fromLocalInput(`${localParts(now).date}T${String(hh).padStart(2, "0")}:00`));
    if (ms <= now + 60000) ms += DAY_MS;
    return dayTime(ms);
  }

  /* why the MES (or the network) refused it, in the language of the page */
  function gotoError(message) {
    const m = String(message || "");
    if (/go back in time/.test(m)) return T("That time is already over in the plant: pick a later one (tomorrow, for example).", "Tento čas už v závode bol: vyber neskorší (napríklad zajtra).");
    if (/seven days/.test(m)) return T("At most seven days ahead.", "Najviac sedem dní dopredu.");
    if (/sign in/i.test(m)) return T("You are not signed in any more: sign in (top right) and try again.", "Už nie si prihlásený: prihlás sa (vpravo hore) a skús to znova.");
    if (/fetch|network|load failed/i.test(m)) return T("The server did not answer: try again in a moment.", "Server neodpovedal: skús to o chvíľu znova.");
    return m;
  }

  function renderGoto(error, keep) {
    const now = nowMs();
    if (now === null) { pop.innerHTML = `<b>${T("Go to a time", "Posunúť čas")}</b><p class="muted" style="margin:0">${T("Waiting for the plant clock…", "Čakám na hodiny závodu…")}</p>`; return; }
    const at = keep || { ...dayTime(now + 3600 * 1000), pause: true };
    const today = plantDay(now, 0);
    const ahead = daysApart(realToday(), today);
    const days = [...Array(8).keys()].map((i) => plantDay(now, i));
    const name = (i) => (i === 0 ? T("today", "dnes") + " · " : i === 1 ? T("tomorrow", "zajtra") + " · " : "");
    const real = Date.now();                       // the real clock: the plant behind it can catch up in one go
    const catchUp = real - now > 60000 && real - now < MAX_AHEAD_MS
      ? [[T("Now", "Teraz"), dayTime(real), T("the real time", "skutočný čas")]] : [];
    const quick = [...catchUp, ["+1 h", dayTime(now + 3600 * 1000), ""], ["+4 h", dayTime(now + 4 * 3600 * 1000), ""],
      ["06:00", nextAt(now, 6), T("morning shift", "ranná zmena")], ["14:00", nextAt(now, 14), T("afternoon shift", "poobedná zmena")],
      ["22:00", nextAt(now, 22), T("night shift", "nočná zmena")]];
    const calendar = ahead !== 0
      ? T(` It is ${Math.abs(ahead)} days ${ahead > 0 ? "ahead of" : "behind"} today (${humanDate(realToday())}); at its midnight it goes on with the real day.`,
        ` Je o ${Math.abs(ahead)} dní ${ahead > 0 ? "pred" : "za"} dneškom (${humanDate(realToday())}); o polnoci závodu pokračuje skutočným dňom.`)
      : "";
    const back = T(" A jump past midnight shows that time; at the end of that day the plant comes back to the real day and what it did ahead is undone.",
      " Skok cez polnoc ukáže zvolený čas; na konci toho dňa sa závod vráti na skutočný deň a to, čo urobil vopred, sa zruší.");
    pop.innerHTML = `<b>${T("Go to a time", "Posunúť čas")}</b>
      <p class="muted" style="margin:0">${T("In the plant it is", "V závode je")} <b>${esc(humanDate(today, true))}, ${localParts(now).hm}</b>.${esc(calendar)}
        ${T("It goes only forward, at most seven days; an hour takes roughly half a minute.", "Ide len dopredu, najviac o sedem dní; hodina trvá zhruba pol minúty.")}${esc(back)}</p>
      <div class="when">
        <label class="field">${T("Day in the plant", "Deň v závode")}<select id="goto-day">${days.map((iso, i) =>
          `<option value="${iso}"${iso === at.day ? " selected" : ""}>${esc(name(i) + humanDate(iso, true))}</option>`).join("")}</select></label>
        <label class="field">${T("Time", "Čas")}<input type="time" id="goto-time" value="${esc(at.time)}" step="60" required></label>
      </div>
      <div class="quick">${quick.map(([label, w, title]) =>
        `<button type="button" class="btn small" data-goto-day="${w.day}" data-goto-time="${w.time}"${title ? ` title="${esc(title)}"` : ""}>${label}</button>`).join("")}</div>
      <label class="check"><input type="checkbox" id="goto-pause"${at.pause ? " checked" : ""}> ${T("stop there (pause)", "zastaviť tam (pauza)")}</label>
      ${error ? `<div class="err" role="alert">${esc(error)}</div>` : ""}
      <div style="display:flex;gap:8px"><button type="button" class="btn small primary" id="goto-go">${T("Go", "Ísť")}</button>
        <button type="button" class="btn small" id="goto-close">${T("Close", "Zavrieť")}</button></div>`;
  }

  /* under the button, whole on the screen also on a phone */
  function placeGoto() {
    const r = gotoBtn.getBoundingClientRect();
    const w = Math.min(340, window.innerWidth - 16);
    pop.style.width = `${w}px`;
    pop.style.left = `${Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8))}px`;
    pop.style.right = "auto";
    pop.style.top = `${Math.max(8, Math.min(r.bottom + 8, window.innerHeight - 120))}px`;
  }

  async function sendGoto() {
    const keep = { day: document.getElementById("goto-day").value, time: document.getElementById("goto-time").value,
                   pause: document.getElementById("goto-pause").checked };
    const now = nowMs();
    if (!keep.time) { renderGoto(T("Pick the time of day.", "Vyber čas."), keep); return; }
    const until = fromLocalInput(`${keep.day}T${keep.time}`);
    const ms = Date.parse(until);
    if (now !== null && ms <= now + 30000) { renderGoto(gotoError("go back in time"), keep); return; }
    if (now !== null && ms - now > MAX_AHEAD_MS) { renderGoto(gotoError("seven days"), keep); return; }
    const go = document.getElementById("goto-go");
    go.disabled = true;
    go.textContent = T("Sending…", "Posielam…");
    try {
      await post("/api/v1/sim/goto", { until, then: keep.pause ? "pause" : "run" });
      pop.hidden = true;
      refreshClock();
    } catch (err) { renderGoto(gotoError(err.message), keep); }
  }

  if (gotoBtn && pop) {
    document.body.appendChild(pop);              // out of the header: nothing on the page can lie over it
    gotoBtn.addEventListener("click", () => {
      pop.hidden = !pop.hidden;
      if (!pop.hidden) { renderGoto(); placeGoto(); }
    });
    window.addEventListener("resize", () => { if (!pop.hidden) placeGoto(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) pop.hidden = true; });
    document.addEventListener("mes-lang", () => {                 // the other language, with what was picked
      if (pop.hidden) return;
      const day = document.getElementById("goto-day"), time = document.getElementById("goto-time");
      renderGoto(null, day && time ? { day: day.value, time: time.value, pause: document.getElementById("goto-pause").checked } : undefined);
    });
    pop.addEventListener("click", (e) => {
      const set = e.target.closest("[data-goto-day]");
      if (set) {
        document.getElementById("goto-day").value = set.dataset.gotoDay;
        document.getElementById("goto-time").value = set.dataset.gotoTime;
        return;
      }
      if (e.target.closest("#goto-close")) { pop.hidden = true; return; }
      if (e.target.closest("#goto-go")) sendGoto();
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

  // a narrow window has the pages under the menu button: a click elsewhere or Escape closes them
  const topbar = document.querySelector(".topbar");
  const navToggle = document.getElementById("nav-toggle");
  if (topbar && navToggle) {
    const showNav = (open) => { topbar.classList.toggle("nav-open", open); navToggle.setAttribute("aria-expanded", String(open)); };
    navToggle.addEventListener("click", () => showNav(!topbar.classList.contains("nav-open")));
    document.addEventListener("click", (e) => { if (!e.target.closest("#main-nav, #nav-toggle")) showNav(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") showNav(false); });
  }

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
    localInput, fromLocalInput, refreshClock, can };
})();
