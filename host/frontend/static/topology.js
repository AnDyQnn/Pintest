// topology.js — ЖИВАЯ силовая (force-directed) топология сети.
//   HOST (центр, закреплён) → АГЕНТЫ → ЦЕЛИ (под агентом, что держит маршрут).
// Узлы сами расталкиваются пружинно-кулоновской моделью → граф строится «красиво»
// и масштабируется на много устройств/сетей. Подписи РАЗВОДЯТСЯ (жадно, в экранных
// координатах): при плотном графе показываются только не-налезающие; при зуме — больше.
// Управление холстом: колесо — масштаб (к курсору), тяни — двигать, 2×клик — сброс.
// Позиции и вид сохраняются между живыми перерисовками (раз в 1.5с); перелейаут —
// только когда меняется состав узлов/связей.
(function () {
  const SVG = "http://www.w3.org/2000/svg";
  const CX = 500, CY = 350;                 // центр (host), совпадает с центром viewBox 1000×700

  const AG_STATUS_RU = { online: "на связи", provisioning: "настройка",
    lost: "потерян", destroyed: "уничтожен", failed: "ошибка" };
  const AG_COL = { online: "#3fb950", provisioning: "#d29922", lost: "#f85149",
    destroyed: "#3a3a3a", failed: "#f85149" };
  const TGT_COL = { captured: "#a371f7", exploitable: "#f0883e", vulnerable: "#d29922",
    discovered: "#3fb950", pending: "#6e7681" };
  const PRIO = { captured: 0, exploitable: 1, vulnerable: 2, discovered: 3, pending: 4 };

  // ── векторные иконки (24×24, Feather-подобные) — вместо эмодзи, чёткие и контрастные ──
  const ICONS = {
    server: "M4 2h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z M4 14h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2z M6 6h.01 M6 18h.01",
    cpu: "M5 5h14v14H5z M9 1v3 M15 1v3 M9 20v3 M15 20v3 M20 9h3 M20 14h3 M1 9h3 M1 14h3 M9 9h6v6H9z",
    relay: "M16 3h5v5 M21 3l-7 7 M8 21H3v-5 M3 21l7-7 M21 21l-6-6 M3 3l6 6",
    flag: "M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z M4 22V15",
    alert: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z M12 9v4 M12 17h.01",
    crosshair: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z M22 12h-4 M6 12H2 M12 6V2 M12 22v-4",
    check: "M20 6 9 17l-5-5",
    wifi: "M5 12.6a11 11 0 0 1 14 0 M1.4 9a16 16 0 0 1 21.2 0 M8.5 16.1a6 6 0 0 1 7 0 M12 20h.01",
    pulse: "M22 12h-4l-3 9L9 3l-3 9H2",
    cross: "M18 6 6 18 M6 6l12 12",
    dot: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  };
  function drawIcon(g, name, x, y, color, size) {
    const p = ICONS[name]; if (!p) return;
    const s = (size || 16) / 24;
    el("path", { d: p, fill: "none", stroke: color, "stroke-width": 2.3,
      "stroke-linecap": "round", "stroke-linejoin": "round",
      transform: `translate(${x},${y}) scale(${s}) translate(-12,-12)` }, g);
  }
  // иконка цели по статусу/стадии
  function tgtIcon(t, cut, probing, alive) {
    if (cut) return "cross";
    if (probing) return "wifi";
    if (alive) return "pulse";
    return { captured: "flag", exploitable: "crosshair", vulnerable: "alert",
      discovered: "check", pending: "dot" }[t.status] || "dot";
  }
  // расходящееся кольцо (пинг/захват), вращающееся штрих-кольцо (живой/реле), бейдж-метка
  function expandRing(g, p, color, dur) {
    const ring = el("circle", { cx: p.x, cy: p.y, fill: "none", stroke: color, "stroke-width": 2 }, g);
    el("animate", { attributeName: "r", values: "12;26", dur, repeatCount: "indefinite" }, ring);
    el("animate", { attributeName: "opacity", values: ".85;0", dur, repeatCount: "indefinite" }, ring);
  }
  function rotRing(g, p, r, color, dur) {
    el("circle", { cx: p.x, cy: p.y, r, fill: "none", stroke: color, "stroke-width": 1.5, "stroke-dasharray": "4 5" }, g)
      .appendChild(el("animateTransform", { attributeName: "transform", type: "rotate",
        from: `0 ${p.x} ${p.y}`, to: `360 ${p.x} ${p.y}`, dur, repeatCount: "indefinite" }));
  }
  function badge(g, x, y, text, color) {
    el("circle", { cx: x, cy: y, r: 7.5, fill: "#161b22", stroke: color, "stroke-width": 1.5 }, g);
    el("text", { x, y: y + 3.5, "text-anchor": "middle", "font-size": "10", fill: color }, g).textContent = text;
  }

  // ── персистентное состояние ──
  const POS = {};                            // id -> {x,y,vx,vy}
  const VIEW = { k: 1, tx: 0, ty: 0 };       // масштаб/сдвиг холста
  const VB_W = 1000, VB_H = 700;             // размер viewBox
  let lastSig = "";                          // сигнатура состава графа
  let lastSvg = null, lastData = null;       // для перерисовки на зум
  let rerenderTimer = null;
  let userAdjusted = false;                  // трогал ли пользователь вид (тогда не авто-фитим)

  function el(name, attrs, parent) {
    const e = document.createElementNS(SVG, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function pathD(x1, y1, x2, y2, bend) {
    const mx = (x1 + x2) / 2 + (y2 - y1) * (bend || 0);
    const my = (y1 + y2) / 2 - (x2 - x1) * (bend || 0);
    return `M${x1.toFixed(1)},${y1.toFixed(1)} Q${mx.toFixed(1)},${my.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`;
  }
  function packet(p, d, dur, cls, r, begin) {
    const c = el("circle", { r: r || 3, class: "pkt " + (cls || "") }, p);
    el("animateMotion", { dur: dur + "s", repeatCount: "indefinite", path: d,
      rotate: "auto", begin: (begin || 0) + "s" }, c);
    return c;
  }
  function applyView(vp) {
    vp.setAttribute("transform", `translate(${VIEW.tx.toFixed(2)},${VIEW.ty.toFixed(2)}) scale(${VIEW.k.toFixed(4)})`);
  }
  // авто-подгонка вида под контент (чтобы граф заполнял карту и подписи были читаемы)
  function fitView(nodes) {
    let minx = 1e9, miny = 1e9, maxx = -1e9, maxy = -1e9;
    nodes.forEach((n) => { const p = POS[n.id]; if (!p) return;
      minx = Math.min(minx, p.x); miny = Math.min(miny, p.y);
      maxx = Math.max(maxx, p.x); maxy = Math.max(maxy, p.y); });
    if (minx > maxx) return;
    const pad = 66;                                   // запас в мировых ед. под подписи
    const w = (maxx - minx) + pad * 2, h = (maxy - miny) + pad * 2;
    VIEW.k = Math.min(VB_W / w, VB_H / h, 2.6);        // маленький граф не раздуваем сверх 2.6×
    VIEW.tx = VB_W / 2 - VIEW.k * (minx + maxx) / 2;
    VIEW.ty = VB_H / 2 - VIEW.k * (miny + maxy) / 2;
  }

  function tgtSub(t) {
    if (t.status === "captured") return t.flag ? "🏳 флаг" : "root";
    if (t.status === "exploitable") return "CVE×" + (t.cve_count || 0) + " ⚑" + (t.exploit_count || 1);
    if (t.status === "vulnerable") return "CVE×" + (t.cve_count || 0);
    if (t.status === "discovered") return "чисто";
    return "ждёт";
  }
  function activeAgents(data) {
    const s = {};
    (data.jobs || []).forEach((j) => (j.chunks || []).forEach((c) => {
      if (c.status === "assigned" && c.agent_id) s[c.agent_id] = true;
    }));
    return s;
  }
  function subnet(ip) { const m = /^(\d+\.\d+\.\d+)\./.exec(ip || ""); return m ? m[1] : ""; }

  // ── силовая раскладка ──────────────────────────────────────────────────────
  function seed(id, x, y) { if (!POS[id]) POS[id] = { x, y, vx: 0, vy: 0 }; }

  function simulate(nodes, edges, iters) {
    const REP = 6200, DAMP = 0.86, SPRING = 0.026, CENTER = 0.0011;
    for (let it = 0; it < iters; it++) {
      // отталкивание всех пар
      for (let i = 0; i < nodes.length; i++) {
        const a = POS[nodes[i].id];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = POS[nodes[j].id];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy; if (d2 < 1) { d2 = 1; dx = Math.random() - .5; dy = Math.random() - .5; }
          const d = Math.sqrt(d2), f = REP / d2;
          const fx = (dx / d) * f, fy = (dy / d) * f;
          a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
        }
      }
      // пружины по связям
      edges.forEach((e) => {
        const a = POS[e.a], b = POS[e.b]; if (!a || !b) return;
        let dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - e.len) * SPRING;
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      });
      // интеграция + лёгкое притяжение к центру + фиксация host
      nodes.forEach((n) => {
        const p = POS[n.id];
        if (n.fixed) { p.x = n.fx != null ? n.fx : CX; p.y = n.fy != null ? n.fy : CY; p.vx = p.vy = 0; return; }
        p.vx += (CX - p.x) * CENTER; p.vy += (CY - p.y) * CENTER;
        p.vx *= DAMP; p.vy *= DAMP;
        p.x += Math.max(-40, Math.min(40, p.vx));
        p.y += Math.max(-40, Math.min(40, p.vy));
      });
    }
  }

  function buildGraph(data) {
    const agents = data.agents || [];
    const targets = (data.topology && data.topology.targets) || [];
    // HOST и агенты — ЗАКРЕПЛЕНЫ (host в центре, агенты ровно по кольцу) → не слипаются.
    const N = Math.max(agents.length, 1), RA = 235;
    const nodes = [{ id: "host", fixed: true, fx: CX, fy: CY }];
    agents.forEach((a, i) => {
      const ang = (Math.PI * 2 * i) / N - Math.PI / 2;
      nodes.push({ id: a.id, kind: "agent", ref: a, fixed: true,
        fx: CX + RA * Math.cos(ang), fy: CY + RA * Math.sin(ang) });
    });
    targets.forEach((t) => nodes.push({ id: t.ip, kind: "target", ref: t }));
    // рёбра-пружины ТОЛЬКО к целям (агенты фиксированы, host→агент рисуется отдельно)
    const edges = [];
    targets.forEach((t) => {
      const home = t.route_agent || t.agent_id;
      if (home) edges.push({ a: home, b: t.ip, len: 128 });
      else edges.push({ a: "host", b: t.ip, len: 150 });   // «в очереди» / без агента
      if (t.exploiter_id && t.exploiter_id !== home)
        edges.push({ a: t.exploiter_id, b: t.ip, len: 110 });
    });
    return { agents, targets, nodes, edges };
  }

  // ── pan/zoom + перерисовка на зум (чтобы подписи разъезжались/появлялись) ──
  function ensureInteractions(svg) {
    if (svg._panzoom) return;
    svg._panzoom = true;
    svg.style.cursor = "grab";
    const toSvg = (evt) => {
      const m = svg.getScreenCTM(); if (!m) return null;
      const pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
      return pt.matrixTransform(m.inverse());
    };
    const vp = () => svg.querySelector("#vp");
    const scheduleRerender = () => {
      if (rerenderTimer) clearTimeout(rerenderTimer);
      rerenderTimer = setTimeout(() => { if (lastSvg && lastData) window.renderTopology(lastSvg, lastData); }, 130);
    };
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const p = toSvg(e); if (!p) return;
      const wx = (p.x - VIEW.tx) / VIEW.k, wy = (p.y - VIEW.ty) / VIEW.k;
      VIEW.k = Math.min(8, Math.max(0.12, VIEW.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      VIEW.tx = p.x - VIEW.k * wx; VIEW.ty = p.y - VIEW.k * wy;
      userAdjusted = true;
      const g = vp(); if (g) applyView(g);
      scheduleRerender();
    }, { passive: false });
    let drag = false, ox = 0, oy = 0;
    svg.addEventListener("mousedown", (e) => {
      const p = toSvg(e); if (!p) return; drag = true; ox = p.x - VIEW.tx; oy = p.y - VIEW.ty; svg.style.cursor = "grabbing";
    });
    window.addEventListener("mousemove", (e) => {
      if (!drag) return; const p = toSvg(e); if (!p) return;
      VIEW.tx = p.x - ox; VIEW.ty = p.y - oy; userAdjusted = true; const g = vp(); if (g) applyView(g);
    });
    window.addEventListener("mouseup", () => { drag = false; svg.style.cursor = "grab"; });

    // ── тач: пан одним пальцем + пинч-зум (телефон). Тап не трогаем — он даёт click (меню узла). ──
    let tdrag = false, tox = 0, toy = 0, pinchD = 0;
    const _dist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    const _mid = (a, b) => ({ clientX: (a.clientX + b.clientX) / 2, clientY: (a.clientY + b.clientY) / 2 });
    svg.addEventListener("touchstart", (e) => {
      if (e.touches.length === 1) {
        const p = toSvg(e.touches[0]); if (!p) return;
        tdrag = true; tox = p.x - VIEW.tx; toy = p.y - VIEW.ty;
      } else if (e.touches.length === 2) {
        tdrag = false; pinchD = _dist(e.touches[0], e.touches[1]);
      }
    }, { passive: true });
    svg.addEventListener("touchmove", (e) => {
      if (e.touches.length === 1 && tdrag) {
        e.preventDefault();
        const p = toSvg(e.touches[0]); if (!p) return;
        VIEW.tx = p.x - tox; VIEW.ty = p.y - toy; userAdjusted = true;
        const g = vp(); if (g) applyView(g);
      } else if (e.touches.length === 2 && pinchD) {
        e.preventDefault();
        const nd = _dist(e.touches[0], e.touches[1]);
        const p = toSvg(_mid(e.touches[0], e.touches[1])); if (!p) return;
        const wx = (p.x - VIEW.tx) / VIEW.k, wy = (p.y - VIEW.ty) / VIEW.k;
        VIEW.k = Math.min(8, Math.max(0.12, VIEW.k * (nd / pinchD)));
        VIEW.tx = p.x - VIEW.k * wx; VIEW.ty = p.y - VIEW.k * wy;
        pinchD = nd; userAdjusted = true;
        const g = vp(); if (g) applyView(g);
      }
    }, { passive: false });
    svg.addEventListener("touchend", (e) => {
      if (e.touches.length === 0) { tdrag = false; pinchD = 0; scheduleRerender(); }
    }, { passive: true });
    svg.addEventListener("dblclick", (e) => {   // двойной клик — снова вписать граф в карту
      e.preventDefault(); userAdjusted = false; scheduleRerender();
    });
  }

  window.renderTopology = function (svg, data) {
    lastSvg = svg; lastData = data;
    ensureInteractions(svg);
    const { agents, targets, nodes, edges } = buildGraph(data);
    const active = activeAgents(data);

    // сигнатура состава → перелейаут только при изменении
    const sig = nodes.map((n) => n.id).sort().join(",") + "|" + edges.length;

    // засев позиций: закреплённые (host/агенты) — жёстко на fx/fy; цели — около своего агента
    nodes.forEach((n) => {
      if (!n.fixed) return;
      POS[n.id] = POS[n.id] || { x: n.fx, y: n.fy, vx: 0, vy: 0 };
      POS[n.id].x = n.fx; POS[n.id].y = n.fy; POS[n.id].vx = POS[n.id].vy = 0;
    });
    targets.forEach((t) => {
      const home = t.route_agent || t.agent_id;
      const hp = (home && POS[home]) || POS.host;
      seed(t.ip, hp.x + (Math.random() - .5) * 80, hp.y + (Math.random() - .5) * 80 + 30);
    });
    // подчистить исчезнувшие узлы
    const alive = {}; nodes.forEach((n) => { alive[n.id] = 1; });
    Object.keys(POS).forEach((id) => { if (!alive[id]) delete POS[id]; });

    if (sig !== lastSig) { simulate(nodes, edges, 420); lastSig = sig; userAdjusted = false; }
    if (!userAdjusted) fitView(nodes);          // вписать граф в карту (пока пользователь не трогал вид)

    // ── рисуем ──
    svg.innerHTML = "";
    const defs = el("defs", {}, svg);
    const hg = el("radialGradient", { id: "hostgrad" }, defs);
    el("stop", { offset: "0%", "stop-color": "#7d5fe0" }, hg);
    el("stop", { offset: "100%", "stop-color": "#1e2a55" }, hg);
    const root = el("g", { id: "vp" }, svg);
    applyView(root);

    const P = (id) => POS[id];
    agents.forEach((a) => { a._active = !!active[a.id]; });

    // связи host→агент + пакеты
    agents.forEach((a) => {
      const online = a.status === "online";
      const h = P("host"), n = P(a.id); if (!n) return;
      const d = pathD(h.x, h.y, n.x, n.y, 0.08);
      el("path", { class: "link " + a.status + (online ? " pulse" : ""), d }, root);
      if (online) {
        packet(root, d, a._active ? 1.6 : 3.4, "", a._active ? 3.3 : 2.4, 0);
        if (a._active) packet(root, d, 1.6, "", 3.3, 0.8);
      }
    });

    // связи к цели: обычно от агента, но реле-цель — ОТ захваченного узла-реле (self-heal)
    targets.forEach((t) => {
      const tp = P(t.ip); if (!tp) return;
      const cut = t.reachable === false && t.status !== "pending";
      const relayed = t.relay && P(t.relay);
      const home = relayed ? t.relay : (t.route_agent || t.agent_id);
      const hp = (home && P(home)) || P("host");
      const scanning = !relayed && home && agents.find((a) => a.id === home && a._active);
      const c = cut ? "#f85149" : (TGT_COL[t.status] || "#8b949e");
      const d = pathD(hp.x, hp.y, tp.x, tp.y, 0.06);
      let cls = "tlink";
      if (relayed) cls += " relay";
      else if (t.rerouted_from) cls += " reroute";
      else if (t.status === "pending" || !home) cls += " pending";
      if (scanning) cls += " scanning";
      el("path", { class: cls, d, stroke: relayed ? "#2dd4bf" : (t.rerouted_from ? "#58a6ff" : c) }, root);
      if (relayed) packet(root, d, 1.5, "relay", 2.6, 0);
      else if (scanning) packet(root, d, 1.1, "scan", 2.6, 0);
      if (!relayed && t.exploiter_id && t.exploiter_id !== home) {
        const ep = P(t.exploiter_id);
        if (ep) { const dx = pathD(ep.x, ep.y, tp.x, tp.y, 0.12); el("path", { class: "xlink", d: dx }, root); packet(root, dx, 1.8, "cap", 2.4, 0); }
      }
    });

    // узлы целей: SVG-иконка по статусу/стадии + своя анимация на каждое состояние
    targets.forEach((t) => {
      const p = P(t.ip); if (!p) return;
      const cut = t.reachable === false && t.status !== "pending";
      const probing = t.stage === "probing", alive = t.stage === "alive", relay = t.is_relay;
      let c = cut ? "#f85149" : (TGT_COL[t.status] || "#8b949e");
      if (probing) c = "#58a6ff"; else if (alive) c = "#2dd4bf";     // пинг — синий, живой — бирюзовый
      const g = el("g", { opacity: cut ? "0.72" : "1" }, root);
      g.style.cursor = "pointer";
      g.addEventListener("click", (ev) => { window.onTopoNode && window.onTopoNode({
        kind: "target", ip: t.ip, status: t.status, candidates: t.candidates || [],
        exploiter_id: t.exploiter_id, hidden: t.hidden, is_relay: t.is_relay, ports: t.ports || [] }, ev); });
      // анимации — у каждого состояния свой ритм/цвет:
      if (probing) expandRing(g, p, "#58a6ff", "1s");                          // пинг — быстрая волна
      else if (t.status === "captured" && !cut) expandRing(g, p, "#a371f7", "2s"); // захват — медленная фиолетовая
      if (alive) rotRing(g, p, 18, "#2dd4bf", "2.2s");                         // живой — быстрое вращение (скан портов)
      if (relay) rotRing(g, p, 21, "#2dd4bf", "5s");                          // реле — медленное вращение (плацдарм активен)
      const rr = relay ? 15 : 12;
      el("circle", { cx: p.x, cy: p.y, r: rr, fill: "#0d1117", stroke: c,
        "stroke-width": relay ? 2.8 : 2.2, "stroke-dasharray": cut ? "3 3" : "",
        filter: (probing || alive || relay || (t.status === "captured" && !cut)) ? "drop-shadow(0 0 6px " + c + ")" : "" }, g);
      drawIcon(g, relay ? "relay" : tgtIcon(t, cut, probing, alive), p.x, p.y, c, relay ? 18 : 15);
      if (relay) badge(g, p.x + 14, p.y - 14, "⇄", "#2dd4bf");
      else if (t.rerouted_from) badge(g, p.x + 13, p.y - 13, "↻", "#58a6ff");
      if (t.hidden) badge(g, p.x - 14, p.y - 14, "H", "#8b5cf6");   // скрытый хост (за pivot'ом)
    });

    // узлы агентов
    agents.forEach((a) => {
      const p = P(a.id); if (!p) return;
      const c = AG_COL[a.status] || "#8b949e";
      const g = el("g", {}, root);
      g.style.cursor = "pointer";
      g.addEventListener("click", (ev) => { window.onTopoNode && window.onTopoNode({
        kind: "agent", id: a.id, name: a.name, status: a.status,
        roles: a.roles || [], tunnel_ip: a.tunnel_ip }, ev); });
      if (a._active) {
        el("circle", { cx: p.x, cy: p.y, r: 34, fill: "none", stroke: "#58a6ff", "stroke-width": 2, class: "aglow" }, g);
        el("circle", { cx: p.x, cy: p.y, r: 34, fill: "none", stroke: "#58a6ff", "stroke-width": 1, "stroke-dasharray": "4 6" }, g)
          .appendChild(el("animateTransform", { attributeName: "transform", type: "rotate", from: `0 ${p.x} ${p.y}`, to: `360 ${p.x} ${p.y}`, dur: "6s", repeatCount: "indefinite" }));
      }
      el("circle", { cx: p.x, cy: p.y, r: 25, fill: "#0d1117", stroke: c,
        "stroke-width": 3, filter: a.status === "online" ? "drop-shadow(0 0 7px " + c + ")" : "" }, g);
      drawIcon(g, "cpu", p.x, p.y, c, 24);                     // нода-агент — «процессор»
      if ((a.roles || []).includes && (a.roles || []).includes("exploiter"))
        badge(g, p.x + 19, p.y - 19, "⚔", "#a371f7");          // exploiter — метка
    });

    // host — «сервер»
    const hp = P("host"), gh = el("g", {}, root);
    el("circle", { cx: hp.x, cy: hp.y, r: 40, fill: "url(#hostgrad)", stroke: "#58a6ff",
      "stroke-width": 3, filter: "drop-shadow(0 0 12px rgba(88,166,255,.55))" }, gh);
    drawIcon(gh, "server", hp.x, hp.y, "#dbe9ff", 30);

    // ── подписи (жадное разведение в экранных координатах) ──
    const placed = [];
    const gapX = 90 / VIEW.k, gapY = 42 / VIEW.k;     // требуемый зазор в мировых ед. (учёт зума)
    function tryLabel(x, y, main, sub, cls) {
      for (const q of placed) if (Math.abs(x - q.x) < gapX && Math.abs(y - q.y) < gapY) return false;
      placed.push({ x, y });
      const g = el("g", {}, root);
      el("text", { x, y, "text-anchor": "middle", class: "node-label " + (cls || ""), "font-size": "11" }, g).textContent = main;
      if (sub) el("text", { x, y: y + 12, "text-anchor": "middle", class: "node-sub" }, g).textContent = sub;
      return true;
    }
    // host + агенты — всегда
    tryLabel(hp.x, hp.y + 58, "HOST", "control plane · " + (data.host_ip || "10.9.0.1"));
    agents.forEach((a) => {
      const p = P(a.id); if (!p) return;
      placed.length = placed.length;   // агентские подписи не подавляем целями — форсим
      const g = el("g", {}, root);
      el("text", { x: p.x, y: p.y + 41, "text-anchor": "middle", class: "node-label" }, g).textContent = a.name;
      el("text", { x: p.x, y: p.y + 55, "text-anchor": "middle", class: "node-sub" }, g).textContent =
        (a.tunnel_ip || "") + " · " + (a._active ? "скан…" : (AG_STATUS_RU[a.status] || a.status));
      placed.push({ x: p.x, y: p.y + 41 });
    });
    // цели — сканируемые (стадии) вперёд, дальше по приоритету статуса; с разведением подписей
    let shown = 0;
    targets.slice().sort((u, v) => ((u.stage ? -1 : 0) - (v.stage ? -1 : 0))
      || (PRIO[u.status] || 5) - (PRIO[v.status] || 5)).forEach((t) => {
      const p = P(t.ip); if (!p) return;
      const cut = t.reachable === false && t.status !== "pending";
      const sub = cut ? "недостижима"
        : t.hidden ? ("скрыт · " + ((t.ports || []).slice(0, 3).join(",") || "?"))
        : t.stage === "probing" ? "пинг…" : t.stage === "alive" ? "скан…" : tgtSub(t);
      if (tryLabel(p.x, p.y + 28, t.ip, sub)) shown++;
    });

    if (!agents.length)
      el("text", { x: CX, y: CY + 120, "text-anchor": "middle", class: "node-sub" }, root)
        .textContent = "нет нод — добавь агента во вкладке «Агенты»";

    // подсказка + счётчик (фиксированы, вне подвижного слоя)
    el("text", { x: 16, y: 26, class: "topo-hint", "font-size": "15" }, svg)
      .textContent = "колесо — масштаб · тяни — двигать · 2×клик — вписать в экран";
    if (targets.length)
      el("text", { x: 16, y: 48, class: "topo-hint", "font-size": "15" }, svg)
        .textContent = `узлов: ${agents.length} агентов · ${targets.length} целей · подписей видно ${shown}/${targets.length} (зум покажет больше)`;
  };
})();
