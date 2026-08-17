// topology.js — анимированная SVG-топология: хост в центре, агенты по кругу,
// связи по статусу (online/provisioning/lost/destroyed), активные пульсируют.
(function () {
  const SVG = "http://www.w3.org/2000/svg";
  const CX = 410, CY = 242, R = 172;
  const STATUS_RU = { online: "на связи", provisioning: "настройка",
    lost: "потерян", destroyed: "уничтожен", failed: "ошибка" };
  const COL = { online: "#3fb950", provisioning: "#d29922", lost: "#f85149",
    destroyed: "#3a3a3a", failed: "#f85149" };

  function el(name, attrs, parent) {
    const e = document.createElementNS(SVG, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  window.renderTopology = function (svg, data) {
    svg.innerHTML = "";
    const defs = el("defs", {}, svg);
    const hg = el("radialGradient", { id: "hostgrad" }, defs);
    el("stop", { offset: "0%", "stop-color": "#7d5fe0" }, hg);
    el("stop", { offset: "100%", "stop-color": "#1e2a55" }, hg);
    const agents = data.agents || [];

    // связи
    agents.forEach((a, i) => {
      const ang = (Math.PI * 2 * i) / Math.max(agents.length, 1) - Math.PI / 2;
      a._pos = { x: CX + R * Math.cos(ang), y: CY + R * Math.sin(ang) };
      const cls = "link " + a.status + (a.status === "online" ? " pulse" : "");
      const mx = (CX + a._pos.x) / 2 + (a._pos.y - CY) * 0.12;
      const my = (CY + a._pos.y) / 2 - (a._pos.x - CX) * 0.12;
      el("path", { class: cls, d: `M${CX},${CY} Q${mx},${my} ${a._pos.x},${a._pos.y}` }, svg);
    });

    // ноды
    agents.forEach((a) => {
      const { x, y } = a._pos, c = COL[a.status] || "#8b949e";
      const g = el("g", {}, svg);
      el("circle", { cx: x, cy: y, r: 30, fill: "rgba(22,27,34,.95)", stroke: c,
        "stroke-width": 3, filter: a.status === "online" ? "drop-shadow(0 0 6px " + c + ")" : "" }, g);
      const roles = a.roles || [];
      if ((roles.includes && roles.includes("exploiter")))
        el("circle", { cx: x + 22, cy: y - 22, r: 7, fill: "#a371f7", stroke: "#161b22", "stroke-width": 2 }, g);
      el("text", { x, y: y + 6, "text-anchor": "middle", "font-size": "20" }, g).textContent = "🖥";
      el("text", { x, y: y + 50, "text-anchor": "middle", class: "node-label" }, g).textContent = a.name;
      el("text", { x, y: y + 66, "text-anchor": "middle", class: "node-sub" }, g).textContent =
        (a.tunnel_ip || "") + " · " + (STATUS_RU[a.status] || a.status);
    });

    // хост
    const gh = el("g", {}, svg);
    el("circle", { cx: CX, cy: CY, r: 44, fill: "url(#hostgrad)", stroke: "#58a6ff",
      "stroke-width": 3, filter: "drop-shadow(0 0 10px rgba(88,166,255,.5))" }, gh);
    el("text", { x: CX, y: CY + 6, "text-anchor": "middle", class: "node-label", "font-size": "17" }, gh)
      .textContent = "◈ HOST";
    el("text", { x: CX, y: CY + 64, "text-anchor": "middle", class: "node-sub" }, gh)
      .textContent = "control plane · " + (data.host_ip || "10.9.0.1");

    if (!agents.length)
      el("text", { x: CX, y: CY + 120, "text-anchor": "middle", class: "node-sub" }, svg)
        .textContent = "нет нод — добавь агента во вкладке «Агенты»";
  };
})();
