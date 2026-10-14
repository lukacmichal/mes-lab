/* Pop-up with a live close-up of one station: point at a station (tile on the line page,
   segment of the strip on the overview) to watch it; click to keep it open. The picture
   is the same live plan as everywhere else, zoomed onto the machine, so it shows exactly
   what is inside it and how far the cycle has got. */
const StationView = (() => {
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  let pop = null;
  let view = null;
  let code = null;
  let pinned = false;
  let hideTimer = null;
  let stations = {};
  let jobs = {};
  let shown = "";
  let timer = null;
  let openedAt = 0;   // the click that follows opening it from a canvas must not close it again

  const PHASE = {
    idle: ["Empty.", "Prázdna."],
    in: ["A piece waits at the entrance (MES check-in, setup).", "Kus čaká pri vstupe (check-in v MES, nastavenie)."],
    work: ["Working on the piece.", "Pracuje na kuse."],
    verify: ["The operator checks the machine's calls.", "Operátor overuje poplachy stroja."],
    pass: ["The piece rides through; nothing to do here for this product.", "Kus prejde; pre tento výrobok tu nie je čo robiť."],
    out: ["Done; the piece waits to move on.", "Hotovo; kus čaká na posun ďalej."],
  };

  function ensure() {
    if (pop) return;
    pop = document.createElement("div");
    pop.className = "sv-pop";
    pop.setAttribute("role", "dialog");
    document.body.appendChild(pop);
    pop.addEventListener("click", (e) => { if (e.target.closest(".sv-x")) close(); });
  }

  function close() {
    if (!pop) return;
    pop.classList.remove("open");
    code = null;
    pinned = false;
    shown = "";
    if (view) view.destroy();
    view = null;
    clearInterval(timer);
  }

  function scheduleHide() {
    if (pinned || hideTimer) return;
    hideTimer = setTimeout(() => { hideTimer = null; close(); }, 250);
  }

  function cancelHide() {
    clearTimeout(hideTimer);
    hideTimer = null;
  }

  function place(anchor) {
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth || 460;
    const h = pop.offsetHeight || 420;
    const left = Math.min(Math.max(12, r.left + r.width / 2 - w / 2), window.innerWidth - w - 12);
    let top = r.bottom + 10;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 10);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  }

  /* who is coming to the machine or working at it (real people of the plan) */
  function crew(info) {
    const cr = info && info.cr;
    if (!cr) return "";
    const who = cr.p && Floor.S.people.get(cr.p);
    const role = cr.k === "call" ? (cr.role === "IT" ? T("IT technician", "IT technik") : T("maintenance technician", "technik údržby")) : T("operator", "operátor");
    const text = cr.ph === "wait" ? T(`Waiting for a ${role}`, `Čaká na: ${role}`)
      : cr.ph === "walk" ? T(`${role} is on the way`, `${role} ide k stroju`)
      : cr.k === "call" ? T(`${role} is repairing it`, `${role} to opravuje`) : T(`${role} is at the machine`, `${role} je pri stroji`);
    return `<div class="sv-text"><b>${MES.esc(text)}</b>${who ? ` · ${MES.esc(who.rec.n)}` : ""}</div>`;
  }

  function texts(s) {
    const job = s.job_id != null ? jobs[s.job_id] : null;
    const help = window.MES_TEXT || {};
    const reason = s.reason_name ? ` · ${MES.esc(s.reason_name)}` : "";
    const info = window.Floor && Floor.floor ? Floor.stationInfo(s.code) : null;
    const phase = info ? PHASE[info.ph] : null;
    const prog = info && info.w ? ` ${Math.round(info.p * 100)} %` : "";
    return `
      <div class="sv-state">${MES.pill(s.state)}<span>${MES.esc(MES.duration(MES.since(s.since)))}${reason}</span></div>
      ${phase ? `<div class="sv-text"><b>${MES.esc(T(...phase))}</b>${prog}</div>` : ""}
      ${crew(info)}
      <div class="sv-text">${MES.esc(MES.tr(help.states?.[s.state]))}</div>
      <div class="sv-text what">${MES.esc(MES.tr(help.stations?.[s.type]))}</div>
      <div class="sv-meta">${s.item ? `${T("Now", "Teraz")}: <span class="mono">${MES.esc(s.item)}</span>` : T("No piece inside right now", "Práve v nej nie je žiadny kus")}
        ${job ? ` · ${MES.esc(job.material)} ${MES.esc(job.operation || "")}` : ""}</div>
      <div class="sv-hint">${pinned ? T("Kept open – close with ×", "Ostáva otvorené – zavri cez ×")
        : T("Click the station to keep this open.", "Klikni na stanicu a okno ostane otvorené.")}
        ${T("This is the live plan zoomed onto the machine: it moves only while a piece is really in it.",
            "Toto je živý plán priblížený na stroj: hýbe sa len vtedy, keď je v ňom naozaj kus.")}</div>`;
  }

  function draw(anchor) {
    const s = stations[code];
    if (!s) return;
    ensure();
    const key = `${s.code}|${MES.lang()}`;
    if (key !== shown) {
      pop.innerHTML = `<div class="sv-top"><b>${MES.esc(s.code)}</b><span class="muted">${MES.esc(s.name || "")}</span>
          <button class="sv-x" type="button" aria-label="Close">×</button></div>
        <div class="sv-scene"><canvas></canvas></div><div class="sv-texts">${texts(s)}</div>`;
      shown = key;
      if (view) view.destroy();
      view = null;
      Floor.start().then(() => {
        if (code !== s.code || !pop || !Floor.layout.stations[s.code]) return;
        const canvas = pop.querySelector("canvas");
        view = new FloorView(canvas, { interactive: false, closeup: true, station: s.code });
      });
    } else {
      pop.querySelector(".sv-texts").innerHTML = texts(s);
    }
    pop.classList.add("open");
    if (anchor) place(anchor);
  }

  function open(stationCode, anchor, pin = false) {
    cancelHide();
    if (stationCode !== code) shown = "";
    code = stationCode;
    if (pin) pinned = true;
    draw(anchor);
    clearInterval(timer);
    timer = setInterval(() => { if (code && pop && pop.classList.contains("open")) draw(null); }, 500);
  }

  /* stations: {code: {code, name, type, state, reason_name, since, item, job_id}}, jobs: {id: {material, operation}} */
  function update(newStations, newJobs) {
    stations = newStations || {};
    jobs = newJobs || {};
    if (code && pop && pop.classList.contains("open")) draw(null);
  }

  document.addEventListener("mousemove", (e) => {
    const el = e.target.closest ? e.target.closest("[data-station]") : null;
    if (el) {
      if (!pinned || el.dataset.station === code) open(el.dataset.station, el);
      return;
    }
    if (pop && pop.contains(e.target)) { cancelHide(); return; }
    if (code) scheduleHide();
  });
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-station]");
    if (el) {
      e.preventDefault();
      pinned = false;
      open(el.dataset.station, el, true);
    } else if (pop && !pop.contains(e.target) && pinned && performance.now() - openedAt > 400) {
      close();
    }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  document.addEventListener("mes-lang", () => { shown = ""; if (code) draw(null); });

  /* open (and keep open) the pop-up next to a point of the screen, e.g. a click on the plan */
  function openAt(stationCode, x, y) {
    const anchor = { getBoundingClientRect: () => ({ left: x, right: x, top: y, bottom: y, width: 0, height: 0 }) };
    pinned = false;
    openedAt = performance.now();
    open(stationCode, anchor, true);
  }

  return { update, close, openAt };
})();
