/* The connectivity page: the Unified Namespace (the MES's mirror of the MQTT tree) on the left, the OPC UA
   server of the machines on the right (browsed through the MES). Both refresh every two seconds. */
(() => {
  const { esc, pill } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const open = new Set(["acme", "acme/plant1"]);
  try { (JSON.parse(MES.store.get("mes-uns-open") || "[]")).forEach((p) => open.add(p)); } catch (e) { /* none */ }
  let filter = MES.store.get("mes-uns-filter") || "";
  let unsRows = [], unsStatus = null, node = MES.store.get("mes-opcua-node") || null, opc = null, opcError = null;
  document.getElementById("cn-filter").value = filter;

  const short = (v) => {
    if (v === null || v === undefined) return `<span class="faint">null</span>`;
    if (typeof v === "object") {
      const { t, ...rest } = v;
      const keys = Object.keys(rest);
      if ("value" in rest) {
        const more = keys.filter((k) => k !== "value" && rest[k] !== null && rest[k] !== "").map((k) => `${esc(k)}=${esc(typeof rest[k] === "object" ? JSON.stringify(rest[k]) : rest[k])}`);
        return `<b>${esc(rest.value)}</b>${more.length ? ` <span class="faint">${more.join(" · ")}</span>` : ""}`;
      }
      return keys.map((k) => `${esc(k)}=<b>${esc(typeof rest[k] === "object" && rest[k] !== null ? JSON.stringify(rest[k]) : rest[k] ?? "null")}</b>`).join(" · ");
    }
    return `<b>${esc(v)}</b>`;
  };

  // ------------------------------------------------------------------ the UNS tree
  function build(rows) {
    const root = { kids: new Map() };
    for (const r of rows) {
      let at = root, path = "";
      for (const seg of r.topic.split("/")) {
        path = path ? `${path}/${seg}` : seg;
        if (!at.kids.has(seg)) at.kids.set(seg, { name: seg, path, kids: new Map() });
        at = at.kids.get(seg);
      }
      at.row = r;
    }
    return root;
  }
  const count = (n) => (n.row ? 1 : 0) + [...n.kids.values()].reduce((a, k) => a + count(k), 0);
  function draw(n, depth) {
    const kids = [...n.kids.values()].sort((a, b) => (a.kids.size === 0) - (b.kids.size === 0) || a.name.localeCompare(b.name, undefined, { numeric: true }));
    return kids.map((k) => {
      const has = k.kids.size > 0, isOpen = open.has(k.path) || (filter && k.path.length < filter.length);
      const row = k.row;
      const value = row ? `<span class="cn-val">${short(row.payload)}</span><span class="cn-age" title="${esc(T("seconds since the last message", "sekúnd od poslednej správy"))}">${row.age_s < 60 ? `${Math.round(row.age_s)} s` : `${Math.round(row.age_s / 60)} min`}${row.retained ? " · R" : ""}</span>` : "";
      return `<div class="cn-node" style="--d:${depth}">
        <div class="cn-row${has ? " branch" : ""}" ${has ? `data-path="${esc(k.path)}"` : ""}>
          <span class="cn-tw">${has ? (isOpen ? "▾" : "▸") : ""}</span><span class="mono cn-name">${esc(k.name)}</span>
          ${has && !isOpen ? `<span class="faint small">${count(k)}</span>` : ""}${value}</div>
        ${has && isOpen ? draw(k, depth + 1) : ""}</div>`;
    }).join("");
  }
  function renderUns() {
    const s = unsStatus;
    const el = document.getElementById("cn-uns");
    if (!s || !s.configured) {
      el.innerHTML = `<p class="muted">${T("MQTT is not configured for this MES (MES_MQTT_URL).", "Tento MES nemá nastavené MQTT (MES_MQTT_URL).")}</p>`;
      document.getElementById("cn-uns-info").textContent = "";
      return;
    }
    document.getElementById("cn-uns-info").innerHTML = `${esc(s.broker)} · ${s.topics} ${T("topics", "tém")} · ${s.per_s} ${T("messages/s", "správ/s")}`;
    el.innerHTML = unsRows.length ? draw(build(unsRows), 0) : `<p class="muted">${T("No topics yet: the plant has not published anything.", "Zatiaľ žiadne témy: závod ešte nič nepublikoval.")}</p>`;
  }
  document.getElementById("cn-uns").addEventListener("click", (e) => {
    const r = e.target.closest("[data-path]");
    if (!r) return;
    const p = r.dataset.path;
    open.has(p) ? open.delete(p) : open.add(p);
    MES.store.set("mes-uns-open", JSON.stringify([...open]));
    renderUns();
  });
  let ft = null;
  document.getElementById("cn-filter").addEventListener("input", (e) => {
    filter = e.target.value.trim().replace(/\/+$/, "");
    MES.store.set("mes-uns-filter", filter);
    clearTimeout(ft);
    ft = setTimeout(refresh, 250);
  });

  // ------------------------------------------------------------------ OPC UA
  function renderOpc() {
    const path = document.getElementById("cn-path"), el = document.getElementById("cn-opcua");
    const parts = (node || "").split(".").filter(Boolean);
    path.innerHTML = [`<a href="#" data-node="">ACME</a>`, ...parts.map((p, i) => `<a href="#" data-node="${esc(parts.slice(0, i + 1).join("."))}">${esc(p)}</a>`)].join(" / ");
    if (opcError) { el.innerHTML = `<p class="muted">${esc(opcError)}</p>`; return; }
    if (!opc) return;
    el.innerHTML = `<table><tr><th>${T("Node", "Uzol")}</th><th>${T("Value", "Hodnota")}</th><th>${T("Type", "Typ")}</th></tr>
      ${opc.children.map((c) => c.class !== "Variable"
        ? `<tr><td colspan="3"><a href="#" data-node="${esc(c.node_id)}"><b>${esc(c.name)}</b></a> <span class="faint small">${esc(c.class)}</span></td></tr>`
        : `<tr><td><span class="mono">${esc(c.name)}</span><div class="faint small mono" title="NodeId">ns=${opc.ns};s=${esc(c.node_id)}</div></td>
            <td><b>${esc(c.value === null || c.value === undefined ? "null" : c.value)}</b>${c.description ? `<div class="faint small">${esc(c.description)}</div>` : ""}</td>
            <td class="mono small">${esc(c.type || "")}</td></tr>`).join("")}</table>`;
  }
  document.getElementById("cn-path").addEventListener("click", go);
  document.getElementById("cn-opcua").addEventListener("click", go);
  function go(e) {
    const a = e.target.closest("[data-node]");
    if (!a) return;
    e.preventDefault();
    node = a.dataset.node || null;
    MES.store.set("mes-opcua-node", node || "");
    opc = null;
    refresh();
  }

  function renderState() {
    const s = unsStatus;
    document.getElementById("cn-state").innerHTML = [
      s && s.configured ? pill(s.connected ? "RUNNING" : "DOWN", s.connected ? T("MQTT connected", "MQTT pripojené") : T("MQTT broker unreachable", "MQTT broker nedostupný")) : "",
      pill(opcError ? "DOWN" : "RUNNING", opcError ? T("OPC UA unreachable", "OPC UA nedostupné") : T("OPC UA online", "OPC UA beží")),
    ].join(" ");
  }

  let timer = null;
  async function refresh() {
    clearTimeout(timer);
    try {
      const [a, b] = await Promise.all([
        fetch(`/api/v1/uns?prefix=${encodeURIComponent(filter)}`, { cache: "no-store" }),
        fetch(`/api/v1/opcua/browse${node ? `?node=${encodeURIComponent(node)}` : ""}`, { cache: "no-store" })]);
      if (a.ok) { const d = await a.json(); unsRows = d.rows; unsStatus = d.status; }
      if (b.ok) { opc = await b.json(); opcError = null; } else {
        let msg = b.statusText;
        try { msg = (await b.json()).detail || msg; } catch (e) { /* not JSON */ }
        opcError = msg;
      }
      renderUns(); renderOpc(); renderState();
    } finally {
      timer = setTimeout(refresh, 2000);
    }
  }
  document.addEventListener("mes-lang", () => { renderUns(); renderOpc(); renderState(); });
  refresh();
})();
