/* The SMT material page: per line the paste on the printer and the reels on the feeders of the placers,
   the last scans with the verdict of the MES, the floor life of the moisture-sensitive reels, and where a
   supplier lot went. */
(() => {
  const { esc, n, dt } = MES;
  const T = (en, sk) => (MES.lang() === "sk" ? sk : en);
  const PLACER = { PNP_CHIP: ["chip shooter (small parts)", "čipový automat (malé súčiastky)"], PNP_IC: ["IC placer (chips, bigger parts)", "automat na IO (čipy, väčšie diely)"] };
  const REFUSED = {
    WRONG_PART: ["wrong part", "nesprávna súčiastka"], NOT_IN_PROGRAM: ["not in the program", "nie je v programe"],
    MSL_EXPIRED: ["floor life ran out", "vypršala životnosť"], PASTE_NOT_THAWED: ["paste not thawed", "pasta nerozmrazená"],
    NOT_ORDERED: ["not ordered", "neobjednané"], LOT_ON_HOLD: ["lot on hold", "šarža blokovaná"], FIFO: ["not the oldest (FIFO)", "nie najstaršia (FIFO)"],
  };
  const KIND = { LOAD: ["load", "nasadenie"], SPLICE: ["splice", "spojenie"], PASTE: ["paste jar", "dóza pasty"],
    ARRIVAL: ["delivery note", "dodací list"], RECEIPT: ["receipt", "príjem"], ISSUE: ["out to a line", "výdaj na linku"] };
  const STATUS = { FEEDER: ["on a feeder", "na podávači"], NEXT: ["spliced on", "pripojená"], LINE: ["in the rack at the line", "v regáli pri linke"],
    BAKE: ["in the bake oven", "vo vypekacej peci"], EMPTY: ["used up", "minutá"] };
  let data = null;

  const bar = (frac, color) => `<span class="mbar"><span style="width:${Math.max(0, Math.min(100, frac * 100)).toFixed(0)}%;background:${color}"></span></span>`;
  const lotLink = (lot) => `<a class="mono" href="#" data-lot="${esc(lot)}">${esc(lot)}</a>`;
  const floorCell = (h, rules) => {
    if (h == null) return `<span class="faint">–</span>`;
    const color = h <= 0 ? "var(--s-down)" : h < 8 ? "#c47f17" : "inherit";
    return `<span style="color:${color}">${h <= 0 ? T("ran out", "vypršala") : `${h.toFixed(h < 10 ? 1 : 0)} h`}</span>`;
  };

  function render() {
    if (!data) return;
    const d = data, r = d.rules;
    const refused = Object.values(d.day.refused).reduce((a, b) => a + b, 0);
    document.getElementById("mat-kpis").innerHTML = [
      [T("Splices · 24 h", "Spojenia · 24 h"), d.day.splices], [T("Reels used up · 24 h", "Minuté cievky · 24 h"), d.day.empty],
      [T("Reels received · 24 h", "Prijaté cievky · 24 h"), d.day.received], [T("Reels to the lines · 24 h", "Cievky na linky · 24 h"), d.day.issued],
      [T("Paste jars opened · 24 h", "Otvorené dózy pasty · 24 h"), d.day.jars], [T("Scans refused · 24 h", "Odmietnuté skeny · 24 h"), refused],
    ].map(([k, v], i) => `<div class="kpi"><div class="label">${esc(k)}</div><div class="value"${i === 5 && v ? ' style="color:#c47f17"' : ""}>${n(v)}</div></div>`).join("");

    const inv = d.inventory || [];
    document.getElementById("mat-stock").innerHTML = inv.length ? `<table>
      <tr><th>${T("Part", "Súčiastka")}</th><th class="num">${T("In store", "V sklade")}</th><th class="num">${T("Days", "Dni")}</th>
        <th class="num">${T("At lines", "Pri linkách")}</th><th class="num">${T("On feeders", "V podávačoch")}</th></tr>
      ${inv.map((x) => `<tr><td><b>${esc(x.part)}</b><div class="faint">${esc(x.supplier || "")} · ${T("use", "spotreba")} ${x.per_day}/${T("day", "deň")}</div></td>
        <td class="num">${n(x.store)}</td><td class="num"${x.days < 1 ? ' style="color:var(--s-down);font-weight:600"' : x.days < 1.5 ? ' style="color:#c47f17"' : ""}>${x.days}</td>
        <td class="num">${n(x.line + x.transit)}${x.transit ? ` <span class="faint">(${n(x.transit)} ${T("on the way", "na ceste")})</span>` : ""}</td><td class="num">${n(x.feeder)}</td></tr>`).join("")}</table>
      <p class="muted" style="font-size:12px">${T("Days: how long the store lasts at the planned use; red under a day (the next truck must come).", "Dni: koľko sklad vydrží pri plánovanej spotrebe; červené pod jeden deň (musí prísť ďalší kamión).")}</p>`
      : `<p class="muted">${T("No reels in the MES yet.", "MES zatiaľ nepozná žiadne cievky.")}</p>`;
    const dl = d.deliveries || [];
    document.getElementById("mat-deliveries").innerHTML = dl.length ? `<table>
      <tr><th>${T("Arrived", "Prišiel")}</th><th>${T("Supplier", "Dodávateľ")}</th><th>${T("Delivery note", "Dodací list")}</th><th class="num">${T("Reels", "Cievky")}</th><th class="num">${T("Refused", "Odmietnuté")}</th></tr>
      ${dl.map((x) => `<tr><td class="mono">${esc(dt(x.arrived))}</td><td>${esc(x.supplier)}</td><td class="mono">${esc(x.note)}</td>
        <td class="num">${n(x.received)} / ${n(x.expected)}</td><td class="num"${x.refused ? ' style="color:#c47f17"' : ""}>${n(x.refused)}</td></tr>`).join("")}</table>`
      : `<p class="muted">${T("No truck in the last 24 hours.", "Za posledných 24 hodín žiadny kamión.")}</p>`;

    document.getElementById("mat-lines").innerHTML = d.lines.map((line) => {
      const pr = line.printer && line.printer.jar;
      const paste = pr ? `<div class="mat-paste"><b>${T("Solder paste", "Spájkovacia pasta")}</b> <span class="mono">${esc(pr.serial)}</span> · ${T("lot", "šarža")} ${lotLink(pr.lot)}
          · ${T("open", "otvorená")} ${pr.open_h.toFixed(1)} / ${r.open_h} h ${bar(pr.open_h / r.open_h, pr.open_left_h < 1 ? "var(--s-down)" : pr.open_left_h < 3 ? "#f1a33c" : "var(--s-running)")}
          · ${n(pr.prints)} ${T("prints left", "tlačí zostáva")}${pr.thawed_h != null ? ` · ${T("thawed", "rozmrazovaná")} ${pr.thawed_h} h` : ""}</div>`
        : `<div class="mat-paste faint">${T("No paste jar reported yet.", "Zatiaľ nehlásená žiadna dóza pasty.")}</div>`;
      const placers = line.placers.map((p) => {
        const rows = p.feeders.map((f) => {
          const reel = f.reel;
          if (!reel) return `<tr><td><b>${esc(f.slot)}</b><div class="faint">${esc(f.name)}</div></td><td colspan="5" class="faint">${T("empty: waiting for a reel", "prázdny: čaká na cievku")}</td></tr>`;
          const frac = reel.qty ? reel.left / reel.qty : 0;
          const eta = reel.eta_min;
          const etaTxt = eta == null ? "" : eta < 60 ? `${eta} min` : `${(eta / 60).toFixed(1)} h`;
          return `<tr><td><b>${esc(f.slot)}</b><div class="faint">${esc(f.name)}</div></td>
            <td class="mono">${esc(reel.serial)}<div>${lotLink(reel.lot)}</div></td>
            <td class="num">${n(reel.left)} ${bar(frac, frac < 0.1 ? "#f1a33c" : "var(--s-running)")}</td>
            <td class="num"${eta != null && eta <= r.splice_warn_min && !f.next ? ' style="color:#c47f17"' : ""}>${esc(etaTxt)}</td>
            <td>${f.next ? `<span class="pill st-RUNNING">${T("next spliced", "ďalšia pripojená")}</span><div class="mono faint">${esc(f.next.serial)}</div>` : ""}</td>
            <td class="num">${reel.msl ? `MSL ${esc(reel.msl)} · ${floorCell(reel.floor_left_h)}` : ""}</td></tr>`;
        }).join("");
        return `<h4 style="margin:10px 0 4px">${esc(p.station)} <span class="faint">${esc(T(...(PLACER[p.type] || [p.type, p.type])))}</span></h4>
          <table class="mat-feeders"><tr><th>${T("Feeder · part", "Podávač · súčiastka")}</th><th>${T("Reel · lot", "Cievka · šarža")}</th><th class="num">${T("Left", "Zostáva")}</th>
          <th class="num">${T("Runs out in", "Dôjde o")}</th><th></th><th class="num">${T("Floor life", "Životnosť")}</th></tr>${rows || `<tr><td colspan="6" class="faint">${T("No job set up.", "Žiadna nastavená zákazka.")}</td></tr>`}</table>`;
      }).join("");
      return `<div class="panel"><h3>${esc(line.line)}</h3>${paste}${placers}</div>`;
    }).join("") || `<div class="panel"><p class="muted">${T("No SMT line reported its material yet.", "Žiadna linka SMT ešte nehlásila materiál.")}</p></div>`;

    document.getElementById("mat-scans").innerHTML = d.scans.length ? `<table>
      <tr><th>${T("Time", "Čas")}</th><th>${T("Machine", "Stroj")}</th><th>${T("Scan", "Sken")}</th><th>${T("Reel / jar", "Cievka / dóza")}</th><th>${T("Verdict", "Verdikt")}</th></tr>
      ${d.scans.map((s) => `<tr><td class="mono">${esc(dt(s.at))}</td><td>${esc(s.station)}</td><td>${esc(T(...(KIND[s.kind] || [s.kind, s.kind])))}${s.slot ? `<div class="faint">${esc(s.slot)}</div>` : ""}</td>
        <td class="mono">${esc(s.item)}${s.part && s.part !== s.slot ? `<div class="faint">${esc(s.part)}</div>` : ""}</td>
        <td>${s.result === "OK" ? `<span class="pill st-RUNNING">OK</span>` : `<span class="pill st-DOWN" title="${esc(s.message || "")}">${esc(T(...(REFUSED[s.result] || [s.result, s.result])))}</span>`}</td></tr>`).join("")}</table>`
      : `<p class="muted">${T("No scans yet.", "Zatiaľ žiadne skeny.")}</p>`;

    document.getElementById("mat-msl").innerHTML = d.msl.length ? `<table>
      <tr><th>${T("Reel", "Cievka")}</th><th>${T("Part", "Súčiastka")}</th><th>MSL</th><th>${T("Where", "Kde")}</th><th class="num">${T("Floor life left", "Zostáva")}</th></tr>
      ${d.msl.map((x) => `<tr><td class="mono">${esc(x.serial)}</td><td>${esc(x.part)}<div>${lotLink(x.lot)}</div></td><td>${esc(x.msl)}</td>
        <td>${esc(T(...(STATUS[x.status] || [x.status, x.status])))}${x.line ? ` <span class="faint">${esc(x.line)}</span>` : ""}</td>
        <td class="num">${x.status === "BAKE" ? `<span class="faint">${T("baking", "vypeká sa")}</span>` : floorCell(x.floor_left_h)}</td></tr>`).join("")}</table>`
      : `<p class="muted">${T("No moisture-sensitive reel is open.", "Žiadna cievka citlivá na vlhkosť nie je otvorená.")}</p>`;
  }

  async function findLot(lot) {
    const box = document.getElementById("lot-out");
    if (!lot) { box.innerHTML = ""; return; }
    document.getElementById("lot").value = lot;
    box.innerHTML = `<p class="muted">…</p>`;
    try {
      const r = await fetch(`/api/v1/material/lot/${encodeURIComponent(lot)}`, { cache: "no-store" });
      const x = await r.json();
      const kinds = x.by_kind.map((k) => `${n(k.count)} ${esc(k.kind.toLowerCase())} ${esc(k.status.toLowerCase().replace("_", " "))}`).join(", ");
      box.innerHTML = `<p><b>${esc(x.lot)}</b>: ${x.reels.length ? `${n(x.reels.length)} ${T("reels", "cievok")} (${esc([...new Set(x.reels.map((q) => q.part))].join(", "))})` : ""}
        ${x.jars.length ? `${n(x.jars.length)} ${T("paste jars", "dóz pasty")}` : ""}${!x.reels.length && !x.jars.length ? T("the MES does not know this lot.", "MES túto šaržu nepozná.") : ""}</p>
        <p>${T("Panels made with it", "Panely vyrobené s ňou")}: <b>${n(x.panels)}</b> · ${T("everything made from them", "všetko, čo z nich vzniklo")}: <b>${n(x.affected)}</b>${kinds ? `<br><span class="faint">${kinds}</span>` : ""}</p>
        ${x.panels && MES.can("quality") ? `<a class="btn small" href="/holds?lot=${encodeURIComponent(x.lot)}">${T("Put the lot on hold…", "Zablokovať šaržu…")}</a>` : ""}`;
    } catch (e) { box.innerHTML = `<p class="muted">${esc(String(e))}</p>`; }
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-lot]");
    if (!a) return;
    e.preventDefault();
    findLot(a.dataset.lot);
    document.getElementById("lot-form").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  document.getElementById("lot-form").addEventListener("submit", (e) => { e.preventDefault(); findLot(document.getElementById("lot").value.trim()); });
  const start = new URLSearchParams(location.search).get("lot");
  if (start) findLot(start);
  document.addEventListener("mes-lang", render);
  MES.poll("/api/v1/material", (d) => { data = d; render(); }, 5000);
})();
