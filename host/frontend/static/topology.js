// topology.js — анимированная SVG-топология: хост в центре, агенты по кругу,
// связи окрашены по статусу (online/provisioning/lost/destroyed), активные пульсируют.
(function () {
  const SVG = "http://www.w3.org/2000/svg";
  const CX = 400, CY = 230, R = 155;

  const STATUS_RU = {
    online: "на связи", provisioning: "настройка",
    lost: "потерян", destroyed: "уничтожен", failed: "ошибка",
  };

  function el(name, attrs, parent) {
    const e = document.createElementNS(SVG, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function nodeColor(st) {
    return { online: "#22c55e", provisioning: "#eab308", lost: "#ef4444",
             destroyed: "#3a3a3a", failed: "#ef4444" }[st] || "#8b98a9";
  }

  window.renderTopology = function (svg, data) {
    svg.innerHTML = "";
    const agents = data.agents || [];

    // связи (рисуем первыми, чтобы были под нодами)
    agents.forEach((a, i) => {
      const ang = (Math.PI * 2 * i) / Math.max(agents.length, 1) - Math.PI / 2;
      const x = CX + R * Math.cos(ang), y = CY + R * Math.sin(ang);
      const cls = "link " + a.status + (a.status === "online" ? " pulse" : "");
      const path = el("path", {
        class: cls,
        d: `M${CX},${CY} Q${(CX + x) / 2 + (y - CY) * 0.12},${(CY + y) / 2 - (x - CX) * 0.12} ${x},${y}`,
      }, svg);
      a._pos = { x, y };
    });

    // агенты
    agents.forEach((a) => {
      const { x, y } = a._pos;
      const g = el("g", {}, svg);
      el("circle", { cx: x, cy: y, r: 26, fill: "#161d2b",
        stroke: nodeColor(a.status), "stroke-width": 3 }, g);
      // индикатор эксплуатора
      const roles = a.roles || [];
      if (roles.includes && roles.includes("exploiter"))
        el("circle", { cx: x + 20, cy: y - 20, r: 6, fill: "#8b5cf6" }, g);
      el("text", { x, y: y + 4, "text-anchor": "middle", class: "node-label" }, g)
        .textContent = "🖥";
      el("text", { x, y: y + 44, "text-anchor": "middle", class: "node-label" }, g)
        .textContent = a.name;
      el("text", { x, y: y + 58, "text-anchor": "middle", class: "node-sub" }, g)
        .textContent = (a.tunnel_ip || "") + " · " + (STATUS_RU[a.status] || a.status);
    });

    // хост в центре
    const gh = el("g", {}, svg);
    el("circle", { cx: CX, cy: CY, r: 38,
      fill: "url(#hostgrad)", stroke: "#3b82f6", "stroke-width": 3 }, gh);
    let defs = svg.querySelector("defs");
    if (!defs) {
      defs = el("defs", {}, svg);
      const grad = el("radialGradient", { id: "hostgrad" }, defs);
      el("stop", { offset: "0%", "stop-color": "#3b82f6" }, grad);
      el("stop", { offset: "100%", "stop-color": "#1e3a8a" }, grad);
    }
    el("text", { x: CX, y: CY + 5, "text-anchor": "middle",
      class: "node-label", "font-size": "16" }, gh).textContent = "◈ HOST";
    el("text", { x: CX, y: CY + 56, "text-anchor": "middle", class: "node-sub" }, gh)
      .textContent = "control plane · " + (data.host_ip || "10.9.0.1");

    if (!agents.length) {
      el("text", { x: CX, y: CY + 110, "text-anchor": "middle", class: "node-sub" }, svg)
        .textContent = "нет подключённых нод — добавь агента во вкладке «Агенты»";
    }
  };
})();
