(() => {
  const { esc, n, pill, dt } = MES;
  let status = (location.hash || "#open").slice(1);
  if (status === "all") status = "";
  const STATUS_STATE = { RELEASED: "NO_ORDER", IN_PROGRESS: "RUNNING", COMPLETED: "CHANGEOVER", CANCELLED: "BLOCKED" };

  const tabs = document.querySelectorAll("#tabs a");
  const markTabs = () => tabs.forEach((t) => t.classList.toggle("active", t.dataset.status === status));
  tabs.forEach((t) => t.addEventListener("click", () => { status = t.dataset.status; markTabs(); refresh(); }));
  markTabs();

  function jobChips(o) {
    return o.jobs.map((j) => `<span class="chip" title="${esc(j.status)}">${esc(j.operation)} @ ${esc(j.line)}:
      ${n(j.good)}/${n(j.expected)}</span>`).join("");
  }

  function render(d) {
    const c = d.counts || {};
    document.getElementById("counts").textContent =
      `${n(c.RELEASED || 0)} released · ${n(c.IN_PROGRESS || 0)} in progress · ${n(c.COMPLETED || 0)} completed`;
    document.getElementById("orders").innerHTML = d.orders.length ? `<table>
      <tr><th>Order</th><th>Material</th><th>Status</th><th class="num">Qty</th><th class="num">Good</th>
        <th class="num">Scrap</th><th>Created</th><th>Started</th><th>Completed</th><th>Jobs</th></tr>
      ${d.orders.map((o) => `<tr>
        <td class="mono">${esc(o.order_no)}</td>
        <td>${esc(o.material)}<div class="faint" style="font-size:12px">${esc(o.material_name)}</div></td>
        <td>${pill(STATUS_STATE[o.status] || "UNKNOWN", o.status.replace("_", " "))}</td>
        <td class="num">${n(o.qty)}</td><td class="num">${n(o.qty_good)}</td><td class="num">${n(o.qty_scrap)}</td>
        <td class="mono">${esc(dt(o.created_at))}</td><td class="mono">${esc(dt(o.started_at))}</td>
        <td class="mono">${esc(dt(o.completed_at))}</td><td>${jobChips(o)}</td></tr>`).join("")}</table>`
      : `<p class="empty">No work orders.</p>`;
  }

  let timer = null;
  async function refresh() {
    clearTimeout(timer);
    try {
      const r = await fetch(`/api/v1/orders?status=${encodeURIComponent(status)}&limit=300`, { cache: "no-store" });
      if (r.ok) {
        const d = await r.json();
        MES.setClock(d.clock);
        render(d);
      }
    } finally {
      timer = setTimeout(refresh, 4000);
    }
  }
  refresh();
})();
