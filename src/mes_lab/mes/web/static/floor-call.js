/* "Call a person": the MES asks for somebody of a department (maintenance, IT, the production foreman,
   logistics) to come to a machine, the way an operator does it with the andon button of a station. The
   plant gets the call with the answer to its next report (within a second), puts it on the andon board
   and sends the nearest free person; the machine goes on producing. This panel follows the call: who
   comes, when he is there, how long it took. */
const CallPanel = (() => {
  const { esc } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const panel = document.getElementById("call-panel");
  const button = document.getElementById("fl-call");
  const ROLES = [["MAINTENANCE", "Maintenance technician", "Technik údržby"], ["IT", "IT technician", "IT technik"],
    ["FOREMAN", "Production foreman", "Majster výroby"], ["LOGISTICS", "Logistics worker", "Skladník"]];
  const S = { station: null, role: "MAINTENANCE", minutes: 10, meter: false, follow: true, last: null, error: null };

  function stations() {
    const L = Floor.layout;
    if (!L) return "";
    const groups = {};
    for (const [code, st] of Object.entries(L.stations)) (groups[st.line] = groups[st.line] || []).push([code, st]);
    return Object.entries(groups).map(([line, list]) => `<optgroup label="${esc(line)}">${list.map(([code, st]) =>
      `<option value="${esc(code)}"${code === S.station ? " selected" : ""}>${esc(code)} · ${esc(st.name)}</option>`).join("")}</optgroup>`).join("");
  }

  function render() {
    if (!window.MES_CAN_ACT) {
      panel.innerHTML = `<div class="head"><b>${T("Call a person", "Privolať pracovníka")}</b><button class="x" type="button" data-call-close aria-label="Close">×</button></div>
        <p>${T("Calling somebody changes what the plant does, so it needs a signed-in user.", "Privolanie mení, čo závod robí, preto treba byť prihlásený.")}</p>
        <a class="btn small primary" href="/login?next=/">${T("Sign in", "Prihlásiť sa")}</a>`;
      return;
    }
    panel.innerHTML = `<div class="head"><b>${T("Call a person", "Privolať pracovníka")}</b><button class="x" type="button" data-call-close aria-label="Close">×</button></div>
      <p class="muted">${T("Like the andon button of a station: the nearest free person of the department walks to the machine, stays there and walks back. The machine goes on producing.", "Ako tlačidlo andon na stanici: najbližší voľný pracovník oddelenia príde k stroju, zostane tam a vráti sa. Stroj vyrába ďalej.")}</p>
      <div class="roles">${ROLES.map(([code, en, sk]) => `<label><input type="radio" name="call-role" value="${code}"${S.role === code ? " checked" : ""}> ${esc(T(en, sk))}</label>`).join("")}</div>
      <label class="field">${T("Machine", "Stroj")} <select id="call-station">${stations()}</select></label>
      <div class="row"><label class="field">${T("Stays", "Zostane")} <select id="call-minutes">${[5, 10, 20, 30].map((m) => `<option value="${m}"${S.minutes === m ? " selected" : ""}>${m} min</option>`).join("")}</select></label>
        <label class="check" id="call-meter-box"${S.role === "MAINTENANCE" ? "" : " hidden"}><input type="checkbox" id="call-meter"${S.meter ? " checked" : ""}> ${T("with measuring instruments", "s meracími prístrojmi")}</label></div>
      <label class="check"><input type="checkbox" id="call-follow"${S.follow ? " checked" : ""}> ${T("follow the person on the plan", "sledovať pracovníka na pláne")}</label>
      <div class="actions"><button class="btn small primary" type="button" id="call-go">${T("Call", "Privolať")}</button>
        <button class="btn small" type="button" data-call-close>${T("Close", "Zavrieť")}</button></div>
      <div class="status" id="call-status"></div>`;
    if (!S.station) S.station = panel.querySelector("#call-station").value;
    status();
  }

  /* what happened to the last call, from the live picture of the plant */
  function status() {
    const el = panel.querySelector("#call-status");
    if (!el) return;
    if (S.error) { el.innerHTML = `<span style="color:var(--s-down)">${esc(S.error)}</span>`; return; }
    const last = S.last;
    if (!last) { el.innerHTML = ""; return; }
    const f = Floor.floor, crew = f && f.crew;
    const call = crew && crew.calls.find((c) => c.id === last.id && c.kind === "request");
    const done = crew && (crew.answered || []).find((a) => a.id === last.id);
    const role = ROLES.find((r) => r[0] === last.role);
    const who = call && call.p ? Floor.S.people.get(call.p) : null;
    let html;
    if (call) {
      const ph = { wait: T("on the andon board, waiting for somebody free", "na tabuli andon, čaká na voľného pracovníka"),
        walk: T("is on the way", "ide k stroju"), fix: T("is at the machine", "je pri stroji") }[call.ph];
      html = `<b>${esc(last.station)}</b> · ${esc(T(role[1], role[2]))}: ${who ? `<a href="#" data-pick-person="${who.id}">${esc(who.rec.n)}</a> ` : ""}${esc(ph)}`;
      if (who && S.follow && !last.followed && window.floorView) { window.floorView.follow("person", who.id, 4); last.followed = true; }
    } else if (done) {
      html = `<b>${esc(last.station)}</b> · ${esc(done.p)}: ${T("came in", "prišiel za")} ${esc(MES.duration(done.walk_s))}, ${T("stayed", "zostal")} ${esc(MES.duration(done.stay_s))} ${T("and went back.", "a vrátil sa.")}`;
    } else {
      html = T("Sent to the plant…", "Odoslané do závodu…");
    }
    el.innerHTML = html;
  }

  async function call() {
    S.error = null;
    try {
      const r = await MES.post("/api/v1/crew/calls", { station: S.station, role: S.role, minutes: S.minutes, meter: S.role === "MAINTENANCE" && S.meter });
      S.last = { id: r.id, station: S.station, role: S.role, followed: false };
    } catch (e) {
      S.error = e.message;
    }
    status();
  }

  function open(station) {
    if (station) S.station = station;
    panel.classList.add("show");
    render();
  }
  function close() { panel.classList.remove("show"); }

  panel.addEventListener("change", (e) => {
    if (e.target.name === "call-role") { S.role = e.target.value; const box = panel.querySelector("#call-meter-box"); if (box) box.hidden = S.role !== "MAINTENANCE"; }
    if (e.target.id === "call-station") S.station = e.target.value;
    if (e.target.id === "call-minutes") S.minutes = +e.target.value;
    if (e.target.id === "call-meter") S.meter = e.target.checked;
    if (e.target.id === "call-follow") S.follow = e.target.checked;
  });
  panel.addEventListener("click", (e) => {
    if (e.target.closest("[data-call-close]")) close();
    if (e.target.closest("#call-go")) call();
  });
  if (button) button.addEventListener("click", () => (panel.classList.contains("show") ? close() : open()));
  document.addEventListener("mes-lang", () => { if (panel.classList.contains("show")) render(); if (button) button.textContent = T("Call a person", "Privolať pracovníka"); });
  if (button) button.textContent = T("Call a person", "Privolať pracovníka");
  Floor.listeners.add(() => { if (panel.classList.contains("show")) status(); });

  return { open, close, setStation: (code) => { S.station = code; } };
})();
