// topology.js — ЖИВАЯ SVG-топология в три эшелона + анимация процессов + pan/zoom.
//   центр   — ХОСТ (control plane);
//   кольцо  — агент-ноды (по статусу; активные при скане светятся);
//   внешние — ЦЕЛИ под агентом, что держит маршрут СЕЙЧАС (после рокировки — переносится).
// Холст: колесо — масштаб (к курсору), тяни мышью — двигать, двойной клик — сброс вида.
// Вид (масштаб/сдвиг) сохраняется между живыми перерисовками (раз в 1.5с).
(function () {
  const SVG = "http://www.w3.org/2000/svg";
  const CX = 500, CY = 336, R_AGENT = 190, R_TGT = 268;

  const AG_STATUS_RU = { online: "на связи", provisioning: "настройка",
    lost: "потерян", destroyed: "уничтожен", failed: "ошибка" };
  const AG_COL = { online: "#3fb950", provisioning: "#d29922", lost: "#f85149",
    destroyed: "#3a3a3a", failed: "#f85149" };

  const TGT_COL = { captured: "#a371f7", exploitable: "#f0883e", vulnerable: "#d29922",
    discovered: "#3fb950", pending: "#6e7681" };

  // ── состояние вида (переживает перерисовки) ──
  const VIEW = { k: 1, tx: 0, ty: 0 };

  function el(name, attrs, parent) {
    const e = document.createElementNS(SVG, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function pathD(x1, y1, x2, y2, bend) {
    const mx = (x1 + x2) / 2 + (y2 - y1) * (bend || 0);
    const my = (y1 + y2) / 2 - (x2 - x1) * (bend || 0);
    return `M${x1},${y1} Q${mx},${my} ${x2},${y2}`;
  }
  function curve(p, x1, y1, x2, y2, cls, bend) {
    return el("path", { class: cls, d: pathD(x1, y1, x2, y2, bend) }, p);
  }
  function packet(p, d, dur, cls, r, begin) {
    const c = el("circle", { r: r || 3, class: "pkt " + (cls || "") }, p);
    el("animateMotion", { dur: dur + "s", repeatCount: "indefinite",
      path: d, rotate: "auto", begin: (begin || 0) + "s" }, c);
    return c;
  }
  function applyView(vp) {
    vp.setAttribute("transform", `translate(${VIEW.tx.toFixed(2)},${VIEW.ty.toFixed(2)}) scale(${VIEW.k.toFixed(4)})`);
  }

  // ── pan/zoom навешиваем на <svg> один раз ──
  function ensureInteractions(svg) {
    if (svg._panzoom) return;
    svg._panzoom = true;
    svg.style.cursor = "grab";

    function toSvg(evt) {                     // client → координаты viewBox
      const m = svg.getScreenCTM();
      if (!m) return null;
      const pt = svg.createSVGPoint();
      pt.x = evt.clientX; pt.y = evt.clientY;
      return pt.matrixTransform(m.inverse());
    }
    function vp() { return svg.querySelector("#vp"); }

    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const p = toSvg(e); if (!p) return;
      const wx = (p.x - VIEW.tx) / VIEW.k, wy = (p.y - VIEW.ty) / VIEW.k;   // мировая точка под курсором
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      VIEW.k = Math.min(7, Math.max(0.2, VIEW.k * factor));
      VIEW.tx = p.x - VIEW.k * wx;            // держим точку под курсором на месте
      VIEW.ty = p.y - VIEW.k * wy;
      const g = vp(); if (g) applyView(g);
    }, { passive: false });

    let dragging = false, ox = 0, oy = 0;
    svg.addEventListener("mousedown", (e) => {
      const p = toSvg(e); if (!p) return;
      dragging = true; ox = p.x - VIEW.tx; oy = p.y - VIEW.ty;
      svg.style.cursor = "grabbing";
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const p = toSvg(e); if (!p) return;
      VIEW.tx = p.x - ox; VIEW.ty = p.y - oy;
      const g = vp(); if (g) applyView(g);
    });
    window.addEventListener("mouseup", () => { dragging = false; svg.style.cursor = "grab"; });
    svg.addEventListener("dblclick", (e) => {
      e.preventDefault();
      VIEW.k = 1; VIEW.tx = 0; VIEW.ty = 0;
      const g = vp(); if (g) applyView(g);
    });
  }

  function tgtSub(t) {
    if (t.status === "captured") return t.flag ? "🏳 флаг" : "root";
    if (t.status === "exploitable") return "CVE×" + (t.cve_count || 0) + " ⚑" + (t.exploit_count || 1);
    if (t.status === "vulnerable") return "CVE×" + (t.cve_count || 0);
    if (t.status === "discovered") return "чисто";
    return "ждёт";
  }
  function activeAgents(data) {
    const set = {};
    (data.jobs || []).forEach((j) => (j.chunks || []).forEach((c) => {
      if (c.status === "assigned" && c.agent_id) set[c.agent_id] = true;
    }));
    return set;
  }

  window.renderTopology = function (svg, data) {
    ensureInteractions(svg);
    svg.innerHTML = "";
    const defs = el("defs", {}, svg);
    const hg = el("radialGradient", { id: "hostgrad" }, defs);
    el("stop", { offset: "0%", "stop-color": "#7d5fe0" }, hg);
    el("stop", { offset: "100%", "stop-color": "#1e2a55" }, hg);

    // весь контент — в подвижный слой #vp (его двигаем/масштабируем)
    const root = el("g", { id: "vp" }, svg);
    applyView(root);

    const agents = data.agents || [];
    const targets = (data.topology && data.topology.targets) || [];
    const active = activeAgents(data);
    const N = Math.max(agents.length, 1);

    agents.forEach((a, i) => {
      const ang = (Math.PI * 2 * i) / N - Math.PI / 2;
      a._ang = ang;
      a._pos = { x: CX + R_AGENT * Math.cos(ang), y: CY + R_AGENT * Math.sin(ang) };
      a._active = !!active[a.id];
    });
    const byAgent = {};
    agents.forEach((a) => { byAgent[a.id] = []; });
    const orphan = [];
    targets.forEach((t) => {
      const home = t.route_agent || t.agent_id;
      if (home && byAgent[home]) byAgent[home].push(t);
      else orphan.push(t);
    });

    // ── связи хост→агент (+ бегущие пакеты по живым) ──
    agents.forEach((a) => {
      const online = a.status === "online";
      const cls = "link " + a.status + (online ? " pulse" : "");
      const d = pathD(CX, CY, a._pos.x, a._pos.y, 0.12);
      el("path", { class: cls, d }, root);
      if (online) {
        packet(root, d, a._active ? 1.6 : 3.2, "", a._active ? 3.5 : 2.6, 0);
        if (a._active) packet(root, d, 1.6, "", 3.5, 0.8);
      }
    });

    // ── цели под агентом (в его секторе) ──
    const sectorHalf = Math.PI / N;
    agents.forEach((a) => {
      const list = byAgent[a.id]; if (!list.length) return;
      const n = list.length;
      const spread = Math.min(sectorHalf * 1.7, 0.34 * (n - 1));
      list.forEach((t, k) => {
        const off = n === 1 ? 0 : (-spread / 2 + spread * (k / (n - 1)));
        const rad = R_TGT + (k % 2 ? 26 : 0);
        const ang = a._ang + off;
        t._pos = { x: CX + rad * Math.cos(ang), y: CY + rad * Math.sin(ang) };
        const c = TGT_COL[t.status] || "#8b949e";
        const rerouted = !!t.rerouted_from;
        const scanning = a._active;
        const d = pathD(a._pos.x, a._pos.y, t._pos.x, t._pos.y, 0.10);
        const cls = "tlink" + (t.status === "pending" ? " pending" : "")
          + (rerouted ? " reroute" : "") + (scanning ? " scanning" : "");
        el("path", { class: cls, d, stroke: rerouted ? "#58a6ff" : c }, root);
        if (scanning) packet(root, d, 1.1, "scan", 3, 0);
        if (t.exploiter_id && t.exploiter_id !== a.id) {
          const ex = agents.find((x) => x.id === t.exploiter_id);
          if (ex && ex._pos) {
            const dx = pathD(ex._pos.x, ex._pos.y, t._pos.x, t._pos.y, 0.16);
            el("path", { class: "xlink", d: dx }, root);
            packet(root, dx, 1.8, "cap", 2.6, 0);
          }
        }
      });
    });

    // ── очередь / недостижимые — дугой под хостом ──
    if (orphan.length) {
      const base = Math.PI / 2, span = Math.min(Math.PI * 0.9, 0.24 * (orphan.length - 1));
      orphan.forEach((t, k) => {
        const ang = base - span / 2 + (orphan.length === 1 ? 0 : span * (k / (orphan.length - 1)));
        t._pos = { x: CX + (R_AGENT + 46) * Math.cos(ang), y: CY + (R_AGENT + 46) * Math.sin(ang) };
        const cut = t.reachable === false && t.status !== "pending";
        curve(root, CX, CY, t._pos.x, t._pos.y, "tlink pending", 0.05)
          .setAttribute("stroke", cut ? "#f85149" : TGT_COL.pending);
      });
    }

    // ── узлы целей ──
    targets.forEach((t) => {
      if (!t._pos) return;
      const { x, y } = t._pos;
      const cut = t.reachable === false && t.status !== "pending";
      const c = cut ? "#f85149" : (TGT_COL[t.status] || "#8b949e");
      const g = el("g", { opacity: cut ? "0.75" : "1" }, root);
      if (t.status === "captured" && !cut) {
        const ring = el("circle", { cx: x, cy: y, fill: "none", stroke: c, "stroke-width": 2 }, g);
        el("animate", { attributeName: "r", values: "16;32", dur: "1.8s", repeatCount: "indefinite" }, ring);
        el("animate", { attributeName: "opacity", values: ".8;0", dur: "1.8s", repeatCount: "indefinite" }, ring);
      }
      el("circle", { cx: x, cy: y, r: 16, fill: "rgba(22,27,34,.96)", stroke: c,
        "stroke-width": 2.5, "stroke-dasharray": cut ? "3 3" : "",
        filter: t.status === "captured" && !cut ? "drop-shadow(0 0 6px " + c + ")" : "" }, g);
      el("text", { x, y: y + 5, "text-anchor": "middle", "font-size": "15" }, g).textContent =
        cut ? "⚠" : (t.status === "captured" ? "🚩" : (t.status === "pending" ? "•" : "◎"));
      if (t.rerouted_from)
        el("text", { x: x + 18, y: y - 11, "text-anchor": "middle", "font-size": "13" }, g).textContent = "↻";
      el("text", { x, y: y + 34, "text-anchor": "middle", class: "node-label", "font-size": "11" }, g)
        .textContent = t.ip;
      el("text", { x, y: y + 48, "text-anchor": "middle", class: "node-sub" }, g).textContent =
        cut ? "недостижима" : tgtSub(t);
    });

    // ── узлы агентов ──
    agents.forEach((a) => {
      const { x, y } = a._pos, c = AG_COL[a.status] || "#8b949e";
      const g = el("g", {}, root);
      if (a._active) {
        el("circle", { cx: x, cy: y, r: 40, fill: "none", stroke: "#58a6ff",
          "stroke-width": 2, class: "aglow" }, g);
        el("circle", { cx: x, cy: y, r: 40, fill: "none", stroke: "#58a6ff", "stroke-width": 1,
          "stroke-dasharray": "4 6" }, g)
          .appendChild(el("animateTransform", { attributeName: "transform", type: "rotate",
            from: `0 ${x} ${y}`, to: `360 ${x} ${y}`, dur: "6s", repeatCount: "indefinite" }));
      }
      el("circle", { cx: x, cy: y, r: 30, fill: "rgba(22,27,34,.95)", stroke: c,
        "stroke-width": 3, filter: a.status === "online" ? "drop-shadow(0 0 7px " + c + ")" : "" }, g);
      const roles = a.roles || [];
      if (roles.includes && roles.includes("exploiter"))
        el("circle", { cx: x + 21, cy: y - 21, r: 7, fill: "#a371f7", stroke: "#161b22", "stroke-width": 2 }, g);
      el("text", { x, y: y + 6, "text-anchor": "middle", "font-size": "19" }, g).textContent = "🖥";
      el("text", { x, y: y + 49, "text-anchor": "middle", class: "node-label" }, g).textContent = a.name;
      el("text", { x, y: y + 65, "text-anchor": "middle", class: "node-sub" }, g).textContent =
        (a.tunnel_ip || "") + " · " + (a._active ? "скан…" : (AG_STATUS_RU[a.status] || a.status));
    });

    // ── хост ──
    const gh = el("g", {}, root);
    el("circle", { cx: CX, cy: CY, r: 46, fill: "url(#hostgrad)", stroke: "#58a6ff",
      "stroke-width": 3, filter: "drop-shadow(0 0 12px rgba(88,166,255,.55))" }, gh);
    el("text", { x: CX, y: CY + 6, "text-anchor": "middle", class: "node-label", "font-size": "17" }, gh)
      .textContent = "◈ HOST";
    el("text", { x: CX, y: CY + 66, "text-anchor": "middle", class: "node-sub" }, gh)
      .textContent = "control plane · " + (data.host_ip || "10.9.0.1");

    if (!agents.length)
      el("text", { x: CX, y: CY + 120, "text-anchor": "middle", class: "node-sub" }, root)
        .textContent = "нет нод — добавь агента во вкладке «Агенты»";

    // ── подсказка по управлению (фиксирована, вне подвижного слоя) ──
    el("text", { x: 14, y: 22, class: "topo-hint" }, svg)
      .textContent = "колесо — масштаб · тяни — двигать · 2×клик — сброс";
  };
})();
