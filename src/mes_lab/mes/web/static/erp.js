/* The ERP page: the demand of the customers by day and how it is covered, the production orders of the ERP,
   the last call-offs, and the interface monitor with the error queue. */
(() => {
  const { esc, n, dt, pill } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const KIND = {
    PRODUCTION_ORDER: ["production order", "výrobná zákazka"], ORDER_RECEIVED: ["order received", "prijatie zákazky"],
    CONFIRMATION: ["confirmation", "hlásenie výroby"], DESADV: ["delivery note (DESADV)", "dodací list (DESADV)"],
    STOCK_COUNT: ["stock count", "inventúra"],
  };
  const MSG = { PENDING: ["MINOR_STOP", ["waiting", "čaká"]], DONE: ["RUNNING", ["done", "hotovo"]],
    ERROR: ["DOWN", ["error queue", "chybová fronta"]], DISCARDED: ["NO_ORDER", ["discarded", "zahodená"]] };
  const ORDER = { CREATED: ["NO_ORDER", ["sent", "odoslaná"]], RELEASED: ["CHANGEOVER", ["in production", "vo výrobe"]],
    CLOSED: ["RUNNING", ["closed", "uzavretá"]] };
  const st = (map, s) => { const [state, [en, sk]] = map[s] || ["NO_ORDER", [s, s]]; return pill(state, T(en, sk)); };
  const kind = (k) => esc(T(...(KIND[k] || [k, k])));
  let status = "";
  let summary = null, messages = null;
  const open = new Set();

  function renderSummary() {
    const d = summary;
    if (!d) return;
    const c = d.interface.counts;
    const errors = (c["ERP>MES|ERROR"] || 0) + (c["MES>ERP|ERROR"] || 0);
    const waiting = (c["ERP>MES|PENDING"] || 0) + (c["MES>ERP|PENDING"] || 0);
    const late = d.products.reduce((a, p) => a + p.late, 0);
    const openOrders = d.orders.filter((o) => o.status !== "CLOSED").length;
    document.getElementById("erp-state").innerHTML = !d.enabled ? pill("NO_ORDER", T("ERP off", "ERP vypnuté"))
      : d.down ? pill("DOWN", T("ERP down for maintenance", "ERP má údržbu")) : pill("RUNNING", T("ERP online", "ERP beží"));
    document.getElementById("erp-kpis").innerHTML = [
      [T("Late to customers (units)", "Meškanie u zákazníkov (ks)"), late, late > 0],
      [T("Open production orders", "Otvorené výrobné zákazky"), openOrders, false],
      [T("Messages waiting", "Čakajúce správy"), waiting, false],
      [T("Error queue", "Chybová fronta"), errors, errors > 0],
      [T("Duplicates caught · 24 h", "Zachytené duplikáty · 24 h"), d.interface.duplicates_24h, false],
      [T("Retries · 24 h", "Opakované doručenia · 24 h"), d.interface.retries_24h, false],
    ].map(([k, v, warn]) => `<div class="kpi"><div class="label">${esc(k)}</div><div class="value"${warn ? ' style="color:var(--s-down)"' : ""}>${n(v)}</div></div>`).join("");

    const days = d.products.length ? d.products[0].days.map((x) => x.due) : [];
    const today = d.today;
    const head = days.map((due) => `<th class="num">${esc(due.slice(5))}${due === today ? `<div class="faint">${T("today", "dnes")}</div>` : ""}</th>`).join("");
    document.getElementById("erp-demand").innerHTML = d.products.length && days.length ? `<table>
      <tr><th>${T("Product", "Výrobok")}</th><th class="num">${T("ERP stock", "Sklad ERP")}</th><th class="num">${T("MES counts", "MES ráta")}</th>
        <th class="num">${T("Orders bring", "Zákazky prinesú")}</th><th class="num">${T("Late", "Mešká")}</th>${head}</tr>
      ${d.products.map((p) => {
        const byDay = Object.fromEntries(p.days.map((x) => [x.due, x]));
        const cells = days.map((due) => {
          const x = byDay[due];
          if (!x) return `<td class="num faint">–</td>`;
          const done = x.open === 0, late = !done && due < today;
          const color = done ? "var(--s-running)" : late ? "var(--s-down)" : "inherit";
          const tip = `${x.firm ? T("firm", "pevné") : T("forecast", "predpoveď")} · ${T("shipped", "odišlo")} ${x.shipped} / ${x.qty}`;
          return `<td class="num" title="${esc(tip)}" style="color:${color}">${x.firm ? `<b>${n(x.qty)}</b>` : `<i>${n(x.qty)}</i>`}${x.shipped && !done ? `<div class="faint">${n(x.shipped)} ${T("out", "odišlo")}</div>` : ""}</td>`;
        }).join("");
        const lock = p.locked_until ? `<div style="color:#c47f17">🔒 ${T("stock count until", "inventúra do")} ${esc(dt(p.locked_until).slice(6))}</div>` : "";
        const diff = p.stock - p.counted;
        return `<tr><td><b>${esc(p.material)}</b><div class="faint">${esc(p.customer)} · ~${n(p.rate)}/${T("day", "deň")}</div>${lock}</td>
          <td class="num">${n(p.stock)}</td><td class="num"${Math.abs(diff) > 0 ? ` title="${esc(T("difference", "rozdiel"))} ${diff}"` : ""}>${n(p.counted)}</td>
          <td class="num">${n(p.supply)}</td><td class="num"${p.late ? ' style="color:var(--s-down);font-weight:600"' : ""}>${n(p.late)}</td>${cells}</tr>`;
      }).join("")}</table>
      <p class="muted" style="font-size:12px">${T("Bold = firm, italic = forecast. Green: shipped; red: due and not shipped. What left the plant covers the oldest demand first.",
        "Tučne = pevné, kurzívou = predpoveď. Zelená: odišlo; červená: malo odísť a neodišlo. Čo odišlo z továrne, pokrýva najstarší dopyt.")}</p>`
      : `<p class="muted">${T("No call-offs yet: the ERP gets the first ones when the plant runs.", "Zatiaľ žiadne odvolávky: ERP dostane prvé, keď závod beží.")}</p>`;

    document.getElementById("erp-orders").innerHTML = d.orders.length ? `<table>
      <tr><th>${T("Order", "Zákazka")}</th><th>${T("Product", "Výrobok")}</th><th>${T("Status", "Stav")}</th><th class="num">${T("Qty", "Množstvo")}</th>
        <th class="num">${T("Good", "Dobré")}</th><th>${T("Due", "Termín")}</th></tr>
      ${d.orders.map((o) => `<tr><td class="mono">${esc(o.number)}${o.mes_order_no ? `<div class="faint" title="${esc(T("work order of the MES", "zákazka MES"))}">${esc(o.mes_order_no)}</div>` : ""}</td>
        <td style="white-space:nowrap">${esc(o.material)}</td><td>${st(ORDER, o.status)}</td>
        <td class="num">${n(o.qty)}</td><td class="num">${n(o.good)}${o.scrap ? ` <span class="faint">+${n(o.scrap)} ${T("scrap", "šrot")}</span>` : ""}</td>
        <td class="mono"${o.late ? ' style="color:var(--s-down)"' : ""}>${esc(o.due.slice(5))}</td></tr>`).join("")}</table>`
      : `<p class="muted">${T("No production orders yet.", "Zatiaľ žiadne výrobné zákazky.")}</p>`;

    document.getElementById("erp-calloffs").innerHTML = d.call_offs.length ? `<table>
      <tr><th>${T("Call-off", "Odvolávka")}</th><th>${T("Received", "Prijatá")}</th><th>${T("Next days", "Ďalšie dni")}</th></tr>
      ${d.call_offs.map((c) => `<tr><td class="mono">${esc(c.number)}<div class="faint">${esc(c.customer)}</div></td><td class="mono">${esc(dt(c.received_at))}</td>
        <td>${c.lines.slice(0, 5).map((l) => `<span class="chip" title="${esc(l.due)}">${l.firm ? `<b>${n(l.qty)}</b>` : n(l.qty)}</span>`).join("")}…</td></tr>`).join("")}</table>`
      : `<p class="muted">–</p>`;
  }

  function renderMessages() {
    const d = messages;
    if (!d) return;
    const can = MES.can("interface");
    document.getElementById("erp-messages").innerHTML = d.messages.length ? `<table>
      <tr><th>${T("Created", "Vznikla")}</th><th>${T("Direction", "Smer")}</th><th>${T("Message", "Správa")}</th><th>${T("Status", "Stav")}</th><th>${T("What happened", "Čo sa stalo")}</th></tr>
      ${d.messages.map((m) => {
        const tries = m.attempts > 1 ? ` <span class="faint">· ${m.attempts}× ${T("delivered", "doručovaná")}</span>` : "";
        const dup = m.duplicates ? ` <span class="chip" title="${esc(T("delivered again, recognised by its ID", "doručená znova, spoznaná podľa ID"))}">${T("duplicate caught", "duplikát zachytený")}</span>` : "";
        const act = m.status === "ERROR" && can ? `<div style="margin-top:4px"><button class="btn small" data-act="reprocess" data-id="${esc(m.id)}">${T("Process again", "Spracovať znova")}</button>
          <button class="btn small" data-act="discard" data-id="${esc(m.id)}">${T("Discard", "Zahodiť")}</button></div>` : "";
        const detail = open.has(m.id) ? `<tr><td colspan="5"><pre class="mono" style="white-space:pre-wrap;font-size:11px;margin:0">${esc(JSON.stringify(m.payload, null, 1))}</pre>
          <div class="faint" style="font-size:11px">ID ${esc(m.id)}</div>
          ${m.log.map(([t, x]) => `<div style="font-size:12px"><span class="mono">${esc(dt(t))}</span> ${esc(x)}</div>`).join("")}</td></tr>` : "";
        return `<tr style="cursor:pointer" data-open="${esc(m.id)}"><td class="mono">${esc(dt(m.created_at))}</td>
          <td class="mono">${esc(m.direction.replace(">", " → "))}</td><td>${kind(m.kind)}<div class="faint mono">${esc(m.ref)}</div></td>
          <td>${st(MSG, m.status)}${tries}${dup}</td><td style="font-size:12px">${esc(m.status === "DONE" ? m.result || "" : m.error || m.result || "")}${act}</td></tr>${detail}`;
      }).join("")}</table>
      <p class="muted" style="font-size:12px">${T("Click a message for its content and history.", "Klik na správu ukáže jej obsah a históriu.")}</p>`
      : `<p class="muted">${status === "ERROR" ? T("The error queue is empty.", "Chybová fronta je prázdna.") : T("No messages.", "Žiadne správy.")}</p>`;
  }

  document.getElementById("erp-messages").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (btn) {
      e.stopPropagation();
      btn.disabled = true;
      try { await MES.post(`/api/v1/erp/messages/${encodeURIComponent(btn.dataset.id)}/${btn.dataset.act}`); }
      catch (err) { alert(err.message); }
      refresh();
      return;
    }
    const row = e.target.closest("tr[data-open]");
    if (!row) return;
    const id = row.dataset.open;
    open.has(id) ? open.delete(id) : open.add(id);
    renderMessages();
  });
  const tabs = document.querySelectorAll("#erp-tabs a");
  tabs.forEach((t) => t.addEventListener("click", (e) => {
    e.preventDefault();
    status = t.dataset.status;
    tabs.forEach((x) => x.classList.toggle("active", x === t));
    refresh();
  }));
  document.addEventListener("mes-lang", () => { renderSummary(); renderMessages(); });

  let timer = null;
  async function refresh() {
    clearTimeout(timer);
    try {
      const [a, b] = await Promise.all([fetch("/api/v1/erp", { cache: "no-store" }),
        fetch(`/api/v1/erp/messages?limit=80&status=${encodeURIComponent(status)}`, { cache: "no-store" })]);
      if (a.ok) { summary = await a.json(); MES.setClock(summary.clock); renderSummary(); }
      if (b.ok) { messages = await b.json(); renderMessages(); }
    } finally {
      timer = setTimeout(refresh, 5000);
    }
  }
  refresh();
})();
