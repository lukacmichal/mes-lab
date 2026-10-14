/* The machines, seen from above. A machine only moves when it is really working on a
   piece: the tools follow the progress `p` of the cycle the plant reports, and a
   machine that is empty, waiting, broken or being changed over stands still.

   Drawing is split in two passes: `base` (body, belt, mechanism under the piece) and
   `top` (heads, covers and signs above the piece). Local coordinates: origin in the
   top-left corner of the machine, the belt runs along y = h / 2. */
const FloorMachines = (() => {
  const A = FloorArt;
  const { P, rr, fillRR, rect, line, circle, text, person } = A;
  const tri = (u) => { u -= Math.floor(u); return u < 0.5 ? u * 2 : 2 - u * 2; };   // 0..1..0
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, f) => a + (b - a) * f;
  const seg = (p, a, b) => clamp((p - a) / (b - a));                                 // 0..1 within [a,b]

  // ---------------------------------------------------------------- common body
  function body(ctx, w, h, a) {
    const color = P.state[a.state] || P.state.UNKNOWN;
    if (a.k < 0.75) {                       // far away: a block, tinted by its state
      rect(ctx, 1.4, 2, w, h, P.shadow);
      rect(ctx, 0, 0, w, h, P.body);
      ctx.globalAlpha = a.state === "NO_ORDER" ? 0.12 : 0.34;
      rect(ctx, 1.5, 1.5, w - 3, h - 3, color);
      ctx.globalAlpha = 1;
      rect(ctx, 0, h / 2 - 2.8, w, 5.6, P.belt);
      rect(ctx, 0, 0, w, 4.6, color);
      return;
    }
    rect(ctx, 1.4, 2, w, h, P.shadow);
    fillRR(ctx, 0, 0, w, h, 3.5, P.body, P.bodyEdge, 0.8);
    fillRR(ctx, 2, 3.4, w - 4, h - 5.4, 2.4, P.deck);
    rect(ctx, 0, h / 2 - 3.1, w, 6.2, P.belt);
    rect(ctx, 3, 0.9, w - 6, 1.7, color);
  }

  function cover(ctx, w, h, a) {
    if (a.k < 0.75) return;
    fillRR(ctx, 2, 3.4, w - 4, h - 5.4, 2.4, P.glass, P.glassEdge, 0.4);
    circle(ctx, w - 4.2, 5.8, 1.5, P.state[a.state] || P.state.UNKNOWN, "rgba(0,0,0,.35)", 0.3);
    if (a.state === "DOWN") {
      ctx.fillStyle = "rgba(220,61,51," + (0.12 + 0.12 * (Math.sin(a.t * 6) > 0 ? 1 : 0)) + ")";
      rr(ctx, 0, 0, w, h, 3.5); ctx.fill();
    } else if (a.state === "CHANGEOVER") {
      ctx.fillStyle = "rgba(47,115,224,.14)"; rr(ctx, 0, 0, w, h, 3.5); ctx.fill();
    } else if (a.state === "MAINTENANCE") {
      ctx.fillStyle = "rgba(138,111,224,.15)"; rr(ctx, 0, 0, w, h, 3.5); ctx.fill();
    } else if (a.state === "NO_ORDER") {
      ctx.fillStyle = P.dark ? "rgba(8,12,18,.38)" : "rgba(235,238,243,.5)"; rr(ctx, 0, 0, w, h, 3.5); ctx.fill();
    }
  }

  const working = (a) => a.phase === "work";
  const live = (a) => a.state === "RUNNING";
  const hasPiece = (a) => !!(a.info && (a.info.it || (a.info.out && a.info.out.length)));

  // ---------------------------------------------------------------- tools
  function head(ctx, x, y, w = 5, h = 4, fill = "#8794a8") {
    fillRR(ctx, x - w / 2, y - h / 2, w, h, 0.8, fill, "rgba(0,0,0,.3)", 0.3);
  }

  function gantry(ctx, w, h, y, a, color = P.rail) {
    rect(ctx, 3, y - 0.8, w - 6, 1.6, color);
    rect(ctx, 3, y - 2, 1.6, 4, color); rect(ctx, w - 4.6, y - 2, 1.6, 4, color);
  }

  function feeders(ctx, w, y0, n = 6) {
    const cw = (w - 12) / n;
    for (let i = 0; i < n; i++) {
      rect(ctx, 6 + i * cw + 0.5, y0, cw - 1, 3.2, "#4b5870");
      circle(ctx, 6 + i * cw + cw / 2, y0 + 1.6, 1.0, "#d9a441");
    }
  }

  function laser(ctx, w, h, a, beamLen = 9) {
    const on = working(a);
    const x = on ? w / 2 + Math.sin(a.p * Math.PI * 6) * (w * 0.2) : w / 2;
    rect(ctx, 4, 7.5, w - 8, 1.2, P.rail);
    head(ctx, x, 8, 6, 4, "#8794a8");
    if (on && live(a)) {
      line(ctx, x, 10, x, h / 2, "rgba(255,70,70,.9)", 0.7);
      circle(ctx, x, h / 2, 1.2, "#ffd34d");
    }
  }

  function camera(ctx, w, h, a, color = "rgba(150,200,255,.35)") {
    const on = working(a);
    const u = on ? a.p * 3 : 0;
    const x = on ? lerp(w * 0.3, w * 0.7, tri(u)) : w / 2;
    const y = on ? lerp(9, 12, tri(u * 2.5)) : 9;
    rect(ctx, 4, 8.6, w - 8, 1.1, P.rail);
    head(ctx, x, y, 6, 4.2, "#7b889c");
    circle(ctx, x, y, 1.1, "#1b2733", "#9cc8ff", 0.3);
    if (on && live(a)) {
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(x - 1, y + 2); ctx.lineTo(x + 1, y + 2); ctx.lineTo(x + 5, h / 2 + 3); ctx.lineTo(x - 5, h / 2 + 3); ctx.fill();
    }
  }

  const verifying = () => {};   // the operator who checks is a real person (floor-people.js)

  // ---------------------------------------------------------------- the machine types
  const T = {};

  T.LOADER = {
    base(ctx, w, h, a) {
      const racks = a.extra.panels;
      fillRR(ctx, 4, 5, 10, h - 10, 1, "#4b5870");
      const lines = racks == null ? 4 : Math.ceil(racks / 10);
      for (let i = 0; i < 4; i++) rect(ctx, 5.2, 7 + i * 4.6, 7.6, 2.2, i < lines ? "#3c8a64" : "rgba(0,0,0,.25)");
    },
    top(ctx, w, h, a) {
      const push = working(a) ? tri(seg(a.p, 0, 0.6)) : 0;
      rect(ctx, 15 + push * (w - 26), h / 2 - 2, 2.2, 4, "#d4dae3");
    },
  };

  T.LASER_PCB = { base() {}, top(ctx, w, h, a) { laser(ctx, w, h, a); } };
  T.LASER_UNIT = T.LASER_PCB;

  T.PASTE_PRINTER = {
    base(ctx, w, h, a) {
      fillRR(ctx, w / 2 - 14, 6, 28, h - 12, 1, null, "#a9b4c0", 0.8);          // stencil frame
      rect(ctx, w / 2 - 12, 7.2, 24, 0.8, "#a9b4c0");
    },
    top(ctx, w, h, a) {
      const on = working(a);
      const sweep = on ? (a.p < 0.5 ? a.p * 2 : 2 - a.p * 2) : 0;
      const x = w / 2 - 11 + sweep * 22;
      rect(ctx, x - 0.6, 7, 1.2, h - 14, "#5f6b7a");
      rect(ctx, x - 1.6, 6.4, 3.2, 1.8, "#c8ced6");
    },
  };

  T.SPI = { base() {}, top(ctx, w, h, a) { camera(ctx, w, h, a); verifying(ctx, w, h, a); } };
  T.COATING_AOI = { base() {}, top(ctx, w, h, a) { camera(ctx, w, h, a, "rgba(170,110,255,.38)"); verifying(ctx, w, h, a); } };
  T.AOI = {
    base() {},
    top(ctx, w, h, a) {
      camera(ctx, w, h, a, "rgba(255,255,255,.38)");
      if (working(a) && tri(a.p * 6) > 0.8) circle(ctx, w / 2, h / 2, 5, "rgba(255,255,255,.35)");
      verifying(ctx, w, h, a);
    },
  };

  function placer(count) {
    return {
      base(ctx, w, h, a) {
        feeders(ctx, w, 4.2, 6);
        feeders(ctx, w, h - 7.6, 6);
      },
      top(ctx, w, h, a) {
        const on = working(a);
        const cycles = count;
        const u = on ? (a.p * cycles) % 1 : 0;
        // head: feeder (left) -> board -> feeder
        let x = w * 0.2, y = 8, carry = false, down = false;
        if (on) {
          const target = lerp(w * 0.35, w * 0.7, ((Math.floor(a.p * cycles) * 0.37) % 1));
          if (u < 0.4) { x = lerp(w * 0.2, target, seg(u, 0, 0.4)); y = lerp(8, h / 2, seg(u, 0, 0.4)); carry = true; }
          else if (u < 0.5) { x = target; y = h / 2; carry = true; down = true; }
          else { x = lerp(target, w * 0.2, seg(u, 0.5, 0.9)); y = lerp(h / 2, 8, seg(u, 0.5, 0.9)); }
        }
        gantry(ctx, w, h, y);
        head(ctx, x, y, 6, 4.4, "#8794a8");
        if (carry) rect(ctx, x - 0.8, y + 2.4, 1.6, 1.0, down ? "#ffd34d" : "#1d2128");
        if (down) circle(ctx, x, y + 3, 1.6, "rgba(255,211,77,.5)");
        verifying(ctx, w, h, a);
      },
    };
  }
  T.PNP_CHIP = placer(10);
  T.PNP_IC = placer(5);

  function tunnel(zones, label) {
    return {
      base(ctx, w, h, a) {
        const n = zones.length, zw = (w - 8) / n;
        const hot = !!(a.info && a.info.ov && a.info.ov.length) || working(a);
        zones.forEach((c, i) => {
          ctx.globalAlpha = hot ? 0.55 + 0.2 * Math.sin(a.t * 2 + i) : 0.22;
          rect(ctx, 4 + i * zw, 6, zw - 0.6, h - 12, c);
        });
        ctx.globalAlpha = 1;
        rect(ctx, 0, h / 2 - 3.1, w, 6.2, P.belt);
      },
      top(ctx, w, h, a) {
        if (a.k > 1.4) {
          const n = (a.info && a.info.ov && a.info.ov.length) || 0;
          text(ctx, `${n}`, w / 2, 11, 5, P.text, "center", "700");
        }
        if (a.info && a.info.ov && a.info.ov.length) {
          for (let i = 0; i < 3; i++) circle(ctx, 12 + i * 14, h - 4.6, 1.6, null, P.bodyEdge, 0.4);
        }
      },
    };
  }
  T.REFLOW_OVEN = tunnel(["#f3c45a", "#f0a33c", "#e9662c", "#e2492b", "#7fb2e5"]);
  T.UV_CURE = tunnel(["#8e5cf0", "#a77bff", "#8e5cf0"]);
  T.CURING_OVEN = tunnel(["#e39a46", "#d98a33", "#e39a46", "#d98a33", "#e39a46"]);

  T.UNLOADER = {
    base(ctx, w, h, a) {
      fillRR(ctx, w - 14, 5, 10, h - 10, 1, "#4b5870");
      for (let i = 0; i < 4; i++) rect(ctx, w - 12.8, 7 + i * 4.6, 7.6, 2.2, "rgba(60,138,100,.55)");
    },
    top(ctx, w, h, a) {
      const push = working(a) ? tri(seg(a.p, 0.1, 0.7)) : 0;
      rect(ctx, 8 + push * (w - 26), h / 2 - 2, 2.2, 4, "#d4dae3");
    },
  };

  T.DEPANELER = {
    base() {},
    top(ctx, w, h, a) {
      const on = working(a);
      const u = on ? a.p : 0;
      // router follows a cutting path around the boards
      const x = on ? w / 2 + Math.cos(u * Math.PI * 4) * 9 : w / 2;
      const y = on ? h / 2 + Math.sin(u * Math.PI * 4) * 5 : 9;
      head(ctx, x, y - 3, 5, 4, "#8794a8");
      circle(ctx, x, y, 1.1, on ? "#ffd34d" : "#5f6b7a");
      if (on && live(a)) for (let i = 0; i < 4; i++) circle(ctx, x + Math.cos(a.t * 20 + i * 1.6) * 3, y + Math.sin(a.t * 20 + i * 2.1) * 3, 0.5, "#c9b88f");
    },
  };

  // ---- final assembly
  function workplace() {
    return {
      base(ctx, w, h, a) {
        fillRR(ctx, 6, 5, w - 12, 5, 1, "#7d6a52");      // table
        rect(ctx, 8, 6, 5, 3, "#4b5870");                  // tray with parts
      },
      top() {},
    };
  }
  T.FA_LOADER = workplace();
  T.FINAL_INSPECTION = {
    base(ctx, w, h, a) {
      fillRR(ctx, 6, h / 2 - 7, w - 12, 14, 2, "rgba(255,255,255,.10)", "rgba(255,255,255,.22)", 0.4);   // the light table
    },
    top() {},
  };

  T.PRESS_FIT = {
    base() {},
    top(ctx, w, h, a) {
      const on = working(a);
      const down = on ? Math.min(seg(a.p, 0.2, 0.45), 1 - seg(a.p, 0.6, 0.8)) : 0;
      fillRR(ctx, w / 2 - 8, 6, 16, 6, 1, "#7b889c");
      fillRR(ctx, w / 2 - 4 + 0, 11 + down * 2.5, 8, 5, 0.8, "#5f6b7a", "rgba(0,0,0,.4)", 0.3);
      rect(ctx, w - 8, 6, 3, h - 12, "rgba(0,0,0,.25)");
      rect(ctx, w - 8, h - 6 - (h - 12) * down * 0.9, 3, (h - 12) * down * 0.9, "#2d8a4e");
    },
  };

  T.COATER = {
    base() {},
    top(ctx, w, h, a) {
      const on = working(a);
      const x = on ? lerp(w * 0.28, w * 0.72, tri(a.p * 2)) : w / 2;
      head(ctx, x, 8, 5, 4, "#8794a8");
      if (on) {
        ctx.fillStyle = "rgba(120,200,255,.5)";
        ctx.beginPath(); ctx.moveTo(x - 0.8, 10); ctx.lineTo(x + 0.8, 10); ctx.lineTo(x + 4, h / 2 + 2); ctx.lineTo(x - 4, h / 2 + 2); ctx.fill();
      }
    },
  };

  T.POTTING = {
    base() {},
    top(ctx, w, h, a) {
      const on = working(a);
      const down = on ? Math.min(seg(a.p, 0.1, 0.3), 1 - seg(a.p, 0.8, 0.95)) : 0;
      head(ctx, w / 2, 8, 7, 4.4, "#8794a8");
      rect(ctx, w / 2 - 0.5, 10, 1, 2 + down * 4, "#8e99ab");
      if (on && down > 0.6) circle(ctx, w / 2, h / 2 - 1, 1.1, "#d6903a");
      fillRR(ctx, w - 12, 6, 7, 8, 1, "#3a2c18", "#d6903a", 0.4);      // resin cartridge
    },
  };

  T.ICT = {
    base(ctx, w, h, a) { fillRR(ctx, w / 2 - 10, h / 2 - 8, 20, 16, 1, null, "rgba(210,170,40,.45)", 0.5); },
    top(ctx, w, h, a) {
      const on = working(a) || a.phase === "work";
      const close = on ? Math.min(seg(a.p, 0.1, 0.3), 1 - seg(a.p, 0.85, 0.97)) : 0;
      ctx.globalAlpha = 0.25 + 0.55 * close;
      fillRR(ctx, w / 2 - 10, h / 2 - 8, 20, 16, 1, "#7b889c", "#4b5870", 0.5);
      ctx.globalAlpha = 1;
      if (close > 0.5) for (let i = 0; i < 12; i++) circle(ctx, w / 2 - 8 + (i % 6) * 3.2, h / 2 - 3 + Math.floor(i / 6) * 6, 0.45, "#e6c34a");
      verifying(ctx, w, h, a);
    },
  };

  T.SCREWDRIVER = {
    base() {},
    top(ctx, w, h, a) {
      const on = working(a);
      const i = on ? Math.min(3, Math.floor(a.p * 4)) : 0;
      const sx = [-3.6, 3.2, 3.2, -3.6][i], sy = [-3, -3, 3, 3][i];
      const x = on ? w / 2 + sx : w / 2, y = on ? h / 2 + sy : 8;
      head(ctx, x, y - 3, 5, 4, "#8794a8");
      circle(ctx, x, y, 1.3, on ? "#d6dde6" : "#69727f");
      if (on && live(a)) line(ctx, x - 1.2, y, x + 1.2, y, "#333", 0.5 + 0.2 * Math.sin(a.t * 40));
      if (a.k > 1.5 && on) text(ctx, "1.2 Nm", w - 9, 9, 3.6, P.textDim, "right", "600");
    },
  };

  T.FLASHER = {
    base(ctx, w, h, a) { fillRR(ctx, 5, 5, 11, 6, 1, "#4b5870"); },
    top(ctx, w, h, a) {
      const on = working(a);
      circle(ctx, 8, 8, 1.1, on && live(a) && Math.sin(a.t * 14) > 0 ? "#3ddc84" : "#26503a");
      const f = on ? a.p : 0;
      rect(ctx, 6, h - 8, w - 12, 2, "rgba(0,0,0,.3)");
      rect(ctx, 6, h - 8, (w - 12) * f, 2, "#2f73e0");
      line(ctx, 12, 9, w / 2, h / 2 - 2, "#2b2f36", 0.5);
    },
  };

  T.EOL_TESTER = {
    base(ctx, w, h, a) { fillRR(ctx, 4, 4.6, w - 8, 7, 1, "#0d1520", "#4b5870", 0.4); },
    top(ctx, w, h, a) {
      const on = working(a);
      if (on) {
        ctx.beginPath();
        for (let i = 0; i <= 20; i++) {
          const x = 6 + (i / 20) * (w - 12) * a.p, y = 8 + Math.sin(i * 1.2 + (live(a) ? a.t * 8 : 0)) * 2;
          i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        }
        ctx.strokeStyle = "#3ddc84"; ctx.lineWidth = 0.6; ctx.stroke();
      } else if (a.info && a.info.lr && a.k > 1.1 && hasPiece(a)) {
        text(ctx, a.info.lr === "PASS" ? "PASS" : "FAIL", w / 2, 8, 4.4, a.info.lr === "PASS" ? "#3ddc84" : "#ff6b61", "center", "800");
      }
      rect(ctx, w / 2 - 9, h / 2 + 5, 18, 1.4, "#7b889c");
      verifying(ctx, w, h, a);
    },
  };

  T.PACK_SCAN = {
    base(ctx, w, h, a) {
      fillRR(ctx, w - 15, h - 11, 11, 8, 1, "#3f78bd", "#27508c", 0.4);          // the KLT being filled
      const fill = a.info && a.info.fill ? a.info.fill[0] / Math.max(1, a.info.fill[1]) : 0;
      for (let i = 0; i < 6; i++) rect(ctx, w - 13.6 + (i % 3) * 3.4, h - 9.6 + Math.floor(i / 3) * 3.4, 2.6, 2.6, i < Math.ceil(fill * 6) ? "#d6dde6" : "rgba(0,0,0,.25)");
    },
    top(ctx, w, h, a) {
      const on = working(a);
      head(ctx, w / 2 - 4, 8, 5, 3.4, "#8794a8");
      if (on && tri(a.p * 3) > 0.3) line(ctx, w / 2 - 4, 10, w / 2 - 4, h / 2, "rgba(255,70,70,.9)", 0.5);
      if (a.k > 1.4 && a.info && a.info.fill) text(ctx, `${a.info.fill[0]}/${a.info.fill[1]}`, w - 9.5, 7, 3.6, P.textDim, "center", "600");
    },
  };

  T.BOX_LABEL = {
    base(ctx, w, h, a) { fillRR(ctx, 5, 5, 14, 7, 1, "#4b5870"); },
    top(ctx, w, h, a) {
      const on = working(a);
      const out = on ? seg(a.p, 0.1, 0.7) : 0;
      rect(ctx, 8, 11, 8, 1.2, "#f4f6f8");
      if (on) rect(ctx, 8, 11 + out * 5, 8, 4, "#f4f6f8");
      if (on && a.p > 0.8) rect(ctx, w / 2 + 3, h / 2 - 1.5, 5, 3, "#f4f6f8");
    },
  };

  T.PALLETIZER = {
    base(ctx, w, h, a) {
      fillRR(ctx, w - 26, h - 15, 22, 13, 1, "#a47a46", "#6b4d27", 0.4);
      const fill = a.info && a.info.fill ? a.info.fill[0] / Math.max(1, a.info.fill[1]) : 0;
      for (let i = 0; i < 4; i++) {
        const on = i < Math.ceil(fill * 4);
        fillRR(ctx, w - 24.4 + (i % 2) * 10, h - 13.4 + Math.floor(i / 2) * 5.6, 9, 5, 0.6, on ? "#3f78bd" : "rgba(0,0,0,.18)", null);
      }
    },
    top(ctx, w, h, a) {
      if (a.k > 1.4 && a.info && a.info.fill) text(ctx, `${a.info.fill[0]}/${a.info.fill[1]}`, w - 15, 5.4, 3.6, P.textDim, "center", "600");
    },
  };

  T.REPAIR_BENCH = {
    base(ctx, w, h, a) {
      rect(ctx, 0, h / 2 - 3.1, w, 6.2, P.deck);
      fillRR(ctx, 4, 6, w - 8, h - 12, 2, "#7d6a52", "#54422d", 0.5);
    },
    top(ctx, w, h, a) {
      const on = working(a);
      if (on && live(a)) {
        line(ctx, w / 2 + 3, h - 9, w / 2 - 4, h / 2, P.person, 0.9);
        circle(ctx, w / 2 - 4, h / 2, 0.9, Math.sin(a.t * 10) > 0 ? "#ff7a3d" : "#ffb347");
        for (let i = 0; i < 3; i++) circle(ctx, w / 2 - 4 + Math.sin(a.t * 3 + i) * 1.2, h / 2 - 3 - ((a.t * 3 + i * 3) % 7), 0.9 + i * 0.3, "rgba(200,200,200,.35)");
      }
    },
  };

  // ---------------------------------------------------------------- one machine
  const NO_BELT = new Set(["REPAIR_BENCH"]);

  function draw(ctx, st, info, k, t, pass, extra, lang) {
    const kind = st.type;
    const a = {
      st, info, k, t, lang, extra,
      state: info ? info.s : "UNKNOWN", phase: info ? info.ph : "idle", p: info ? info.p : 0,
    };
    const type = T[kind] || { base() {}, top() {} };
    ctx.save();
    ctx.translate(st.x, st.y);
    if (pass === "base") {
      body(ctx, st.w, st.h, a);
      if (k > 1.1 && type.base) type.base(ctx, st.w, st.h, a);
    } else {
      if (k > 1.1 && type.top) type.top(ctx, st.w, st.h, a);
      cover(ctx, st.w, st.h, a);
      if (k > 0.85) {
        text(ctx, st.tag, st.w / 2, -3.2, 4.6, P.text, "center", "700");
      }
      if (k > 1.6) captions(ctx, st, a);
    }
    ctx.restore();
  }

  function captions(ctx, st, a) {
    const T2 = (en, sk) => (a.lang === "sk" ? sk : en);
    let msg = null;
    const cr = a.info && a.info.cr;
    if (cr) {
      const who = cr.k === "call" ? T2("technician", "technik") : T2("operator", "operátor");
      msg = cr.ph === "wait" ? T2(`waiting for a ${who}`, `čaká na: ${who}`)
        : cr.ph === "walk" ? T2(`${who} is on the way`, `${who} ide k stroju`)
        : cr.k === "call" ? T2("being repaired", "oprava prebieha") : T2(`${who} at the machine`, `${who} pri stroji`);
    } else if (a.state === "STARVED") msg = T2("waiting for parts", "čaká na kusy");
    else if (a.state === "BLOCKED") msg = T2("next one is full", "ďalšia plná");
    else if (a.state === "NO_ORDER") msg = T2("no order", "bez zákazky");
    else if (a.state === "CHANGEOVER") msg = T2("changeover", "prestavba");
    else if (a.state === "DOWN") msg = T2("breakdown", "porucha");
    else if (a.state === "MAINTENANCE") msg = T2("service", "údržba");
    else if (a.state === "MINOR_STOP") msg = T2("minor stop", "mikroprestoj");
    if (msg) text(ctx, msg, st.w / 2, st.h + 15, 3.8, P.textDim, "center");
    if (a.state === "RUNNING" && (a.phase === "work" || a.phase === "verify") && a.k > 2.2) {
      const bw = st.w - 8;
      rect(ctx, 4, st.h + 3.2, bw, 1.6, "rgba(127,140,160,.35)");
      rect(ctx, 4, st.h + 3.2, bw * a.p, 1.6, a.phase === "verify" ? "#2f73e0" : "#1f9d63");
    }
  }

  return { draw, working, hasPiece };
})();
