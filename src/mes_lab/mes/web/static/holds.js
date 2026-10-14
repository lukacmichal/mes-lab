(() => {
  const { esc, n, upill, dt, post } = MES;
  const form = document.getElementById("hold-form") || document.createElement("form");
  const kind = document.getElementById("kind") || { value: "" , addEventListener() {} };
  const msg = document.getElementById("form-msg");
  const LABELS = { SERIAL: "Serial number", WORK_ORDER: "Work order number", STATION_WINDOW: "Station code (e.g. SMT-02-OVN)" };

  function syncKind() {
    if (!document.getElementById("target-label")) return;
    document.getElementById("target-label").textContent = LABELS[kind.value];
    document.querySelectorAll(".window").forEach((el) => { el.style.display = kind.value === "STATION_WINDOW" ? "" : "none"; });
    if (kind.value === "STATION_WINDOW" && !document.getElementById("to").value) {
      const now = MES.nowMs();
      if (now) {
        document.getElementById("to").value = MES.localInput(now);
        document.getElementById("from").value = MES.localInput(now - 3600 * 1000);
      }
    }
  }
  kind.addEventListener("change", syncKind);
  const params = new URLSearchParams(location.search);
  if (params.get("serial") && document.getElementById("target")) {
    kind.value = "SERIAL";
    document.getElementById("target").value = params.get("serial");
  }
  syncKind();

  function body() {
    const b = { kind: kind.value, target: document.getElementById("target").value.trim(),
      reason: document.getElementById("reason").value.trim() || "preview" };
    if (kind.value === "STATION_WINDOW") {
      b.window_from = MES.fromLocalInput(document.getElementById("from").value);
      b.window_to = MES.fromLocalInput(document.getElementById("to").value);
    }
    return b;
  }

  document.getElementById("preview")?.addEventListener("click", async () => {
    msg.textContent = "…";
    try {
      const p = await post("/api/v1/holds/preview", body());
      const parts = p.by_kind.map((r) => `${r.count} ${r.kind.toLowerCase()} ${r.status.toLowerCase().replace("_", " ")}`);
      msg.innerHTML = `Would hold <b>${n(p.affected)}</b> pieces${parts.length ? ": " + esc(parts.join(", ")) : ""}.`;
    } catch (e) { msg.textContent = e.message; }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const h = await post("/api/v1/holds", body());
      msg.innerHTML = `Hold #${h.id} created, <b>${n(h.affected)}</b> pieces held.`;
      form.reset();
      syncKind();
      refresh();
    } catch (err) { msg.textContent = err.message; }
  });

  function render(d) {
    document.getElementById("holds").innerHTML = d.holds.length ? `<table>
      <tr><th>#</th><th>Created</th><th>On</th><th>Reason</th><th class="num">Held</th><th class="num">In quarantine</th>
        <th>Status</th><th></th></tr>
      ${d.holds.map((h) => `<tr>
        <td>${h.id}</td><td class="mono">${esc(dt(h.created_at))}<div class="faint">${esc(h.created_by)}</div></td>
        <td>${esc(h.kind.replace("_", " ").toLowerCase())}<div>${h.kind === "STATION_WINDOW" ? esc(h.target)
          : `<a class="mono" href="/trace?q=${encodeURIComponent(h.target)}">${esc(h.target)}</a>`}</div>
          ${h.window_from ? `<div class="faint mono">${esc(dt(h.window_from))} – ${esc(dt(h.window_to))}</div>` : ""}</td>
        <td>${esc(h.reason)}</td><td class="num">${n(h.affected)}</td><td class="num">${n(h.in_quarantine)}</td>
        <td>${upill(h.status === "ACTIVE" ? "HOLD" : "RELEASED", h.status)}${h.disposition ? `<div class="faint">${esc(h.disposition.toLowerCase())}, ${esc(dt(h.released_at))}</div>` : ""}</td>
        <td style="white-space:nowrap">${h.status === "ACTIVE" && window.MES_CAN_ACT ? `<button class="btn small" data-id="${h.id}" data-d="RETURN">Release</button>
          <button class="btn small danger" data-id="${h.id}" data-d="SCRAP">Release + scrap</button>` : ""}</td>
      </tr>`).join("")}</table>` : `<p class="empty">No holds yet.</p>`;
  }

  document.getElementById("holds").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-id]");
    if (!b) return;
    const what = b.dataset.d === "SCRAP" ? "release the hold and SCRAP the pieces waiting in quarantine" : "release the hold and send the pieces back to production";
    if (!confirm(`Hold #${b.dataset.id}: ${what}?`)) return;
    try {
      await post(`/api/v1/holds/${b.dataset.id}/release`, { disposition: b.dataset.d });
      refresh();
    } catch (err) { alert(err.message); }
  });

  let timer = null;
  async function refresh() {
    clearTimeout(timer);
    try {
      const r = await fetch("/api/v1/holds", { cache: "no-store" });
      if (r.ok) {
        const d = await r.json();
        MES.setClock(d.clock);
        render(d);
        if (kind.value === "STATION_WINDOW") syncKind();
      }
    } finally { timer = setTimeout(refresh, 5000); }
  }
  refresh();
})();
