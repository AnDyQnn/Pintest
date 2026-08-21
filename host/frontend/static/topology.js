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
    const pad = 78;                                   // запас в мировых ед. под подписи
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
    const REP = 3300, DAMP = 0.85, SPRING = 0.033, CENTER = 0.0018;
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
        if (n.fixed) { p.x = CX; p.y = CY; p.vx = p.vy = 0; return; }
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
    const nodes = [{ id: "host", fixed: true }];
    agents.forEach((a) => nodes.push({ id: a.id, kind: "agent", ref: a }));
    targets.forEach((t) => nodes.push({ id: t.ip, kind: "target", ref: t }));
    const edges = [];
    agents.forEach((a) => edges.push({ a: "host", b: a.id, len: 165 }));
    targets.forEach((t) => {
      const home = t.route_agent || t.agent_id;
      if (home) edges.push({ a: home, b: t.ip, len: 88 });
      else edges.push({ a: "host", b: t.ip, len: 120 });   // «в очереди» / без агента
      if (t.exploiter_id && t.exploiter_id !== home)
        edges.push({ a: t.exploiter_id, b: t.ip, len: 95 });
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

    // засев позиций
    seed("host", CX, CY);
    agents.forEach((a, i) => {
      const ang = (Math.PI * 2 * i) / Math.max(agents.length, 1) - Math.PI / 2;
      seed(a.id, CX + 165 * Math.cos(ang), CY + 165 * Math.sin(ang));
    });
    targets.forEach((t) => {
      const home = t.route_agent || t.agent_id;
      const hp = (home && POS[home]) || POS.host;
      seed(t.ip, hp.x + (Math.random() - .5) * 60, hp.y + 40 + (Math.random() - .5) * 60);
    });
    // подчистить исчезнувшие узлы
    const alive = {}; nodes.forEach((n) => { alive[n.id] = 1; });
    Object.keys(POS).forEach((id) => { if (!alive[id]) delete POS[id]; });

    if (sig !== lastSig) { simulate(nodes, edges, 320); lastSig = sig; userAdjusted = false; }
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

    // связи агент→цель (+ exploiter) + пакеты
    targets.forEach((t) => {
      const tp = P(t.ip); if (!tp) return;
      const home = t.route_agent || t.agent_id;
      const hp = (home && P(home)) || P("host");
      const cut = t.reachable === false && t.status !== "pending";
      const scanning = home && agents.find((a) => a.id === home && a._active);
      const c = cut ? "#f85149" : (TGT_COL[t.status] || "#8b949e");
      const d = pathD(hp.x, hp.y, tp.x, tp.y, 0.06);
      const cls = "tlink" + (t.status === "pending" || !home ? " pending" : "")
        + (t.rerouted_from ? " reroute" : "") + (scanning ? " scanning" : "");
      el("path", { class: cls, d, stroke: t.rerouted_from ? "#58a6ff" : c }, root);
      if (scanning) packet(root, d, 1.1, "scan", 2.6, 0);
      if (t.exploiter_id && t.exploiter_id !== home) {
        const ep = P(t.exploiter_id);
        if (ep) { const dx = pathD(ep.x, ep.y, tp.x, tp.y, 0.12); el("path", { class: "xlink", d: dx }, root); packet(root, dx, 1.8, "cap", 2.4, 0); }
      }
    });

    // узлы целей
    targets.forEach((t) => {
      const p = P(t.ip); if (!p) return;
      const cut = t.reachable === false && t.status !== "pending";
      const c = cut ? "#f85149" : (TGT_COL[t.status] || "#8b949e");
      const g = el("g", { opacity: cut ? "0.75" : "1" }, root);
      if (t.status === "captured" && !cut) {
        const ring = el("circle", { cx: p.x, cy: p.y, fill: "none", stroke: c, "stroke-width": 2 }, g);
        el("animate", { attributeName: "r", values: "12;26", dur: "1.8s", repeatCount: "indefinite" }, ring);
        el("animate", { attributeName: "opacity", values: ".8;0", dur: "1.8s", repeatCount: "indefinite" }, ring);
      }
      el("circle", { cx: p.x, cy: p.y, r: 12, fill: "rgba(22,27,34,.96)", stroke: c,
        "stroke-width": 2.2, "stroke-dasharray": cut ? "3 3" : "",
        filter: t.status === "captured" && !cut ? "drop-shadow(0 0 5px " + c + ")" : "" }, g);
      el("text", { x: p.x, y: p.y + 4, "text-anchor": "middle", "font-size": "12" }, g).textContent =
        cut ? "⚠" : (t.status === "captured" ? "🚩" : (t.status === "pending" ? "•" : "◎"));
      if (t.rerouted_from)
        el("text", { x: p.x + 14, y: p.y - 9, "text-anchor": "middle", "font-size": "12" }, g).textContent = "↻";
    });

    // узлы агентов
    agents.forEach((a) => {
      const p = P(a.id); if (!p) return;
      const c = AG_COL[a.status] || "#8b949e";
      const g = el("g", {}, root);
      if (a._active) {
        el("circle", { cx: p.x, cy: p.y, r: 34, fill: "none", stroke: "#58a6ff", "stroke-width": 2, class: "aglow" }, g);
        el("circle", { cx: p.x, cy: p.y, r: 34, fill: "none", stroke: "#58a6ff", "stroke-width": 1, "stroke-dasharray": "4 6" }, g)
          .appendChild(el("animateTransform", { attributeName: "transform", type: "rotate", from: `0 ${p.x} ${p.y}`, to: `360 ${p.x} ${p.y}`, dur: "6s", repeatCount: "indefinite" }));
      }
      el("circle", { cx: p.x, cy: p.y, r: 25, fill: "rgba(22,27,34,.95)", stroke: c,
        "stroke-width": 3, filter: a.status === "online" ? "drop-shadow(0 0 6px " + c + ")" : "" }, g);
      if ((a.roles || []).includes && (a.roles || []).includes("exploiter"))
        el("circle", { cx: p.x + 18, cy: p.y - 18, r: 6, fill: "#a371f7", stroke: "#161b22", "stroke-width": 2 }, g);
      el("text", { x: p.x, y: p.y + 6, "text-anchor": "middle", "font-size": "17" }, g).textContent = "🖥";
    });

    // host
    const hp = P("host"), gh = el("g", {}, root);
    el("circle", { cx: hp.x, cy: hp.y, r: 40, fill: "url(#hostgrad)", stroke: "#58a6ff",
      "stroke-width": 3, filter: "drop-shadow(0 0 12px rgba(88,166,255,.55))" }, gh);
    el("text", { x: hp.x, y: hp.y + 6, "text-anchor": "middle", class: "node-label", "font-size": "16" }, gh).textContent = "◈ HOST";

    // ── подписи (жадное разведение в экранных координатах) ──
    const placed = [];
    const gapX = 76 / VIEW.k, gapY = 24 / VIEW.k;     // требуемый зазор в мировых ед. (учёт зума)
    function tryLabel(x, y, main, sub, cls) {
      for (const q of placed) if (Math.abs(x - q.x) < gapX && Math.abs(y - q.y) < gapY) return false;
      placed.push({ x, y });
      const g = el("g", {}, root);
      el("text", { x, y, "text-anchor": "middle", class: "node-label " + (cls || ""), "font-size": "11" }, g).textContent = main;
      if (sub) el("text", { x, y: y + 12, "text-anchor": "middle", class: "node-sub" }, g).textContent = sub;
      return true;
    }
    // host + агенты — всегда
    tryLabel(hp.x, hp.y + 56, "control plane", data.host_ip || "10.9.0.1");
    agents.forEach((a) => {
      const p = P(a.id); if (!p) return;
      placed.length = placed.length;   // агентские подписи не подавляем целями — форсим
      const g = el("g", {}, root);
      el("text", { x: p.x, y: p.y + 41, "text-anchor": "middle", class: "node-label" }, g).textContent = a.name;
      el("text", { x: p.x, y: p.y + 55, "text-anchor": "middle", class: "node-sub" }, g).textContent =
        (a.tunnel_ip || "") + " · " + (a._active ? "скан…" : (AG_STATUS_RU[a.status] || a.status));
      placed.push({ x: p.x, y: p.y + 41 });
    });
    // цели — по приоритету, с разведением
    let shown = 0;
    targets.slice().sort((u, v) => (PRIO[u.status] || 5) - (PRIO[v.status] || 5)).forEach((t) => {
      const p = P(t.ip); if (!p) return;
      const cut = t.reachable === false && t.status !== "pending";
      if (tryLabel(p.x, p.y + 28, t.ip, cut ? "недостижима" : tgtSub(t))) shown++;
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
