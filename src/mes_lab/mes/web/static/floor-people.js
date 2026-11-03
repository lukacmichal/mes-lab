/* The people of the plant and their places: staff building, team desks with their
   computers, the walkways, the board with the head count of the shifts, the dock with its
   trucks. People come from the snapshot: where they are, where they are walking to, what
   they are doing; nothing here moves on its own. */
const FloorPeople = (() => {
  const A = FloorArt;
  const { P, rr, fillRR, rect, line, circle, text } = A;

  const ROLE = {
    OPE: { color: "#f08a24", name: ["Operator", "Operátor"] },
    MAI: { color: "#2f73e0", name: ["Maintenance technician", "Technik údržby"] },
    IT: { color: "#8a6fe0", name: ["IT technician", "IT technik"] },
    FOR: { color: "#e0453a", name: ["Production foreman", "Majster výroby"] },
    LOG: { color: "#3aa660", name: ["Logistics worker", "Skladník"] },
    MAN: { color: "#34495e", name: ["Production manager", "Vedúci výroby"] },
    MAT: { color: "#14a3a0", name: ["Material handler", "Zásobovač"] },
  };
  const ACT = {
    post: ["at the post", "na svojom mieste"], idle: ["waiting", "čaká"], walk: ["walking", "ide"],
    fix: ["working at the machine", "pracuje pri stroji"], setup: ["changes the machine over", "prestavuje stroj"], break: ["on a break", "má prestávku"],
    supervise: ["looking at the line", "kontroluje linku"],
    check: ["checks the machine on a round (state, feeders, trays) with the handheld terminal", "kontroluje stroj na obchôdzke (stav, podávače, zásobníky) s ručným terminálom"],
    inspect: ["inspection round of maintenance: looks the machine over (noise, leaks, filters, lubrication)", "obchôdzka údržby: prezerá stroj (hluk, úniky, filtre, mazanie)"],
    esd: ["at the ESD gate", "na ESD bráne"], coat: ["putting on an ESD coat", "oblieka si ESD plášť"],
    coffee: ["at the coffee machine", "pri kávovare"], wc: ["at the toilet", "na toalete"],
    take: ["takes a piece out of the rack and scans it", "berie kus z regálu a skenuje ho"],
    put: ["puts the piece away", "odkladá kus"],
    wash: ["washes the hands", "umýva si ruky"], uncoat: ["takes off the ESD coat", "vyzlieka si ESD plášť"],
    handover: ["hands over to the next shift, then goes home", "odovzdáva zmenu nástupcovi, potom ide domov"],
    cards: ["plays cards with a colleague (nothing to do for a while)", "hrá s kolegom karty (chvíľu nie je čo robiť)"],
    read: ["reads a book (nothing to do for a while)", "číta knihu (chvíľu nie je čo robiť)"],
    meeting: ["at the shift meeting in front of the andon board", "na porade zmeny pred andon tabuľou"],
    scan: ["scans the reels of a box against the delivery note", "skenuje cievky z krabice podľa dodacieho listu"],
    pick: ["picks the reels the lines ordered in the store, oldest first", "vychystáva v sklade cievky, ktoré linky objednali, najstaršie prvé"],
  };
  const SUPPLIER = { OHMWERK: "#2e6fbf", MORAVIA: "#b8452f", TISZAKON: "#3d8a4a" };
  const ESD = {
    g: ["passed the ESD gate (wrist strap and footwear tested)", "prešiel ESD bránou (náramok a obuv otestované)"],
    c: ["wears an ESD coat", "má ESD plášť"],
  };
  const CUSTOMER = { KRAFTWAGEN: "#3d6fb5", NORDAUTO: "#d6a21f", VOLTARO: "#c0504d" };

  const stateColor = (s) => P.state[s] || P.state.UNKNOWN;
  const T = (en, sk) => (typeof MES !== "undefined" && MES.lang() === "sk" ? sk : en);

  /* The right hand of somebody at a computer: now and then on the mouse, and then it moves it (small
     strokes, a pause, a click), the same way for the hand and the mouse on the desk. x forward, y right. */
  function mouseHand(id, t) {
    const on = Math.sin(t * 0.4 + id * 1.7) > 0.2;
    if (!on) return { on: false, dx: 0, dy: 0 };
    const s = t + id * 3.1;
    const burst = Math.max(0, Math.sin(s * 0.9));                 // moves in bursts, rests in between
    const dx = (Math.sin(s * 2.3) * 0.9 + Math.sin(s * 5.7) * 0.35) * burst;
    const dy = (Math.cos(s * 1.9) * 1.1 + Math.sin(s * 4.3) * 0.3) * burst;
    const click = Math.sin(s * 3.3) > 0.97 ? 0.25 : 0;          // the finger presses: the hand dips a little
    return { on: true, dx: dx - click, dy };
  }

  /* who works at the computer whose mouse lies here (sitting, at most a step away) */
  function typistAt(x, y) {
    let best = null, bd = 16;
    for (const p of Floor.S.people.values()) {
      if (!p.rec.pc || p.walking) continue;
      const d = Math.hypot(p.bx - x, p.by - y);
      if (d < bd) { bd = d; best = p; }
    }
    return best;
  }

  // ---------------------------------------------------------------- a person, from above
  const HAIR = ["#2b1d14", "#4a3020", "#6b4a2e", "#8c6239", "#b58b4c", "#d9b56b", "#1d1d1f", "#3a2a22", "#9a4a24", "#5a3a26"];
  const GREY = ["#8d8d8f", "#a9a9ab", "#6f6f72"];
  const SKIN = ["#f5d6bf", "#f1c9a5", "#ecc19c", "#f3cfb3", "#e6b590", "#e0ac83", "#d9a07a", "#b97e58"];
  const EYES = ["#5a3a1e", "#3b5f8a", "#4f6b3a", "#6b4a2e", "#2e2e2e"];

  /* How somebody looks: the same on the plan from above and on the photo in the panel. Derived from the
     person (id, man or woman), so it never changes. */
  function look(rec) {
    const id = rec.i;
    const w = rec.g === "w";
    const h = (n) => (((id * 2654435761) ^ (n * 40503 * 977)) >>> 0) % 1000 / 1000;   // stable dice per person
    const grey = h(2) < (w ? 0.08 : 0.14);
    const hair = grey ? GREY[Math.floor(h(3) * GREY.length)] : HAIR[Math.floor(h(3) * HAIR.length)];
    const style = w ? ["long", "ponytail", "bun", "bob", "long"][Math.floor(h(4) * 5)]
      : (h(4) < 0.12 ? "bald" : ["short", "short", "crew", "curly"][Math.floor(h(5) * 4)]);
    const pick = (x, table) => { let acc = 0; for (const [v, p] of table) { acc += p; if (x < acc) return v; } return table[table.length - 1][0]; };
    return {
      woman: w, hair, style, grey, skin: SKIN[Math.floor(h(1) * SKIN.length)], eyes: EYES[Math.floor(h(6) * EYES.length)],
      glasses: h(7) < 0.22, beard: !w && h(8) < 0.22, lips: w && h(10) < 0.6,
      // only on the photo: the face of everybody is a little different
      mood: pick(h(11), [["laugh", 0.12], ["smile", 0.24], ["slight", 0.2], ["smirk", 0.08], ["neutral", 0.24], ["frown", 0.12]]),
      sw: 0.9 + h(12) * 0.2, sh: 0.93 + h(13) * 0.14,                      // the size of the head
      dx: (h(14) - 0.5) * 9, dy: (h(15) - 0.5) * 6, tilt: (h(16) - 0.5) * 7,   // not quite in the middle of the photo
      ear: { rx: 3.4 + h(17) * 1.9, ry: 5.4 + h(18) * 2.8, y: (h(19) - 0.5) * 4, out: h(20) < 0.15 },
      brow: { w: 1.4 + h(21) * 1.9, arch: h(22), tilt: (h(23) - 0.5) * 3, len: 0.85 + h(24) * 0.35 },
      eyeGap: (h(25) - 0.5) * 3, eyeR: 1.55 + h(26) * 0.7, nose: 0.85 + h(27) * 0.35,
      tattoo: h(28) < 0.04 ? ["star", "dots", "swirl"][Math.floor(h(29) * 3)] : null,      // one in twenty-five
      earring: w ? (h(9) < 0.55 ? ["stud", "hoop", "drop"][Math.floor(h(30) * 3)] : null) : (h(9) < 0.07 ? "stud1" : null),
      silver: h(33) < 0.4, mole: h(31) < 0.12, freckles: h(32) < 0.08,
    };
  }

  /* hair seen from above, head at (0.9, 0), x forward: the hair lies on the back and the top of the head */
  function hairTop(ctx, lk) {
    ctx.fillStyle = lk.hair;
    switch (lk.style) {
      case "long":                                                      // over the shoulders and down the back
        ctx.beginPath(); ctx.ellipse(-1.9, 0, 2.7, 3.1, 0, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(0.3, 0, 2.55, 0, Math.PI * 2); ctx.fill();
        break;
      case "ponytail":
        ctx.beginPath(); ctx.ellipse(-3.3, 0, 1.7, 0.95, 0, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(0.25, 0, 2.4, 0, Math.PI * 2); ctx.fill();
        break;
      case "bun":
        ctx.beginPath(); ctx.arc(0.25, 0, 2.4, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "rgba(0,0,0,.18)";
        ctx.beginPath(); ctx.arc(-1.2, 0, 1.35, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = lk.hair;
        ctx.beginPath(); ctx.arc(-1.25, 0, 1.15, 0, Math.PI * 2); ctx.fill();
        break;
      case "bob":
        ctx.beginPath(); ctx.ellipse(0.1, 0, 2.6, 2.85, 0, 0, Math.PI * 2); ctx.fill();
        break;
      case "bald":                                                      // a ring of hair at the back
        ctx.strokeStyle = lk.hair; ctx.lineWidth = 0.7;
        ctx.beginPath(); ctx.arc(0.9, 0, 2.15, Math.PI * 0.62, Math.PI * 1.38); ctx.stroke();
        break;
      case "curly":
        for (let i = 0; i < 7; i++) {
          const a = Math.PI * 0.45 + i * Math.PI * 1.1 / 6;
          ctx.beginPath(); ctx.arc(0.6 + Math.cos(a) * 1.7, Math.sin(a) * 1.7, 0.95, 0, Math.PI * 2); ctx.fill();
        }
        ctx.beginPath(); ctx.arc(0.2, 0, 1.9, 0, Math.PI * 2); ctx.fill();
        break;
      case "crew":
        ctx.beginPath(); ctx.arc(0.45, 0, 2.2, 0, Math.PI * 2); ctx.fill();
        break;
      default:
        ctx.beginPath(); ctx.arc(0.25, 0, 2.35, 0, Math.PI * 2); ctx.fill();
    }
  }

  /* is a person out of sight: in a toilet cabin with the door shut */
  function hidden(p) {
    return p.rec.a === "wc" && !p.walking;
  }

  /* Seen from above: shoulders in the colour of the job (or a white ESD coat), the head with its hair and
     the face to the front, two arms. Walking the arms swing; at a computer both hands are on the keyboard
     and now and then the right one on the mouse; at a machine they work; at the logistics table they hold
     their cards or a book. Far out the figure keeps a size you can see. */
  function drawPerson(ctx, p, k, t) {
    if (hidden(p)) return;
    const rec = p.rec, role = ROLE[rec.r] || ROLE.OPE;
    const lk = look(rec);
    const walking = p.walking;
    const s = Math.max(1, 0.95 / k);
    const coat = rec.e === "c";
    const body = coat ? "#eef3f8" : role.color;
    const typing = !!rec.pc && !walking;
    const holds = !!rec.h || (rec.c === "laptop" && walking);
    let lh, rh;                                                  // hands: x forward, y to the right
    if (typing) {
      const tap = Math.sin(t * 13 + rec.i) * 0.3;
      const m = mouseHand(rec.i, t);
      lh = [6.3, -2.6 + (m.on ? tap * 0.3 : tap)];
      rh = m.on ? [6.6 + m.dx, 8.4 + m.dy] : [6.3, 2.6 - tap];
    } else if (holds) {
      lh = [5.4, -2.4]; rh = [5.4, 2.4];
    } else if (!walking && rec.a === "cards") {
      const w = Math.sin(t * 0.7 + rec.i) * 0.25;
      lh = [5.0, -1.5 + w]; rh = [5.0, 1.5 + w];
    } else if (!walking && rec.a === "read") {
      lh = [5.3, -2.6]; rh = [5.3, 2.6];
    } else if (rec.a === "fix" || rec.a === "setup" || rec.a === "take" || rec.a === "put" || rec.a === "uncoat" || rec.a === "wash" || (rec.a === "post" && rec.s)) {
      const w = Math.sin(t * 6 + rec.i) * (rec.a === "wash" ? 1.4 : 1);
      lh = [6.2 + w * 0.7, -3 + w * 0.5]; rh = [6.4 - w * 0.7, 3 + w * 0.5];
    } else if (walking) {
      const sw = Math.sin(t * 9 + rec.i) * 2;
      lh = [0.8 + sw, -5.6]; rh = [0.8 - sw, 5.6];
    } else if (rec.a === "supervise" || rec.a === "check" || rec.a === "inspect") {
      lh = [4.6, -2.2]; rh = [4.2, 2.6];                         // holds the clipboard / the handheld terminal
    } else {
      lh = [0.8, -5.5]; rh = [0.8, 5.5];
    }
    ctx.save();
    ctx.globalAlpha = p.alpha;
    ctx.translate(p.x, p.y);
    ctx.rotate(Math.atan2(p.dy, p.dx));
    ctx.scale(s, s);
    if (!walking && (rec.a === "read" || rec.a === "cards") && k > 1.2) {          // what is in the hands, under them
      if (rec.a === "read") {
        ctx.save(); ctx.translate(5.6, 0);
        fillRR(ctx, -1.6, -3.4, 3.2, 3.3, 0.3, "#f4efe2", "#8a7f66", 0.2);       // an open book: two pages
        fillRR(ctx, -1.6, 0.1, 3.2, 3.3, 0.3, "#f4efe2", "#8a7f66", 0.2);
        rect(ctx, -1.6, -0.15, 3.2, 0.3, "#7d3b2b");
        ctx.restore();
      } else {
        ctx.save(); ctx.translate(5.3, 0);
        for (let i = -2; i <= 2; i++) {                                          // a fan of cards
          ctx.save(); ctx.rotate(i * 0.22);
          fillRR(ctx, 0.2, -0.8, 2.6, 1.7, 0.2, "#fbfbfb", "#9aa3b2", 0.15);
          ctx.restore();
        }
        ctx.restore();
      }
    }
    // arms with the sleeves, hands
    ctx.strokeStyle = body; ctx.lineWidth = 2.2; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(0.4, -4.6); ctx.lineTo(lh[0], lh[1]); ctx.moveTo(0.4, 4.6); ctx.lineTo(rh[0], rh[1]); ctx.stroke();
    ctx.strokeStyle = "rgba(0,0,0,.28)"; ctx.lineWidth = 0.35;
    ctx.beginPath(); ctx.moveTo(0.4, -4.6); ctx.lineTo(lh[0], lh[1]); ctx.moveTo(0.4, 4.6); ctx.lineTo(rh[0], rh[1]); ctx.stroke();
    if (rec.e === "g" || coat) {                                    // the green wrist strap / the cuffs of the coat
      ctx.fillStyle = coat ? role.color : "#2fd27a";
      for (const h of [lh, rh]) { ctx.beginPath(); ctx.arc(h[0] - 0.9, h[1] * 0.97, 0.75, 0, Math.PI * 2); ctx.fill(); }
    }
    ctx.fillStyle = lk.skin;
    for (const h of [lh, rh]) { ctx.beginPath(); ctx.arc(h[0], h[1], 1.05, 0, Math.PI * 2); ctx.fill(); }
    // shoulders (a little narrower for a woman)
    ctx.fillStyle = body;
    ctx.beginPath(); ctx.ellipse(0, 0, 3.1, lk.woman ? 5.3 : 5.8, 0, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,.35)"; ctx.lineWidth = 0.4; ctx.stroke();
    if (coat) {                                                     // the collar of the coat in the colour of the job
      ctx.strokeStyle = role.color; ctx.lineWidth = 1.1;
      ctx.beginPath(); ctx.arc(0.3, 0, 2.9, -1.25, 1.25); ctx.stroke();
    } else if (rec.r === "LOG") {                                   // reflective bands of the warehouse vest
      ctx.fillStyle = "rgba(255,255,255,.75)";
      ctx.fillRect(-2.6, -4.6, 5.2, 0.8); ctx.fillRect(-2.6, 3.8, 5.2, 0.8);
    }
    // head: face to the front, hair on top and at the back
    ctx.fillStyle = lk.skin;
    ctx.beginPath(); ctx.arc(0.9, 0, 2.45, 0, Math.PI * 2); ctx.fill();
    hairTop(ctx, lk);
    ctx.restore();
    if (k > 1.2) {                                                  // what is carried
      ctx.save(); ctx.globalAlpha = p.alpha; ctx.translate(p.x, p.y);
      ctx.rotate(Math.atan2(p.dy, p.dx));
      if (rec.c === "laptop") { fillRR(ctx, 4.2, -3, 5.8, 6, 0.6, "#2b2f36", "#59657a", 0.3); rect(ctx, 5, -2.2, 4.2, 4.4, "#7fb2e5"); }       // the laptop of IT
      else if (rec.c === "meter") { fillRR(ctx, 4.4, 2.2, 3.4, 5.8, 0.7, "#f1c40f", "#7a6508", 0.3); rect(ctx, 4.9, 2.7, 2.4, 1.8, "#cfe9d4"); circle(ctx, 6.1, 6.8, 0.7, "#c0392b"); }  // multimeter
      else if (rec.c === "tools") fillRR(ctx, -1.2, 6.2, 5, 3.4, 0.5, "#c0392b", "#7d231a", 0.3);                                   // tool box at the side
      else if (rec.c === "reel") { circle(ctx, 5.4, 0, 2.6, "#20262f", "#a9b4c0", 0.4); circle(ctx, 5.4, 0, 0.7, "#a9b4c0"); }    // a reel of components
      else if (rec.c === "paste") { fillRR(ctx, 4.2, -1.8, 3.2, 3.6, 0.8, "#eef1f4", "#7d8896", 0.3); rect(ctx, 4.2, -1.8, 3.2, 1, "#5a8fd8"); }  // a jar of paste
      else if (rec.c === "box") fillRR(ctx, 3.8, -3.4, 5.6, 6.8, 0.5, "#c9a56b", "#8a6d3b", 0.4);                                  // a box of the supplier
      else if (rec.c === "trolley") {                                                                                         // the reel trolley, pushed
        fillRR(ctx, 4.6, -4.2, 8.4, 8.4, 0.8, P.dark ? "#3a4350" : "#b8c0cc", "#59657a", 0.4);
        for (let i = 0; i < 4; i++) circle(ctx, 6.7 + (i % 2) * 4.2, -2.1 + Math.floor(i / 2) * 4.2, 1.7, "#20262f", "#a9b4c0", 0.3);
      }
      if ((rec.r === "FOR" || rec.r === "MAN") && (rec.a === "supervise" || rec.a === "meeting")) fillRR(ctx, 3.2, -1.8, 3.4, 4.4, 0.3, "#f4f6f8", "#8795ab", 0.3);   // clipboard
      if (!walking && (rec.a === "check" || rec.a === "inspect")) {                              // the handheld terminal
        fillRR(ctx, 3.4, -1.7, 3.0, 4.6, 0.5, "#2b2f36", "#59657a", 0.3);
        rect(ctx, 3.9, -1.2, 2.0, 2.4, rec.a === "inspect" ? "#f5c66a" : "#7fd4a8");
      }
      ctx.restore();
    }
  }

  // ---------------------------------------------------------------- the names, where there is room for them
  /* Static things a name must not cover: machines, desks, furniture, terminals, racks. Kept in a grid. */
  let blockers = null;
  const CELL = 40;
  function blockerGrid(L) {
    if (blockers && blockers.L === L) return blockers;
    const grid = new Map();
    const add = (x, y, w, h) => {
      for (let i = Math.floor(x / CELL); i <= Math.floor((x + w) / CELL); i++) {
        for (let j = Math.floor(y / CELL); j <= Math.floor((y + h) / CELL); j++) {
          const key = i * 4096 + j;
          if (!grid.has(key)) grid.set(key, []);
          grid.get(key).push([x, y, x + w, y + h]);
        }
      }
    };
    for (const st of Object.values(L.stations)) add(st.x, st.y, st.w, st.h);
    for (const d of L.desks) add(d.x, d.y, d.w, d.h);
    for (const m of L.terminals) add(m.x - 4, m.y - (m.w || 8) / 2, 8, m.w || 8);
    for (const f of L.furniture) {
      if (f.kind === "railing") continue;
      if (f.kind === "computer" || f.kind === "laptop") { add(f.x - 6, f.y - 9, 12, 18); continue; }
      add(f.x, f.y, f.w, f.h);
    }
    for (const r of Object.values(L.racks || {})) add(r.x, r.y, r.w, r.h);
    for (const n of Object.values(L.nok || {})) add(n.x, n.y, n.w, n.h);
    blockers = { L, grid };
    return blockers;
  }

  function overlap(a, b) {
    const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
    const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
    return w > 0 && h > 0 ? w * h : 0;
  }

  /* The short names of the people close up. Each name goes where it covers the least: below the person,
     above, right or left (a person at the far side of a desk gets the name above, not on the keyboard); a
     name keeps its side while that side stays free, so names do not jump about. */
  function drawLabels(ctx, L, k, view) {
    if (k <= 3.4) return;
    const { grid } = blockerGrid(L);
    const size = 3.1;
    ctx.font = `600 ${size}px "Segoe UI", system-ui, sans-serif`;
    const shown = [];
    for (const p of Floor.S.people.values()) {
      if (hidden(p) || p.x < view.x0 || p.x > view.x1 || p.y < view.y0 || p.y > view.y1) continue;
      shown.push(p);
    }
    const bodies = shown.map((p) => [p.x - 5.5, p.y - 5.5, p.x + 5.5, p.y + 5.5]);
    const placed = [];
    shown.sort((a, b) => a.id - b.id);
    for (const p of shown) {
      const parts = p.rec.n.split(" ");
      const label = `${parts[0][0]}. ${parts[parts.length - 1]}`;
      const w = ctx.measureText(label).width + 1.2, h = size + 0.8;
      const spots = [[0, 9.5], [0, -9.8], [7 + w / 2, 0.3], [-7 - w / 2, 0.3], [6 + w / 2, 8], [-6 - w / 2, 8], [6 + w / 2, -8], [-6 - w / 2, -8]];
      const near = [];
      for (let i = Math.floor((p.x - 30) / CELL); i <= Math.floor((p.x + 30) / CELL); i++) {
        for (let j = Math.floor((p.y - 30) / CELL); j <= Math.floor((p.y + 30) / CELL); j++) {
          for (const r of grid.get(i * 4096 + j) || []) near.push(r);
        }
      }
      let best = -1, bestScore = Infinity;
      spots.forEach(([dx, dy], i) => {
        const box = [p.x + dx - w / 2, p.y + dy - h / 2, p.x + dx + w / 2, p.y + dy + h / 2];
        let score = i * 0.6;                                         // below first, then above, then the sides
        for (const r of near) score += overlap(box, r);
        bodies.forEach((b, j) => { if (shown[j] !== p) score += 3 * overlap(box, b); });
        for (const r of placed) score += 5 * overlap(box, r);
        if (i === p.labelSpot) score -= 4;                           // keep the side it had
        if (score < bestScore) { bestScore = score; best = i; }
      });
      p.labelSpot = best;
      const [dx, dy] = spots[best];
      placed.push([p.x + dx - w / 2, p.y + dy - h / 2, p.x + dx + w / 2, p.y + dy + h / 2]);
      ctx.globalAlpha = p.alpha;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.lineJoin = "round"; ctx.lineWidth = 1.1; ctx.strokeStyle = P.floor;              // a halo: readable on anything
      ctx.strokeText(label, p.x + dx, p.y + dy);
      ctx.fillStyle = P.text;
      ctx.fillText(label, p.x + dx, p.y + dy);
      ctx.globalAlpha = 1;
    }
  }

  // ---------------------------------------------------------------- the photo in the panel
  const svgEsc = (v) => String(v).replace(/[<>&"]/g, "");

  /* A portrait as on a company badge, made from the same look as the figure on the plan: hair colour and
     style, skin, man or woman, the clothes of the job (ESD coat, warehouse vest). */
  function portrait(rec) {
    const lk = look(rec);
    const role = ROLE[rec.r] || ROLE.OPE;
    const coat = rec.e === "c";
    const shirt = coat ? "#f2f5f9" : rec.r === "LOG" ? "#c9e83a" : role.color;
    const dark = (c, f) => {                                       // a shade of a colour
      const n = parseInt(c.slice(1), 16);
      const r = Math.round(((n >> 16) & 255) * f), g = Math.round(((n >> 8) & 255) * f), b = Math.round((n & 255) * f);
      return `rgb(${r},${g},${b})`;
    };
    const f1 = (v) => v.toFixed(1);
    const hairD = dark(lk.hair, 0.72);
    const id = `p${rec.i}`;
    const parts = [];
    // the person stands a little off the middle, the head a little turned and of its own size
    const body = `translate(${f1(lk.dx * 0.7)} ${f1(Math.max(0, lk.dy))})`;
    const head = `translate(${f1(lk.dx)} ${f1(lk.dy)}) rotate(${f1(lk.tilt)} 60 96) translate(60 62) scale(${lk.sw.toFixed(3)} ${lk.sh.toFixed(3)}) translate(-60 -62)`;
    parts.push(`<defs><linearGradient id="${id}bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dfe7f1"/><stop offset="1" stop-color="#b9c6d6"/></linearGradient>
      <radialGradient id="${id}sk" cx="0.45" cy="0.4" r="0.7"><stop offset="0" stop-color="${lk.skin}"/><stop offset="1" stop-color="${dark(lk.skin, 0.9)}"/></radialGradient>
      <clipPath id="${id}cl"><rect width="120" height="140" rx="10"/></clipPath></defs>`);
    parts.push(`<rect width="120" height="140" rx="10" fill="url(#${id}bg)"/><g clip-path="url(#${id}cl)">`);
    // long hair falls behind the shoulders
    if (lk.style === "long") parts.push(`<path transform="${head}" d="M34 60 C30 92 32 118 40 128 L80 128 C88 118 90 92 86 60 Z" fill="${lk.hair}"/>`);
    if (lk.style === "bob") parts.push(`<path transform="${head}" d="M35 58 C33 80 36 94 42 98 L78 98 C84 94 87 80 85 58 Z" fill="${lk.hair}"/>`);
    // the body: shirt, coat or vest
    parts.push(`<g transform="${body}">`);
    parts.push(`<path d="M6 150 C8 118 28 106 46 103 L74 103 C92 106 112 118 114 150 Z" fill="${shirt}" stroke="${dark(shirt === "#f2f5f9" ? "#c3ccd8" : shirt, 0.8)}" stroke-width="1"/>`);
    if (coat) parts.push(`<path d="M50 103 L60 122 L70 103" fill="none" stroke="${role.color}" stroke-width="5"/><circle cx="60" cy="130" r="1.6" fill="#9aa6b8"/>`);
    else if (rec.r === "LOG") parts.push(`<path d="M50 103 L60 116 L70 103 Z" fill="#3a4350"/><rect x="16" y="122" width="88" height="5" fill="#eef2f6" opacity=".9"/>`);
    else parts.push(`<path d="M50 103 L60 114 L70 103" fill="${dark(shirt, 0.82)}"/>`);
    // a badge on the chest
    parts.push(`<rect x="76" y="116" width="20" height="14" rx="2" fill="#ffffff" stroke="#9aa6b8" stroke-width=".8"/><rect x="76" y="116" width="20" height="4" rx="1.5" fill="${role.color}"/>`);
    parts.push(`<path d="M50 80 L50 104 C55 109 65 109 70 104 L70 80 Z" fill="${dark(lk.skin, 0.88)}"/></g>`);     // the neck
    // the head: ears, face, hair
    parts.push(`<g transform="${head}">`);
    const ear = lk.ear, ey = 64 + ear.y, ex = ear.out ? 1.4 : 0;
    parts.push(`<ellipse cx="${f1(37.8 - ex)}" cy="${f1(ey)}" rx="${f1(ear.rx)}" ry="${f1(ear.ry)}" fill="${lk.skin}"/><ellipse cx="${f1(82.2 + ex)}" cy="${f1(ey)}" rx="${f1(ear.rx)}" ry="${f1(ear.ry)}" fill="${lk.skin}"/>`);
    parts.push(`<path d="M${f1(37.6 - ex)} ${f1(ey - ear.ry * 0.5)} q-1.6 ${f1(ear.ry * 0.5)} 0 ${f1(ear.ry)} M${f1(82.4 + ex)} ${f1(ey - ear.ry * 0.5)} q1.6 ${f1(ear.ry * 0.5)} 0 ${f1(ear.ry)}" stroke="${dark(lk.skin, 0.8)}" stroke-width=".8" fill="none"/>`);
    if (lk.earring) {
      const lobe = ey + ear.ry - 0.8, gold = lk.silver ? "#c9ced6" : "#e6c25a";
      const one = (x) => lk.earring === "hoop" ? `<circle cx="${f1(x)}" cy="${f1(lobe + 2.2)}" r="2.4" fill="none" stroke="${gold}" stroke-width="1"/>`
        : lk.earring === "drop" ? `<path d="M${f1(x)} ${f1(lobe)} L${f1(x)} ${f1(lobe + 3.2)}" stroke="${gold}" stroke-width=".7"/><circle cx="${f1(x)}" cy="${f1(lobe + 4.4)}" r="1.5" fill="${lk.silver ? "#9fd3e8" : "#c0485a"}"/>`
          : `<circle cx="${f1(x)}" cy="${f1(lobe)}" r="1.4" fill="${gold}"/>`;
      parts.push(one(37.8 - ex) + (lk.earring === "stud1" ? "" : one(82.2 + ex)));
    }
    parts.push(`<ellipse cx="60" cy="62" rx="22" ry="27" fill="url(#${id}sk)"/>`);
    if (lk.beard) parts.push(`<path d="M39 66 C40 86 50 92 60 92 C70 92 80 86 81 66 C77 78 70 82 60 82 C50 82 43 78 39 66 Z" fill="${lk.hair}" opacity=".85"/>`);
    // the hair on top
    switch (lk.style) {
      case "bald": parts.push(`<path d="M38 58 C38 52 40 50 41 49 L42 62 Z M82 58 C82 52 80 50 79 49 L78 62 Z" fill="${lk.hair}"/>`); break;
      case "crew": parts.push(`<path d="M38 56 C38 38 50 33 60 33 C70 33 82 38 82 56 C78 46 70 43 60 43 C50 43 42 46 38 56 Z" fill="${lk.hair}"/>`); break;
      case "curly": parts.push(`<g fill="${lk.hair}">${[36, 43, 50, 57, 64, 71, 78, 84].map((x, i) => `<circle cx="${x}" cy="${f1(44 - Math.sin(i / 7 * Math.PI) * 9)}" r="7"/>`).join("")}</g>`); break;
      case "ponytail": parts.push(`<path d="M37 60 C35 36 50 31 60 31 C72 31 86 37 83 60 C80 48 74 42 60 42 C48 42 41 48 37 60 Z" fill="${lk.hair}"/><path d="M80 44 C92 52 92 74 86 86 C84 72 82 60 78 52 Z" fill="${hairD}"/>`); break;
      case "bun": parts.push(`<circle cx="60" cy="30" r="9" fill="${hairD}"/><path d="M37 60 C35 36 50 32 60 32 C72 32 86 37 83 60 C80 48 74 42 60 42 C48 42 41 48 37 60 Z" fill="${lk.hair}"/>`); break;
      case "long": case "bob": parts.push(`<path d="M36 66 C33 36 48 30 60 30 C74 30 88 36 84 66 C82 52 76 44 64 41 C56 46 46 48 40 52 C38 56 37 60 36 66 Z" fill="${lk.hair}"/>`); break;
      default: parts.push(`<path d="M38 58 C36 38 49 32 61 32 C73 32 85 39 82 58 C80 48 73 42 64 41 C56 44 46 45 40 50 Z" fill="${lk.hair}"/>`);
    }
    // the face: freckles, a mole, a small tattoo now and then
    const ink = "#33506e";
    if (lk.freckles) parts.push(`<g fill="${dark(lk.skin, 0.78)}">${[[46, 68], [49, 70], [52, 68.5], [68, 68.5], [71, 70], [74, 68]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r=".7"/>`).join("")}</g>`);
    if (lk.mole) parts.push(`<circle cx="${rec.i % 2 ? 72.5 : 47}" cy="${rec.i % 3 ? 74 : 69}" r=".9" fill="${dark(lk.skin, 0.55)}"/>`);
    if (lk.tattoo === "star") parts.push(`<path d="M75 66.5 l.9 2 2.1.2 -1.6 1.4 .5 2.1 -1.9 -1.1 -1.9 1.1 .5 -2.1 -1.6 -1.4 2.1 -.2 Z" fill="${ink}" opacity=".85"/>`);
    if (lk.tattoo === "dots") parts.push(`<g fill="${ink}" opacity=".85"><circle cx="72.5" cy="67.5" r=".9"/><circle cx="75.2" cy="68.8" r=".9"/><circle cx="77.8" cy="70.3" r=".9"/></g>`);
    if (lk.tattoo === "swirl") parts.push(`<path d="M41.5 66 q2 -3.2 4.2 -.6 q2 2.6 4.2 -.4 M43 70 q1.6 -1.8 3.2 0" stroke="${ink}" stroke-width=".9" fill="none" opacity=".85" stroke-linecap="round"/>`);
    // the eyebrows: thin or bushy, straight or arched; drawn together when frowning, raised when laughing
    const brow = lk.grey ? "#8a8a8c" : hairD, b = lk.brow;
    const lift = lk.mood === "laugh" ? -1.2 : lk.mood === "smile" ? -0.5 : 0;
    const inner = (lk.mood === "frown" ? 2.2 : 0) + b.tilt * 0.5;              // the inner ends lower = a frown
    const reach = 5.5 * b.len, archY = 51.5 - b.arch * 2.2 + lift;
    parts.push(`<path d="M${f1(55 - reach * 2)} ${f1(55 + lift)} Q${f1(55 - reach)} ${f1(archY)} 55 ${f1(54 + lift + inner)}" stroke="${brow}" stroke-width="${f1(b.w)}" fill="none" stroke-linecap="round"/>`);
    parts.push(`<path d="M65 ${f1(54 + lift + inner)} Q${f1(65 + reach)} ${f1(archY)} ${f1(65 + reach * 2)} ${f1(55 + lift)}" stroke="${brow}" stroke-width="${f1(b.w)}" fill="none" stroke-linecap="round"/>`);
    // the eyes: squeezed into arcs by a big laugh
    const xl = 50 - lk.eyeGap, xr = 70 + lk.eyeGap;
    if (lk.mood === "laugh") {
      parts.push(`<path d="M${f1(xl - 4)} 62 Q${f1(xl)} 58 ${f1(xl + 4)} 62 M${f1(xr - 4)} 62 Q${f1(xr)} 58 ${f1(xr + 4)} 62" stroke="#2a2a2a" stroke-width="1.5" fill="none" stroke-linecap="round"/>`);
      parts.push(`<circle cx="${f1(xl - 3)}" cy="69" r="3.6" fill="#e8838a" opacity=".22"/><circle cx="${f1(xr + 3)}" cy="69" r="3.6" fill="#e8838a" opacity=".22"/>`);
    } else {
      const ry = lk.mood === "frown" ? 2.2 : 2.6;
      parts.push(`<ellipse cx="${f1(xl)}" cy="61" rx="4" ry="${ry}" fill="#fff"/><ellipse cx="${f1(xr)}" cy="61" rx="4" ry="${ry}" fill="#fff"/>`);
      parts.push(`<circle cx="${f1(xl + 0.5)}" cy="61" r="${f1(lk.eyeR)}" fill="${lk.eyes}"/><circle cx="${f1(xr + 0.5)}" cy="61" r="${f1(lk.eyeR)}" fill="${lk.eyes}"/><circle cx="${f1(xl + 1)}" cy="60.4" r=".6" fill="#fff"/><circle cx="${f1(xr + 1)}" cy="60.4" r=".6" fill="#fff"/>`);
      if (lk.woman) parts.push(`<path d="M${f1(xl - 4.5)} 58.6 L${f1(xl - 3)} 57.8 M${f1(xr + 4.5)} 58.6 L${f1(xr + 3)} 57.8" stroke="#2a2a2a" stroke-width=".8"/>`);
    }
    const nose = lk.nose;
    parts.push(`<path d="M60 63 L${f1(58 - (nose - 1) * 2)} ${f1(63 + 9 * nose)} Q60 ${f1(65 + 9 * nose)} 62.5 ${f1(63.5 + 9 * nose)}" stroke="${dark(lk.skin, 0.72)}" stroke-width="1.3" fill="none" stroke-linecap="round"/>`);
    // the mouth
    const lips = lk.lips ? "#b5545a" : dark(lk.skin, 0.62), lw = lk.lips ? 2.4 : 1.8;
    const line = (d) => `<path d="${d}" stroke="${lips}" stroke-width="${lw}" fill="none" stroke-linecap="round"/>`;
    switch (lk.mood) {
      case "laugh":
        parts.push(`<path d="M49.5 77 Q60 92 70.5 77 Q60 80 49.5 77 Z" fill="#7a2b30" stroke="${lips}" stroke-width="${lk.lips ? 1.6 : 1}"/>`);
        parts.push(`<path d="M52 78.2 Q60 80.6 68 78.2 L67.2 80.2 Q60 82.4 52.8 80.2 Z" fill="#fbfbf7"/>`);
        break;
      case "smile": parts.push(line(`M51.5 78.5 Q60 ${lk.woman ? 85 : 84} 68.5 78.5`)); break;
      case "slight": parts.push(line("M52.5 79.5 Q60 82.6 67.5 79.5")); break;
      case "smirk": parts.push(line("M52.5 80.5 Q61 82.5 68 77.6")); break;
      case "frown": parts.push(line("M53 82 Q60 78 67 82")); break;
      default: parts.push(line("M53 80.4 Q60 81.2 67 80.4"));
    }
    if (lk.glasses) parts.push(`<g fill="rgba(200,225,255,.18)" stroke="#2e3440" stroke-width="1.6"><rect x="${f1(xl - 8)}" y="55.5" width="16" height="11" rx="4"/><rect x="${f1(xr - 8)}" y="55.5" width="16" height="11" rx="4"/><path d="M${f1(xl + 8)} 60 L${f1(xr - 8)} 60" fill="none"/></g>`);
    parts.push(`</g></g>`);
    return `<svg class="portrait" viewBox="0 0 120 140" width="96" height="112" role="img" aria-label="${svgEsc(rec.n)}">${parts.join("")}</svg>`;
  }

  // ---------------------------------------------------------------- desks and computers, from above
  /* An office chair: the seat and the backrest; `face` is where the sitter looks. Empty, it stands a bit
     away from the desk. */
  function chair(ctx, x, y, face, taken) {
    const a = face * Math.PI / 180;
    const back = taken ? 1.2 : 3.2;
    ctx.save(); ctx.translate(x - Math.cos(a) * back, y - Math.sin(a) * back); ctx.rotate(a);
    fillRR(ctx, -4.4, -4.4, 8.4, 8.8, 2.2, P.dark ? "#2a3a52" : "#6f7d93", P.dark ? "#1b2638" : "#4f5b6e", 0.3);
    fillRR(ctx, -6.4, -4.2, 2.2, 8.4, 1, P.dark ? "#22314a" : "#5b687d", null);                 // the backrest
    ctx.restore();
  }

  /* A monitor seen from above: a thin panel with its foot behind; the screen side faces `face` (degrees)
     and lights the desk in front of it in the colour of what it shows. */
  function monitorTop(ctx, x, y, w, face, color, on, k) {
    const a = face * Math.PI / 180;
    ctx.save(); ctx.translate(x, y); ctx.rotate(a);
    if (on && k > 0.8) {                                                                   // light of the screen on the desk
      ctx.fillStyle = color; ctx.globalAlpha = 0.16;
      ctx.beginPath(); ctx.moveTo(0.8, -w / 2); ctx.lineTo(5.5, -w / 2 - 2); ctx.lineTo(5.5, w / 2 + 2); ctx.lineTo(0.8, w / 2); ctx.closePath(); ctx.fill();
      ctx.globalAlpha = 1;
    }
    fillRR(ctx, -5.6, -3.2, 2.8, 6.4, 1, "#3a4350", null);                                 // the foot
    rect(ctx, -3, -0.8, 2.4, 1.6, "#3a4350");                                              // the neck
    fillRR(ctx, -0.9, -w / 2, 1.8, w, 0.5, "#1b222d", "#59657a", 0.25);                    // the panel
    rect(ctx, 0.55, -w / 2 + 0.6, 0.5, w - 1.2, on ? color : "#33404f");                   // the screen edge, lit
    ctx.restore();
  }

  function keyboard(ctx, x, y, face, k) {
    const a = face * Math.PI / 180;
    ctx.save(); ctx.translate(x, y); ctx.rotate(a);
    fillRR(ctx, -2, -6.5, 4, 13, 0.6, P.dark ? "#2f3742" : "#3a424f", null);
    if (k > 2.2) for (let i = 0; i < 3; i++) rect(ctx, -1.4 + i * 1.1, -5.9, 0.7, 11.8, P.dark ? "#4a5462" : "#5d6878");
    ctx.restore();
  }

  /* the mouse; while the hand of the person at the desk is on it, it moves with the hand */
  function mouse(ctx, x, y, face, t) {
    const a = face * Math.PI / 180;
    let dx = 0, dy = 0;
    const who = t === undefined ? null : typistAt(x, y);
    if (who) { const m = mouseHand(who.rec.i, t); if (m.on) { dx = m.dx; dy = m.dy; } }
    ctx.save(); ctx.translate(x, y); ctx.rotate(a);
    ctx.fillStyle = P.dark ? "#3a4350" : "#4a5462";
    ctx.beginPath(); ctx.ellipse(dx, dy, 1.9, 1.25, 0, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }

  /* is somebody sitting at this place (at all / working at the computer)? */
  function presentAt(x, y) {
    for (const p of Floor.S.people.values()) if (!p.walking && Math.abs(p.bx - x) < 5 && Math.abs(p.by - y) < 5) return true;
    return false;
  }
  function workingAt(x, y) {
    for (const p of Floor.S.people.values()) if (p.rec.pc && Math.abs(p.bx - x) < 5 && Math.abs(p.by - y) < 5) return true;
    return false;
  }

  /* the computer of a desk: monitor, keyboard and mouse (`m` from the plan) */
  function workplace(ctx, m, color, on, k, t) {
    const sit = m.face + 180;                      // the person looks against the screen
    monitorTop(ctx, m.x, m.y, m.w, m.face, color, on, k);
    if (m.kb && k > 0.7) keyboard(ctx, m.kb[0], m.kb[1], sit, k);
    if (m.mouse && k > 1.1) mouse(ctx, m.mouse[0], m.mouse[1], sit, t);
  }

  function drawDesks(ctx, L, floor, k, t) {
    for (const d of L.desks) {
      fillRR(ctx, d.x, d.y, d.w, d.h, 1.5, P.dark ? "#5b4a36" : "#b49a76", P.dark ? "#3c3123" : "#8a734f", 0.4);
      if (k > 0.9 && d.chair) chair(ctx, d.chair[0], d.chair[1], d.face, presentAt(d.chair[0], d.chair[1]));
    }
    for (const m of L.terminals) {
      let color = "#3ddc84", on = true;
      if (m.station) {
        const e = floor && floor.stations[m.station];
        const state = e ? e.s : "UNKNOWN";
        color = state === "RUNNING" ? "#2fbf71" : state === "DOWN" ? "#e0453a" : state === "NO_ORDER" ? "#33404f" : "#4a6a96";
        if (e && e.r === "MES_UNAVAILABLE") color = "#e0453a";
      } else if (m.desk) {
        const line = L.lines[m.desk];
        const states = line.cells.flatMap((c) => c.stations).map((s) => floor && floor.stations[s] && floor.stations[s].s);
        color = states.includes("DOWN") ? "#e0453a" : states.includes("RUNNING") ? "#2fbf71" : "#4a6a96";
      }
      workplace(ctx, m, color, on, k, t);
    }
  }

  // ---------------------------------------------------------------- what the things in the rooms are
  /* name, and what it is for, of everything that stands in the rooms (hover tip, labels on the plan) */
  const THINGS = {
    coffee: { name: ["Coffee machine", "Kávovar"], what: ["Ten minutes of coffee per person and shift (on top of the half hour lunch).", "Desať minút kávy na osobu a zmenu (navyše k polhodinovému obedu)."] },
    vending: { name: ["Vending machine", "Automat"], what: ["Drinks and snacks.", "Nápoje a sladkosti."] },
    andon: { name: ["Andon board", "Andon tabuľa"], what: ["The open calls of the plant: which machine waits for a technician, who is on the way, who repairs.", "Otvorené volania závodu: ktorý stroj čaká na technika, kto ide, kto opravuje."] },
    rack: { name: ["Server rack", "Serverový rack"], what: ["The servers of the plant (MES, network); the lights blink with the traffic.", "Servery závodu (MES, sieť); svetielka blikajú podľa prevádzky."] },
    printer: { name: ["Printer", "Tlačiareň"], what: ["Label and document printer of the IT room.", "Tlačiareň štítkov a dokumentov v IT miestnosti."] },
    cabinet: { name: ["Instrument cabinet", "Skrinka s meracími prístrojmi"], what: ["Calibrated measuring instruments: a technician takes them when a fault needs measuring.", "Kalibrované meracie prístroje: technik si ich berie, keď porucha vyžaduje meranie."] },
    shelf: { name: ["Spare parts shelf", "Regál s náhradnými dielmi"], what: ["Spare parts of the machines.", "Náhradné diely strojov."] },
    locker: { name: ["Lockers", "Šatňové skrinky"], what: ["The ESD coats: technicians, IT and the foreman put them on when they come and take them off when they go home.", "ESD plášte: technici, IT a majster si ich obliekajú pri príchode a vyzliekajú pri odchode domov."] },
    table: { name: ["Table", "Stôl"], what: ["", ""] },
    meeting: { name: ["Meeting table", "Stôl na porady"], what: ["The foreman's table for the shift meetings and the hand-over.", "Stôl majstra na porady zmeny a odovzdávanie."] },
    cards: { name: ["Logistics table", "Stôl skladníkov"], what: ["Logistics workers sit here when there is nothing to do for five minutes: cards with a colleague, a book alone. A truck calls them away.", "Tu si skladníci sadnú, keď päť minút nemajú čo robiť: s kolegom karty, sami knihu. Kamión ich odvolá."] },
    desk: { name: ["Desk", "Stôl s počítačom"], what: ["A workstation of its own for everybody of the three shifts.", "Vlastné pracovné miesto pre každého z troch zmien."] },
    cabin: { name: ["Toilet cabin", "WC kabínka"], what: ["The door closes behind you and from outside nobody sees in; red = occupied.", "Dvere sa za človekom zavrú a zvonku nikto nevidí dnu; červená = obsadené."] },
    sink: { name: ["Wash basins", "Umývadlá"], what: ["After the toilet everybody washes their hands.", "Po toalete si každý umyje ruky."] },
    esd: { name: ["ESD gate", "ESD brána"], what: ["The way in: the wrist strap and the shoes are tested, one person at a time; the barrier opens when the test passes.", "Cesta dnu: test náramku a obuvi, jeden po druhom; závora sa otvorí, keď test prejde."] },
    turnstile: { name: ["Turnstile", "Turniket"], what: ["The way out, one person at a time.", "Cesta von, po jednom."] },
    bench: { name: ["Work bench", "Pracovný stôl"], what: ["", ""] },
    laptop: { name: ["Laptop", "Notebook"], what: ["IT takes it along to a call.", "IT si ho berie na zásah."] },
    computer: { name: ["Computer", "Počítač"], what: ["", ""] },
  };
  /* the things that get a label on the plan (they are not obvious from above) */
  const LABELLED = new Set(["coffee", "vending", "andon", "rack", "printer", "cabinet", "shelf", "locker", "sink"]);

  /* SMT material beside the team desks: a reel rack at every line, the paste fridge and the thaw cabinet */
  function drawStore(ctx, L, floor, k, lang) {
    const S = L.smt_store;
    if (!S || k < 0.35) return;
    const mat = (floor && floor.mat) || {};
    const T2 = (en, sk) => (lang === "sk" ? sk : en);
    const size = Math.max(3, Math.min(5.5, 6 / k));
    for (const r of S.racks) {
      fillRR(ctx, r.x, r.y, r.w, r.h, 1, P.dark ? "#3a4350" : "#b8c0cc", P.dark ? "#59657a" : "#7d8896", 0.5);
      const count = Math.min(12, (mat.racks && mat.racks[r.line]) || 0);
      for (let i = 0; i < 3; i++) rect(ctx, r.x + 1.5, r.y + 2 + i * 6.8, r.w - 3, 0.5, P.dark ? "#59657a" : "#8a93a1");
      for (let i = 0; i < count; i++) {
        const cx = r.x + 4.6 + (i % 4) * 6.9, cy = r.y + 5.4 + Math.floor(i / 4) * 6.8;
        circle(ctx, cx, cy, 2.6, "#20262f", "#a9b4c0", 0.3);
        circle(ctx, cx, cy, 0.6, "#a9b4c0");
      }
      if (k > 0.75) text(ctx, T2("Reels", "Cievky"), r.x + r.w / 2, r.y + r.h + size * 0.9, size, P.textDim, "center", "600");
    }
    const f = S.fridge;
    if (f) {
      fillRR(ctx, f.x, f.y, f.w, f.h, 1.2, "#f2f5f9", "#5a8fd8", 0.7);
      rect(ctx, f.x + f.w - 3, f.y + 4, 0.9, f.h - 8, "#8a93a1");                             // the handle
      const cx = f.x + f.w / 2 - 1.5, cy = f.y + f.h / 2;
      for (let i = 0; i < 3; i++) {
        const an = (i * Math.PI) / 3;
        line(ctx, cx - Math.cos(an) * 3.2, cy - Math.sin(an) * 3.2, cx + Math.cos(an) * 3.2, cy + Math.sin(an) * 3.2, "#5a8fd8", 0.6);
      }
      if (k > 0.75) text(ctx, T2("Paste fridge", "Chladnička"), f.x + f.w / 2, f.y + f.h + size * 0.9, size * 0.9, P.textDim, "center", "600");
    }
    const c = S.thaw;
    if (c) {
      fillRR(ctx, c.x, c.y, c.w, c.h, 1, P.dark ? "#2b3646" : "#dfe7f1", P.dark ? "#59657a" : "#7d8896", 0.5);
      const cab = mat.cab || [];
      cab.slice(0, 8).forEach((u, i) => {
        const cx = c.x + 4 + (i % 4) * 6, cy = c.y + 6 + Math.floor(i / 4) * 9;
        const color = u >= 1 ? "#3ddc84" : u > 0.5 ? "#f1a33c" : "#5a8fd8";                   // thawed / warming / cold
        fillRR(ctx, cx - 2, cy - 2.4, 4, 4.8, 0.8, "#eef1f4", color, 0.7);
      });
      if (k > 0.75) text(ctx, T2("Thaw cabinet", "Rozmrazovanie"), c.x + c.w / 2, c.y + c.h + size * 0.9, size * 0.9, P.textDim, "center", "600");
    }
  }

  /* goods receipt south of the goods-in warehouse: the bay of the supplier trucks, the desk, the reel store */
  function drawReceiving(ctx, L, floor, k, lang, view) {
    const R = L.receiving;
    if (!R || !R.bay) return;
    const bay = R.bay, t2 = (en, sk) => (lang === "sk" ? sk : en);
    if (view && !view.seen({ x: bay.x - 40, y: bay.rear_y - 20, w: 260, h: L.world.h - bay.rear_y + 20 })) return;
    rect(ctx, bay.x - bay.trailer_w / 2 - 4, bay.rear_y - 4, bay.trailer_w + 8, L.world.h - bay.rear_y + 4, P.dark ? "#161c27" : "#cdd4de");
    const tb = R.table;
    fillRR(ctx, tb.x, tb.y, tb.w, tb.h, 1.2, P.dark ? "#5b4a36" : "#b49a76", P.dark ? "#3c3123" : "#8a734f", 0.4);
    fillRR(ctx, tb.x + tb.w - 12, tb.y + 2, 9, 7, 0.8, "#eef1f4", "#7d8896", 0.4);                 // the label printer
    rect(ctx, tb.x + tb.w - 10.5, tb.y + 8.4, 6, 1.2, "#ffffff");
    fillRR(ctx, tb.x + 4, tb.y + 4, 5, 3, 0.6, "#2b2f36", null);                                    // the hand scanner
    const st = R.store, rv = (floor && floor.recv) || {};
    const dryW = 14 * (st.dry || 0);
    fillRR(ctx, st.x, st.y, st.w - dryW - 2, st.h, 1, P.dark ? "#3a4350" : "#b8c0cc", P.dark ? "#59657a" : "#7d8896", 0.5);
    const shown = Math.min(24, Math.ceil((rv.store || 0) / 12));
    for (let i = 0; i < shown; i++) {
      const cx = st.x + 4 + (i % 12) * 5.6, cy = st.y + 6 + Math.floor(i / 12) * 11;
      circle(ctx, cx, cy, 2.4, "#20262f", "#a9b4c0", 0.3);
    }
    for (let d = 0; d < (st.dry || 0); d++) {                                                       // dry cabinets (MSL)
      const x = st.x + st.w - dryW + d * 14;
      fillRR(ctx, x, st.y - 1, 12, st.h + 2, 1, "#e8edf3", "#5a8fd8", 0.6);
      rect(ctx, x + 2, st.y + 2, 8, st.h - 4, P.dark ? "#2b3646" : "#cfdbe8");
      text(ctx, "RH", x + 6, st.y + st.h / 2 + 1.2, 3, "#5a8fd8", "center", "700");
    }
    if (k > 0.6) {
      const size = Math.max(3.2, Math.min(6, 6.5 / k));
      text(ctx, t2("Goods receipt · SMT reels", "Príjem tovaru · cievky SMT"), R.label[0], R.label[1], size, P.textDim, "left", "700");
      if (k > 1) {
        text(ctx, t2("Receiving desk", "Príjem"), tb.x + tb.w / 2, tb.y - size * 0.4, size * 0.8, P.textDim, "center", "600");
        text(ctx, t2(`Reel store · ${rv.store || 0}`, `Sklad cievok · ${rv.store || 0}`), st.x + st.w / 2, st.y - size * 0.6, size * 0.8, P.textDim, "center", "600");
      }
    }
    const truck = rv.truck;
    if (!truck) return;
    let pose = { x: bay.x, y: bay.rear_y };
    if (truck.ph !== "unload") {
      const route = Floor.S.routes && Floor.S.routes[`recv_${truck.ph}`];
      if (route) pose = Floor.ridePoint(route, Math.min(1, Math.max(0, (Floor.sim - truck.t) / Math.max(1, truck.d))));
    }
    const W = bay.trailer_w, Lt = bay.trailer_l, Cl = bay.cab_l, color = SUPPLIER[truck.s] || "#6b7686";
    ctx.save();
    ctx.translate(pose.x, pose.y);
    fillRR(ctx, -W / 2, 0, W, Lt, 2, P.dark ? "#2a3548" : "#d9dfe8", P.dark ? "#55667f" : "#8795ab", 0.8);
    fillRR(ctx, -W / 2 + 2, 2, W - 4, Lt - 4, 1, P.dark ? "#1b2433" : "#eef1f6", null);
    rect(ctx, -W / 2, 0, 2.2, Lt, color); rect(ctx, W / 2 - 2.2, 0, 2.2, Lt, color);
    for (let i = 0; i < Math.min(truck.boxes, 24); i++) {                                           // the boxes still on it
      fillRR(ctx, -W / 2 + 4 + (i % 3) * 9.6, 4 + Math.floor(i / 3) * 9.6, 8.4, 8.4, 0.6, "#c9a56b", "#8a6d3b", 0.4);
    }
    fillRR(ctx, -W / 2 + 4, Lt + 1.5, W - 8, Cl - 3, 3, color, "rgba(0,0,0,.4)", 0.6);
    fillRR(ctx, -W / 2 + 7, Lt + Cl - 12, W - 14, 6, 1.5, "#bfd9f2", null);
    ctx.restore();
    if (k > 1.4) text(ctx, truck.plate, pose.x, pose.y + Lt + Cl + 7, 4, P.text, "center", "600");
  }

  function storeAt(x, y) {
    const S = Floor.layout && Floor.layout.smt_store;
    if (!S) return null;
    const inside = (r) => r && x >= r.x - 1 && x <= r.x + r.w + 1 && y >= r.y - 1 && y <= r.y + r.h + 1;
    const R = Floor.layout.receiving;
    if (R && R.table) {
      if (inside(R.table)) return "store:recv:table";
      if (inside(R.store)) return "store:recv:store";
      const b = R.bay, tr = Floor.floor && Floor.floor.recv && Floor.floor.recv.truck;
      if (tr && inside({ x: b.x - b.trailer_w / 2, y: b.rear_y, w: b.trailer_w, h: b.trailer_l + b.cab_l })) return "store:recv:truck";
    }
    for (const r of S.racks) if (inside(r)) return `store:rack:${r.line}`;
    if (inside(S.fridge)) return "store:fridge";
    if (inside(S.thaw)) return "store:thaw";
    return null;
  }

  function storeTip(key, lang) {
    const T2 = (en, sk) => (lang === "sk" ? sk : en);
    const mat = (Floor.floor && Floor.floor.mat) || {};
    const rv = (Floor.floor && Floor.floor.recv) || {};
    if (key === "store:recv:table") return { name: T2("Receiving desk", "Príjmový stôl"), extra: "",
      what: T2("The material handler scans every reel of a supplier box against the delivery note (the MES refuses a part that was not ordered and a lot on hold) and sticks its reel ID label on.",
        "Zásobovač tu naskenuje každú cievku z krabice dodávateľa podľa dodacieho listu (MES odmietne neobjednanú súčiastku aj šaržu na blokácii) a nalepí na ňu štítok s ID cievky.") };
    if (key === "store:recv:store") return { name: T2("Reel store", "Sklad cievok"), extra: `${rv.store || 0} ${T2("reels", "cievok")} · ${rv.orders || 0} ${T2("orders open", "otvorených objednávok")}`,
      what: T2("Sealed reels, the moisture-sensitive ones in the dry cabinets (RH: low humidity). They go out oldest first (FIFO) to the racks at the SMT lines; the lines ordered " + (rv.cards || 0) + " (" + (rv.urgent || 0) + " urgent).",
        "Zapečatené cievky, citlivé na vlhkosť v suchých skriniach (RH: nízka vlhkosť). Vydávajú sa najstaršie prvé (FIFO) do regálov pri linkách SMT; linky objednali " + (rv.cards || 0) + " (" + (rv.urgent || 0) + " súrne).") };
    if (key === "store:recv:truck") {
      const t = rv.truck || {};
      return { name: T2(`Supplier truck · ${t.s || ""}`, `Kamión dodávateľa · ${t.s || ""}`), extra: `${t.plate || ""} · ${t.note || ""}`,
        what: T2(`${t.boxes || 0} boxes still on it; it leaves when the material handler has carried the last one in.`, `Ešte ${t.boxes || 0} krabíc; odíde, keď zásobovač prenesie poslednú.`) };
    }
    if (key.startsWith("store:rack:")) {
      const line = key.slice(11), nr = (mat.racks || {})[line] || 0;
      return { name: T2(`Reel rack ${line}`, `Regál cievok ${line}`), extra: `${nr} ${T2("partly used reels", "načatých cievok")}`,
        what: T2("The operator takes the next reel here for a splice (a partly used one first, else a new one from the warehouse) and puts the reels of the last job back.",
          "Tu si operátor berie ďalšiu cievku na spojenie (najprv načatú, inak novú zo skladu) a vracia sem cievky minulej zákazky.") };
    }
    if (key === "store:fridge") return { name: T2("Solder paste fridge", "Chladnička na spájkovaciu pastu"), extra: "2–10 °C",
      what: T2("The jars of paste wait here; one comes out into the thaw cabinet for every jar taken from it.", "Tu čakajú dózy pasty; za každú dózu vzatú z rozmrazovacej skrine sa do nej presunie ďalšia.") };
    const cab = mat.cab || [];
    return { name: T2("Thaw cabinet", "Rozmrazovacia skriňa"), extra: `${cab.filter((u) => u >= 1).length}/${cab.length} ${T2("ready", "pripravených")}`,
      what: T2("A jar of paste must warm up four hours before it is opened (blue cold, amber warming, green ready).",
        "Dóza pasty sa musí štyri hodiny ohrievať, kým sa otvorí (modrá studená, oranžová sa ohrieva, zelená pripravená).") };
  }

  function thingOf(f) {
    return THINGS[f.cards ? "cards" : f.kind === "table" && f.room === "office" ? "meeting" : f.kind] || null;
  }

  /* the thing of the staff building (or the logistics corner) at a point of the plan */
  function thingAt(x, y) {
    const L = Floor.layout;
    if (!L) return null;
    const store = storeAt(x, y);
    if (store) return store;
    for (let i = L.furniture.length - 1; i >= 0; i--) {
      const f = L.furniture[i];
      if (f.kind === "railing" || f.kind === "chair") continue;
      let r = f;
      if (f.kind === "computer") r = { x: f.x - 6, y: f.y - 9, w: 12, h: 18 };
      if (f.kind === "laptop") r = { x: f.x - 6, y: f.y - 6, w: 12, h: 12 };
      if (x >= r.x - 1 && x <= r.x + r.w + 1 && y >= r.y - 1 && y <= r.y + r.h + 1 && thingOf(f)) return i;
    }
    return null;
  }

  /* the tip text of a thing: name, and what it is for; a cabin says whether it is free */
  function thingTip(i, lang) {
    if (typeof i === "string") return storeTip(i, lang);
    const f = Floor.layout.furniture[i];
    const th = thingOf(f);
    if (!th) return "";
    const T2 = (pair) => (lang === "sk" ? pair[1] : pair[0]);
    let extra = "";
    if (f.kind === "cabin") extra = cabinUsed(f) ? T2(["occupied", "obsadené"]) : T2(["free", "voľné"]);
    return { name: T2(th.name), what: T2(th.what), extra };
  }

  function labelThings(ctx, L, k, lang) {
    if (k < 0.75) return;
    const size = Math.max(3.2, Math.min(6.5, 7 / k));
    const lastLocker = Math.max(...L.furniture.filter((g) => g.kind === "locker").map((g) => g.y));
    const groups = [];                                   // two racks / coffee machines side by side: one label for both
    for (const f of L.furniture) {
      const g = groups.find((x) => x.kind === f.kind && x.room === f.room && x.kind !== "locker" && Math.abs(x.y - f.y) < 1
        && f.x - (x.x + x.w) < 10 && f.x >= x.x);
      if (g) { g.w = f.x + f.w - g.x; g.n++; } else groups.push({ ...f, n: 1 });
    }
    for (const f of groups) {
      const meeting = f.kind === "table" && f.room === "office";
      if (!LABELLED.has(f.kind) && !meeting) continue;
      if (f.kind === "locker" && f.y !== lastLocker) continue;                       // once for the row, under it
      const th = thingOf(f);
      let name = lang === "sk" ? th.name[1] : th.name[0];
      if (f.n > 1) name = { coffee: ["Coffee machines", "Kávovary"], rack: ["Server racks", "Serverové racky"] }[f.kind]?.[lang === "sk" ? 1 : 0] || name;
      const below = f.kind !== "andon" && f.kind !== "shelf" && f.kind !== "cabinet";
      if (f.kind === "andon" && k > 1.4) continue;                                  // close up the board says it itself
      if (f.kind === "cabinet") continue;                                            // labelled in its drawing
      const x = f.kind === "sink" ? f.x + f.w + size * 0.5 : f.x + f.w / 2;
      const y = f.kind === "sink" ? f.y - size * 0.8 : below ? f.y + f.h + size * 0.75 : f.y - size * 0.7;
      text(ctx, name, x, y, size, P.textDim, f.kind === "sink" ? "left" : "center", "600");
    }
  }

  // ---------------------------------------------------------------- toilets
  /* is somebody in this cabin (standing in it, not walking through its door)? */
  function cabinUsed(f) {
    for (const p of Floor.S.people.values()) {
      if (p.rec.a === "wc" && !p.walking && p.bx > f.x && p.bx < f.x + f.w && p.by > f.y && p.by < f.y + f.h) return true;
    }
    return false;
  }

  /* A cabin from above: three thin walls, the door in the front wall, the toilet at the back. The door swings
     open while somebody walks through it; with somebody inside it is shut and the cabin is covered (nobody
     looks in from above either), the lamp on the door red. */
  function drawCabin(ctx, f, k, t) {
    const used = cabinUsed(f);
    const wall = P.dark ? "#7d8aa3" : "#7c889c";
    rect(ctx, f.x, f.y, f.w, f.h, P.dark ? "#223047" : "#dfe6ef");
    const bx = f.x + f.w / 2, by = f.y + f.h - 6;
    fillRR(ctx, bx - 3.2, by - 3.6, 6.4, 8.2, 3, "#f7f9fb", "#9aa6b8", 0.35);              // the toilet
    rect(ctx, bx - 4, f.y + f.h - 2.2, 8, 1.6, "#cfd6e0");                                // and its cistern
    if (used) {
      fillRR(ctx, f.x + 0.6, f.y + 0.6, f.w - 1.2, f.h - 1.2, 0.8, P.dark ? "#3a4a66" : "#b9c4d6", null);   // the roof: private
      if (k > 1.6) {
        ctx.strokeStyle = P.dark ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.08)"; ctx.lineWidth = 0.4;
        ctx.beginPath();
        for (let x = f.x + 3; x < f.x + f.w; x += 3) { ctx.moveTo(x, f.y + 1); ctx.lineTo(x, f.y + f.h - 1); }
        ctx.stroke();
      }
    }
    ctx.strokeStyle = wall; ctx.lineWidth = 1.2; ctx.lineCap = "butt";
    ctx.beginPath();
    for (const [x1, y1, x2, y2] of f.walls) { ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); }
    ctx.stroke();
    const [dx, dy] = f.door, half = f.door_w / 2;
    const passing = walkingNear(dx, dy, 9);
    ctx.strokeStyle = P.dark ? "#9fb0c8" : "#56637a"; ctx.lineWidth = 1.0;
    ctx.beginPath(); ctx.moveTo(dx - half, dy);
    if (passing) ctx.lineTo(dx - half, dy - f.door_w);                                   // swung open into the room
    else ctx.lineTo(dx + half, dy);
    ctx.stroke();
    if (passing && k > 1.2) {
      ctx.strokeStyle = P.dark ? "rgba(159,176,200,.35)" : "rgba(86,99,122,.3)"; ctx.lineWidth = 0.3; ctx.setLineDash([0.8, 0.8]);
      ctx.beginPath(); ctx.arc(dx - half, dy, f.door_w, -Math.PI / 2, 0); ctx.stroke(); ctx.setLineDash([]);
    }
    circle(ctx, dx + half - 1.6, dy - 1.4, 0.75, used ? "#e0453a" : "#2fbf71");            // the lock: red = occupied
  }

  function drawSink(ctx, f, k) {
    fillRR(ctx, f.x, f.y, f.w, f.h, 1, P.dark ? "#3a4961" : "#d9e0ea", P.rackEdge, 0.3);
    for (let i = 0; i < 2; i++) {
      const cy = f.y + f.h * (0.25 + i * 0.5);
      ctx.fillStyle = "#f4f7fa"; ctx.beginPath(); ctx.ellipse(f.x + f.w * 0.55, cy, f.w * 0.3, 4.2, 0, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "#9aa6b8"; ctx.lineWidth = 0.3; ctx.stroke();
      if (k > 1.4) rect(ctx, f.x + 0.6, cy - 0.4, 2.2, 0.8, "#8c96a8");                  // the tap
    }
  }

  /* the sign on the door of a toilet: a man / a woman */
  function wcSign(ctx, r, k) {
    if (!r.sign || k < 0.9) return;
    const x = r.door[0], y = r.y + 12;
    const color = r.sign === "w" ? "#d0587e" : "#3d78c9";
    circle(ctx, x, y - 3.2, 1.3, color);
    ctx.fillStyle = color;
    ctx.beginPath();
    if (r.sign === "w") { ctx.moveTo(x, y - 1.6); ctx.lineTo(x - 2.4, y + 2.4); ctx.lineTo(x + 2.4, y + 2.4); }
    else { ctx.rect(x - 1.4, y - 1.6, 2.8, 4.2); }
    ctx.fill();
  }

  // ---------------------------------------------------------------- the logistics corner
  function drawCorner(ctx, L, k, lang) {
    const c = L.corner;
    if (!c || !c.w) return;
    fillRR(ctx, c.x, c.y + 6, c.w, c.h - 6, 5, P.dark ? "rgba(58,166,96,.06)" : "rgba(58,166,96,.07)", P.dark ? "rgba(58,166,96,.25)" : "rgba(58,166,96,.35)", 0.6);
    if (k > 0.75) text(ctx, lang === "sk" ? c.name_sk : c.name, c.x + c.w / 2 - 20, c.y + c.h - 8, Math.max(4.4, Math.min(9, 8 / k)), P.textDim, "center", "600");
  }

  /* cards on the table while two or more play there */
  function cardsOnTable(ctx, f, k) {
    let players = 0;
    for (const p of Floor.S.people.values()) {
      if (p.rec.a === "cards" && !p.walking && p.bx > f.x - 14 && p.bx < f.x + f.w + 14 && p.by > f.y - 14 && p.by < f.y + f.h + 14) players++;
    }
    if (players < 2 || k < 1) return;
    const cx = f.x + f.w / 2, cy = f.y + f.h / 2;
    fillRR(ctx, cx - 6, cy - 2.2, 3, 4.2, 0.3, "#3d6fb5", "#24477a", 0.2);                 // the pack
    [[-1, 0.2, 0.3], [1.8, -0.6, -0.4], [3.6, 0.8, 0.6]].forEach(([dx, dy, a]) => {
      ctx.save(); ctx.translate(cx + dx, cy + dy); ctx.rotate(a);
      fillRR(ctx, -1.4, -2.1, 2.8, 4.2, 0.3, "#fbfbfb", "#9aa3b2", 0.2);
      if (k > 2.2) circle(ctx, 0, 0, 0.6, a > 0 ? "#c0392b" : "#1d1d1f");
      ctx.restore();
    });
  }

  // ---------------------------------------------------------------- the staff building
  /* is somebody sitting at this chair (at all / working at the computer)? */
  function presentAt(x, y) {
    for (const p of Floor.S.people.values()) if (!p.walking && Math.abs(p.bx - x) < 5 && Math.abs(p.by - y) < 5) return true;
    return false;
  }
  function workingAt(x, y) {
    for (const p of Floor.S.people.values()) if (p.rec.pc && Math.abs(p.bx - x) < 5 && Math.abs(p.by - y) < 5) return true;
    return false;
  }

  /* is somebody walking right here (through a gate)? */
  function walkingNear(x, y, r) {
    for (const p of Floor.S.people.values()) if (p.walking && Math.abs(p.x - x) < r && Math.abs(p.y - y) < r) return true;
    return false;
  }

  /* is somebody doing this (ESD check, coffee, toilet) at this spot of the plan? */
  function busyNear(x, y, act) {
    for (const p of Floor.S.people.values()) {
      if (p.rec.a === act && Math.abs(p.bx - x) < 11 && Math.abs(p.by - y) < 11) return true;
    }
    return false;
  }

  function furniture(ctx, f, floor, k, t, lang) {
    switch (f.kind) {
      case "locker":
        fillRR(ctx, f.x, f.y, f.w, f.h, 1, P.dark ? "#3c4c66" : "#aeb9c9", P.rackEdge, 0.3);
        rect(ctx, f.x + 1.5, f.y + f.h * 0.3, f.w - 3, 0.6, P.rackEdge);
        break;
      case "table":
        fillRR(ctx, f.x, f.y, f.w, f.h, 2, P.dark ? "#4d3f2e" : "#c4aa84", P.dark ? "#33291d" : "#9b8260", 0.4);
        if (f.cards) cardsOnTable(ctx, f, k);
        break;
      case "coffee": {
        fillRR(ctx, f.x, f.y, f.w, f.h, 1.5, "#4a5568", "#2b3340", 0.3);
        rect(ctx, f.x + 2, f.y + 2, f.w - 4, 4, "#10161f");                      // the display
        rect(ctx, f.x + 3, f.y + 3, 5, 1.6, "#3ddc84");
        rect(ctx, f.x + f.w / 2 - 2, f.y + 8, 4, 4, "#d8dde6");                  // the cup
        if (busyNear(f.x + f.w / 2, f.y + 26, "coffee") && k > 1.2) {            // steam while somebody takes a coffee
          const o = Math.sin(t * 3) * 1.2;
          ctx.strokeStyle = "rgba(230,235,245,.55)"; ctx.lineWidth = 0.5;
          ctx.beginPath(); ctx.moveTo(f.x + f.w / 2 - 1, f.y + 7); ctx.quadraticCurveTo(f.x + f.w / 2 - 2 + o, f.y + 4, f.x + f.w / 2 - 1, f.y + 1.5); ctx.stroke();
        }
        break;
      }
      case "vending": {
        fillRR(ctx, f.x, f.y, f.w, f.h, 1.5, "#33506f", "#1f3349", 0.3);
        rect(ctx, f.x + 2, f.y + 2, f.w - 4, f.h - 8, "#10161f");
        for (let i = 0; i < 3; i++) for (let j = 0; j < 2; j++) rect(ctx, f.x + 3.2 + i * 6.4, f.y + 3.2 + j * 4.2, 4.6, 3, ["#d9534f", "#f0ad4e", "#5bc0de", "#5cb85c"][(i + j * 2) % 4]);
        break;
      }
      case "chair":
        chair(ctx, f.x + f.w / 2, f.y + f.h / 2, f.face || 0, presentAt(f.x + f.w / 2, f.y + f.h / 2));
        break;
      case "cabin":
        drawCabin(ctx, f, k, t);
        break;
      case "sink":
        drawSink(ctx, f, k);
        break;
      case "esd": {
        // an ESD gate: two posts, the tester on the left post, the barrier opens when the test passes;
        // it is closed (red) while nobody is tested, amber while somebody is, open when they walk through
        const testing = busyNear(f.x + f.w / 2, f.y + f.h + 10, "esd");
        const passing = walkingNear(f.x + f.w / 2, f.y + f.h / 2, 9);
        fillRR(ctx, f.x, f.y, 3, f.h, 1, "#4a5568", "#2b3340", 0.3);
        fillRR(ctx, f.x + f.w - 3, f.y, 3, f.h, 1, "#4a5568", "#2b3340", 0.3);
        const color = passing ? "#2fbf71" : testing ? "#f1a33c" : "#e0453a";
        if (passing) {                                                                        // the barrier swung open
          line(ctx, f.x + 3, f.y + f.h / 2, f.x + 3, f.y + f.h / 2 - 7, color, 1.4);
        } else {
          rect(ctx, f.x + 3, f.y + f.h / 2 - 0.8, f.w - 6, 1.6, color);
        }
        circle(ctx, f.x + 1.5, f.y + f.h + 1.6, 1.4, color);                                   // the tester (wrist strap, shoes)
        if (k > 1.6 && f.gate === 0) A.text(ctx, T("ESD in", "ESD dnu"), f.x + f.w / 2, f.y - 3, 4, "#2fbf71", "center", "700");
        break;
      }
      case "turnstile": {
        // the way out: a free turnstile with three arms, turning while somebody goes through
        fillRR(ctx, f.x, f.y, 3, f.h, 1, "#4a5568", "#2b3340", 0.3);
        fillRR(ctx, f.x + f.w - 3, f.y, 3, f.h, 1, "#4a5568", "#2b3340", 0.3);
        const cx = f.x + f.w / 2, cy = f.y + f.h / 2;
        const turn = walkingNear(cx, cy, 10) ? t * 4 : 0;
        ctx.strokeStyle = "#8c96a8"; ctx.lineWidth = 1.1;
        ctx.beginPath();
        for (let i = 0; i < 3; i++) { const a = turn + i * 2 * Math.PI / 3; ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(a) * 7.5, cy + Math.sin(a) * 7.5); }
        ctx.stroke();
        circle(ctx, cx, cy, 1.4, "#4a5568");
        if (k > 1.6) A.text(ctx, T("out", "von"), cx, f.y - 3, 4, P.textDim, "center", "700");
        break;
      }
      case "railing":
        rect(ctx, f.x, f.y, f.w, f.h, P.dark ? "#56627a" : "#8c96a8");
        break;
      case "desk":
        fillRR(ctx, f.x, f.y, f.w, f.h, 1.5, P.dark ? "#5b4a36" : "#b49a76", P.dark ? "#3c3123" : "#8a734f", 0.4);
        break;
      case "computer": {
        const on = !f.chair || workingAt(f.chair[0], f.chair[1]);                  // the screen is on while somebody works
        workplace(ctx, f, "#4a8fd8", on, k, t);
        break;
      }
      case "laptop":
        if (f.chair && presentAt(f.chair[0], f.chair[1])) {                         // gone with the IT technician on a call
          const a = f.face * Math.PI / 180;
          ctx.save(); ctx.translate(f.x, f.y); ctx.rotate(a);
          fillRR(ctx, -1, -5.5, 7.5, 11, 0.8, "#2b2f36", "#59657a", 0.3);          // the keyboard half, towards the person
          monitorTop(ctx, -1.6, 0, 11, 0, "#7fb2e5", workingAt(f.chair[0], f.chair[1]), k);   // the lid, open
          ctx.restore();
        }
        break;
      case "cabinet":
        fillRR(ctx, f.x, f.y, f.w, f.h, 1, P.dark ? "#4a5568" : "#9aa6b8", P.rackEdge, 0.4);
        for (let i = 0; i < 4; i++) { rect(ctx, f.x + 2 + i * (f.w - 4) / 4, f.y + 2, (f.w - 4) / 4 - 1, f.h - 4, P.dark ? "#2e3a4d" : "#b5bfce"); circle(ctx, f.x + 2 + i * (f.w - 4) / 4 + (f.w - 4) / 8, f.y + f.h / 2, 0.7, "#f1c40f"); }
        if (k > 2) A.text(ctx, T("instruments", "meracie prístroje"), f.x + f.w / 2, f.y - 2.5, 3.2, P.textDim, "center", "600");
        break;
      case "rack": {
        fillRR(ctx, f.x, f.y, f.w, f.h, 1, "#20262f", "#59657a", 0.4);
        for (let i = 0; i < 7; i++) {
          rect(ctx, f.x + 2, f.y + 3 + i * 4.5, f.w - 4, 2.6, "#313b49");
          if (Math.sin(t * (2 + i) + f.x) > -0.2) circle(ctx, f.x + f.w - 4, f.y + 4.3 + i * 4.5, 0.6, i % 3 ? "#3ddc84" : "#4a8fd8");
        }
        break;
      }
      case "printer":
        fillRR(ctx, f.x, f.y, f.w, f.h, 1.5, "#cfd6e0", "#8795ab", 0.4);
        rect(ctx, f.x + 4, f.y + 3, f.w - 8, 3, "#f4f6f8");
        break;
      case "bench":
        fillRR(ctx, f.x, f.y, f.w, f.h, 1, P.dark ? "#4a5568" : "#9aa6b8", P.rackEdge, 0.4);
        rect(ctx, f.x + 6, f.y + 5, 8, 2, "#c0392b"); rect(ctx, f.x + 20, f.y + 8, 12, 2, "#d9dde4");
        break;
      case "shelf":
        fillRR(ctx, f.x, f.y, f.w, f.h, 1, P.dark ? "#2e3a4d" : "#b5bfce", P.rackEdge, 0.4);
        for (let i = 0; i < 5; i++) rect(ctx, f.x + 3 + i * 10, f.y + 4, 7, 6, i % 2 ? "#a47a46" : "#6b86b0");
        break;
      case "andon": {
        // the andon board: the open calls with their level (1 technician, 2 foreman, 3 production manager);
        // during the shift meeting the numbers of the last shift (plan against output per area)
        fillRR(ctx, f.x, f.y, f.w, f.h, 2, "#10161f", "#59657a", 0.6);
        const calls = ((floor && floor.crew && floor.crew.calls) || []).slice().sort((a, b) => (b.lv || 1) - (a.lv || 1) || a.t - b.t);
        const LV = { 1: "#4a8fd8", 2: "#f1a33c", 3: "#e0453a" };
        const meeting = floor && floor.crew && floor.crew.meeting && (typeof FloorBoards !== "undefined") && FloorBoards.data;
        if (k > 1.4) {
          if (meeting) {
            A.text(ctx, T("SHIFT MEETING · last shift", "PORADA ZMENY · minulá zmena"), f.x + 4, f.y + 5, 3.6, "#9fb0c8", "left", "700");
            const sums = {};
            for (const [code, b] of Object.entries(FloorBoards.data.lines)) {
              const area = code.split("-")[0];
              sums[area] = sums[area] || { plan: 0, actual: 0 };
              sums[area].plan += b.last_shift.plan; sums[area].actual += b.last_shift.actual;
            }
            Object.entries(sums).forEach(([area, v], i) => {
              const color = !v.plan ? "#8fa3bf" : v.actual >= 0.95 * v.plan ? "#3ddc84" : v.actual >= 0.85 * v.plan ? "#f1a33c" : "#e0453a";
              A.text(ctx, `${area}: ${v.actual} / ${v.plan}`, f.x + 6, f.y + 11.5 + i * 5.2, 3.4, color, "left", "600");
            });
          } else {
            A.text(ctx, "ANDON", f.x + 4, f.y + 5, 4, "#9fb0c8", "left", "700");
            if (!calls.length) A.text(ctx, T("no open calls", "žiadne otvorené volania"), f.x + f.w - 4, f.y + 5, 3.4, "#3ddc84", "right");
            calls.slice(0, 3).forEach((c, i) => {
              const lv = c.lv || 1;
              circle(ctx, f.x + 5, f.y + 11.5 + i * 5.2, 1.3, LV[lv]);
              const who = lv === 3 ? T("L3 manager", "L3 vedúci") : lv === 2 ? T("L2 foreman", "L2 majster") : c.role;
              A.text(ctx, `${c.st} · ${who} · ${c.ph}`, f.x + 9, f.y + 11.5 + i * 5.2, 3.3, "#d7e0ee", "left");
            });
          }
        } else if (calls.length) circle(ctx, f.x + f.w - 6, f.y + 6, 2.4, LV[Math.max(...calls.map((c) => c.lv || 1))]);
        break;
      }
      default: rect(ctx, f.x, f.y, f.w, f.h, "#888");
    }
  }

  function drawStaff(ctx, L, floor, k, lang, t) {
    const b = L.buildings.find((x) => x.id === "STAFF");
    if (!b) return;
    fillRR(ctx, b.x, b.y, b.w, b.h, 6, P.building, P.buildingEdge, 1);
    const ls = Math.max(5.5, Math.min(11, 9 / k));
    for (const r of L.rooms) {
      rect(ctx, r.x + 1, r.y + 1, r.w - 2, r.h - 2, P.dark ? "rgba(255,255,255,.035)" : "rgba(0,0,0,.035)");
      line(ctx, r.x, r.y, r.x, r.y + r.h, P.buildingEdge, 0.6);
      rect(ctx, r.door[0] - 7, r.y - 0.8, 14, 1.6, P.dark ? "#0e1520" : "#e8ecf1");               // the doorway
      A.door(ctx, r.door[0], r.y, 14, 1, k);                                                         // and its door
      if (r.entrance) {                                                                              // from the street
        rect(ctx, r.entrance[0] - 8, r.y + r.h - 0.8, 16, 1.8, P.dark ? "#0e1520" : "#e8ecf1");
        A.door(ctx, r.entrance[0], r.y + r.h, 16, -1, k);
        if (k > 0.9) A.text(ctx, "▲ " + T("ENTRANCE", "VCHOD"), r.entrance[0], r.y + r.h + 6, 4.6, P.textDim, "center", "700");
      }
    }
    drawCorner(ctx, L, k, lang);
    if (k > 0.55) {
      for (const r of L.rooms) {
        const y = r.label_dy !== undefined ? r.y + r.label_dy : r.y + r.h - ls * 0.9;
        A.fitText(ctx, lang === "sk" ? r.name_sk : r.name, r.x + r.w / 2, y, ls, r.w - 6, P.textDim, "center", "600");
      }
    }
    for (const r of L.rooms) wcSign(ctx, r, k);
    for (const f of L.furniture) furniture(ctx, f, floor, k, t, lang);
    labelThings(ctx, L, k, lang);
  }

  /* the screen with the head count of the shifts, in the strip above the halls: one column a shift.
     Everything on it is in plan units, so it grows and shrinks with the plan like a real screen. From far
     away the text would be too small to read: it fades into a bar a shift (how much of its roster is here) */
  function drawBoard(ctx, L, floor, k, lang) {
    const bd = L.board;
    if (!bd || !bd.w) return;
    const crew = floor && floor.crew;
    const u = bd.h / 64;                                                                         // the text grows with the screen
    fillRR(ctx, bd.x + 2, bd.y + 2.5, bd.w, bd.h, 4, "rgba(0,0,0,.18)", null);                  // its shadow on the floor
    fillRR(ctx, bd.x, bd.y, bd.w, bd.h, 4, "#0d131c", "#3a4659", 1);                             // the bezel
    rect(ctx, bd.x + 3, bd.y + 3, bd.w - 6, bd.h - 6, "#121b28");
    const tw = 140 * u, cw = (bd.w - tw - 8) / 3, pad = 10 * u;
    const near = Math.min(1, Math.max(0, (k - 0.36) / 0.1));                                    // 0 far away … 1 readable
    const y1 = bd.y + bd.h * 0.25, y2 = bd.y + bd.h * 0.53, y3 = bd.y + bd.h * 0.79;
    ctx.save();
    if (near > 0) {
      ctx.globalAlpha = near;
      text(ctx, T("PEOPLE", "ĽUDIA"), bd.x + pad, y1, 11 * u, "#9fb0c8", "left", "700");
      text(ctx, T("ON SITE", "V ZÁVODE"), bd.x + pad, y2, 11 * u, "#9fb0c8", "left", "700");
      if (crew && crew.now) text(ctx, `${T("now shift", "teraz zmena")} ${crew.now}`, bd.x + pad, y3, 8 * u, "#6f8098", "left");
    }
    if (crew) ["A", "B", "C"].forEach((code, i) => {
      const s = crew.shifts[code];
      if (!s) return;
      const x = bd.x + tw + i * cw, w = cw - 2 * pad, now = crew.now === code;
      ctx.globalAlpha = 1;
      line(ctx, x, bd.y + 8 * u, x, bd.y + bd.h - 8 * u, "#26303f", 0.8 * u);
      if (now) circle(ctx, x + cw - pad - 2 * u, y1, 2.6 * u, "#3ddc84");
      if (near < 1) {                                                                            // far away: the bar
        ctx.globalAlpha = 1 - near;
        const share = s.roster ? Math.min(1, s.on_site / s.roster) : 0;
        rect(ctx, x + pad, y2 - 8 * u, w, 16 * u, "#1b2533");
        if (share) rect(ctx, x + pad, y2 - 8 * u, w * share, 16 * u, now ? "#2fbf71" : "#8fa3bf");
      }
      if (!near) return;
      ctx.globalAlpha = near;
      A.fitText(ctx, `${T("Shift", "Zmena")} ${code} · ${s.start}–${s.end}`, x + pad, y1, 9.5 * u, w - 8 * u,
        now ? "#ffffff" : "#8191a8", "left", now ? "700" : "400");
      const big = String(s.on_site || s.roster);
      ctx.font = `700 ${15 * u}px "Segoe UI", system-ui, sans-serif`;
      const nw = ctx.measureText(big).width + 4 * u;
      text(ctx, big, x + pad, y2, 15 * u, s.on_site ? (now ? "#3ddc84" : "#c9d6e8") : "#4d5a6d", "left", "700");
      A.fitText(ctx, s.on_site ? T("on site", "v závode") : T("rostered", "v zozname"), x + pad + nw, y2 + u, 9 * u, w - nw,
        s.on_site ? (now ? "#3ddc84" : "#8fa3bf") : "#6f7f96", "left", "600");
      A.fitText(ctx, s.on_site
        ? `${s.posts} ${T("at posts", "na miestach")} · ${s.break} ${T("on break", "na prestávke")} · ${s.walking} ${T("walking", "idú")}`
        : T("not here", "nie sú tu"), x + pad, y3, 8 * u, w, s.on_site ? "#9fb0c8" : "#6f7f96", "left");
    });
    ctx.restore();
  }

  function drawWalkways(ctx, L, k) {
    if (k < 0.5) return;
    ctx.save();
    ctx.strokeStyle = P.dark ? "rgba(240,200,80,.22)" : "rgba(160,120,0,.28)";
    ctx.lineWidth = 1.1; ctx.setLineDash([4, 4]);
    ctx.beginPath();
    for (const [[x1, y1], [x2, y2]] of L.walkways) { ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); }
    ctx.stroke();
    ctx.restore();
  }

  // ---------------------------------------------------------------- the dock and the trucks
  /* a pallet on the trailer: its real footprint, boxes standing on it, long side along the trailer */
  function palletOnTruck(ctx, pl, x, y, k) {
    const prod = Floor.layout.products[pl.p];
    A.drawItem(ctx, { x, y, alpha: 1, desc: { k: "L", p: pl.p, n: pl.n, c: prod ? prod.klt_per_pallet : pl.n } }, k, { rot: Math.PI / 2 });
  }

  function drawTruck(ctx, truck, pose, k, lang) {
    const bay = Floor.layout.bays.find((b) => b.bay === truck.bay);
    const color = CUSTOMER[truck.c] || "#6b7686";
    const W = bay.trailer_w, Lt = bay.trailer_l, Cl = bay.cab_l;
    ctx.save();
    ctx.translate(pose.x, pose.y);
    // trailer without its roof: the load can be seen
    fillRR(ctx, -W / 2, 0, W, Lt, 2, P.dark ? "#2a3548" : "#d9dfe8", P.dark ? "#55667f" : "#8795ab", 0.8);
    fillRR(ctx, -W / 2 + 2, 2, W - 4, Lt - 4, 1, P.dark ? "#1b2433" : "#eef1f6", null);
    rect(ctx, -W / 2, 0, 2.2, Lt, color); rect(ctx, W / 2 - 2.2, 0, 2.2, Lt, color);
    let rowY = 3;
    for (let i = 0; i < truck.load.length; i += 2) {
      const pair = truck.load.slice(i, i + 2);
      const sz = pair.map((pl) => A.sizes(pl.p).pallet);
      const along = Math.max(...sz.map((z) => z[0]));                 // 1200 mm side, along the trailer
      pair.forEach((pl, j) => {
        const across = sz[j][1];                                        // 800 / 1000 mm side
        palletOnTruck(ctx, pl, (j ? 1 : -1) * (across / 2 + 0.7), rowY + along / 2, k);
      });
      rowY += along + 1.4;
    }
    // cab
    fillRR(ctx, -W / 2 + 4, Lt + 1.5, W - 8, Cl - 3, 3, color, "rgba(0,0,0,.4)", 0.6);
    fillRR(ctx, -W / 2 + 7, Lt + Cl - 12, W - 14, 6, 1.5, "#bfd9f2", null);
    circle(ctx, -W / 2 + 8, Lt + Cl - 2, 1.3, "#f6e27a"); circle(ctx, W / 2 - 8, Lt + Cl - 2, 1.3, "#f6e27a");
    ctx.restore();
    if (k > 1.4) {
      A.text(ctx, truck.plate, pose.x, pose.y + Lt + Cl + 7, 4, P.text, "center", "600");
      A.text(ctx, `${truck.load.length}/${truck.cap}`, pose.x, pose.y + Lt + Cl + 13, 3.6, P.textDim, "center");
    }
  }

  function drawDock(ctx, L, floor, k, lang, view) {
    const fgw = L.buildings.find((b) => b.id === "FGW");
    if (!fgw || !L.bays.length) return;
    const top = L.bays[0].rear_y - 4, h = L.world.h - top;
    rect(ctx, fgw.x - 2, top, fgw.w + 4, h, P.dark ? "#161c27" : "#cdd4de");                       // the yard
    ctx.strokeStyle = P.dark ? "rgba(255,255,255,.18)" : "rgba(255,255,255,.7)"; ctx.lineWidth = 0.8; ctx.setLineDash([5, 4]);
    ctx.beginPath();
    for (const bay of L.bays) { ctx.moveTo(bay.x - 20, top + 2); ctx.lineTo(bay.x - 20, L.world.h - 4); }
    ctx.moveTo(L.bays[L.bays.length - 1].x + 20, top + 2); ctx.lineTo(L.bays[L.bays.length - 1].x + 20, L.world.h - 4);
    ctx.stroke(); ctx.setLineDash([]);
    if (k > 0.7) {
      for (const bay of L.bays) A.text(ctx, `${bay.bay + 1} · ${bay.customer}`, bay.x, L.world.h - 8, Math.max(4.4, 7 / k), P.textDim, "center", "600");
    }
    for (const truck of Floor.trucksNow()) {
      const pose = Floor.truckPose(truck);
      if (pose && view.seen({ x: pose.x - 20, y: pose.y, w: 40, h: 150 })) drawTruck(ctx, truck, pose, k, lang);
    }
  }

  return { drawPerson, drawLabels, drawDesks, drawStaff, drawBoard, drawWalkways, drawDock, monitorTop, chair, look, portrait,
    thingAt, thingTip, drawStore, drawReceiving, hidden, ROLE, ACT, ESD, CUSTOMER };
})();
