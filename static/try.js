/* Playground: a private self-healing graph built from the visitor's own facts. */
const $ = s => document.querySelector(s);
const pct = x => Math.round(x * 100) + "%";
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const SID = (() => {
  const fresh = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
  try {
    let id = sessionStorage.getItem("physarum_sid");
    if (!id) sessionStorage.setItem("physarum_sid", (id = fresh()));
    return id;
  } catch {
    return fresh();
  }
})();
async function api(path, body) {
  const headers = { "X-Physarum-Session": SID };
  const r = await fetch(
    path,
    body === undefined
      ? { headers }
      : { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body) }
  );
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || "request failed");
  return j;
}
let toastT;
function toast(html) {
  const t = $("#toast");
  t.innerHTML = html;
  t.classList.add("on");
  clearTimeout(toastT);
  toastT = setTimeout(() => t.classList.remove("on"), 3200);
}

const P = { schema: null, answer: null, prev: null };

function relOptions(selected) {
  return P.schema.relations.map(r => `<option ${r === selected ? "selected" : ""}>${esc(r)}</option>`).join("");
}
function addHop(rel) {
  const n = $("#hops").children.length + 1;
  const d = document.createElement("div");
  d.className = "hop";
  d.innerHTML = `<label>then follow <select>${relOptions(rel)}</select></label><button class="x" title="remove hop">×</button>`;
  d.querySelector(".x").onclick = () => {
    if ($("#hops").children.length > 1) d.remove();
  };
  $("#hops").appendChild(d);
  return n;
}
function hops() {
  return [...document.querySelectorAll("#hops select")].map(s => s.value);
}
function snippet() {
  const s = $("#subject").value || "Bob";
  const rels = hops()
    .map(r => `"${r}"`)
    .join(", ");
  $("#snippet").textContent =
    `from physarum import Graph\n\ng = Graph.from_csv("facts.csv")\na = g.ask("${s}", ${rels})\ng.feedback(a, correct=False)\ng.audit()`;
}

/* ---------- live diagram: the visitor's graph, its tubes and the answer path ---------- */
const GR = { nodes: new Map(), hl: { path: [], cand: new Set() }, fitted: false };
const tubeW = e => Math.min(12, 1.4 + 2.6 * (e.D - 1));
const nodeR = n => (n.src || n.ans ? 9 : 5 + Math.min(4, n.deg * 0.6));
const fmt = x => (Math.round(x * 100) / 100).toString();

function gsize() {
  const r = $("#graph").getBoundingClientRect();
  return { W: Math.max(200, r.width), H: Math.max(200, r.height) };
}
function graphInit() {
  if (GR.svg || !window.d3) return !!GR.svg;
  const svg = (GR.svg = d3.select("#graph"));
  GR.k = Math.max(0.55, Math.min(1, gsize().W / 640)); // tighter layout on narrow screens
  const defs = svg.append("defs");
  ["idle", "fed", "weak", "bad"].forEach(k =>
    defs
      .append("marker")
      .attr("id", "ar-" + k)
      .attr("viewBox", "0 0 10 10")
      .attr("refX", 8)
      .attr("refY", 5)
      .attr("markerWidth", 8)
      .attr("markerHeight", 8)
      .attr("markerUnits", "userSpaceOnUse")
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,1 L9,5 L0,9 z")
      .attr("class", "ah " + k)
  );
  GR.root = svg.append("g");
  GR.gL = GR.root.append("g");
  GR.gF = GR.root.append("g");
  GR.gT = GR.root.append("g");
  GR.gN = GR.root.append("g");
  GR.zoom = d3
    .zoom()
    .scaleExtent([0.2, 3])
    .on("zoom", ev => GR.root.attr("transform", ev.transform));
  svg.call(GR.zoom).on("dblclick.zoom", null);
  GR.sim = d3
    .forceSimulation()
    .force(
      "link",
      d3
        .forceLink()
        .id(d => d.id)
        .distance(l => (96 + 5 * Math.min(l.rel.length, 14)) * GR.k)
        .strength(0.55)
    )
    .force("charge", d3.forceManyBody().strength(-520 * GR.k).distanceMax(600))
    .force("collide", d3.forceCollide(d => nodeR(d) + 30 * GR.k))
    .force("x", d3.forceX(0).strength(0.05))
    .force("y", d3.forceY(0).strength(0.08))
    .on("tick", graphTick);
  const size = () => {
    const { W, H } = gsize();
    svg.attr("viewBox", [-W / 2, -H / 2, W, H]);
  };
  size();
  addEventListener("resize", size);
  $("#gFit").onclick = () => graphFit();
  return true;
}
function linkGeom(l) {
  const s = l.source,
    t = l.target;
  const dx = t.x - s.x,
    dy = t.y - s.y,
    d = Math.hypot(dx, dy) || 1,
    ux = dx / d,
    uy = dy / d;
  const rs = nodeR(s) + 1,
    rt = nodeR(t) + 3;
  const x1 = s.x + ux * rs,
    y1 = s.y + uy * rs,
    x2 = t.x - ux * rt,
    y2 = t.y - uy * rt;
  const mx = (x1 + x2) / 2 - uy * l.curve * d,
    my = (y1 + y2) / 2 + ux * l.curve * d;
  return { x1, y1, x2, y2, mx, my };
}
function linkPath(l) {
  const g = linkGeom(l);
  return l.curve ? `M${g.x1},${g.y1}Q${g.mx},${g.my} ${g.x2},${g.y2}` : `M${g.x1},${g.y1}L${g.x2},${g.y2}`;
}
function graphTick() {
  if (!GR.link) return;
  GR.link.attr("d", linkPath);
  GR.flow.attr("d", linkPath);
  GR.label.each(function (l) {
    const g = linkGeom(l);
    d3.select(this)
      .attr("x", (g.x1 + 2 * g.mx + g.x2) / 4)
      .attr("y", (g.y1 + 2 * g.my + g.y2) / 4 - 5);
  });
  GR.node.attr("transform", d => `translate(${d.x},${d.y})`);
}
function graphFit(ms = 650) {
  const ns = [...GR.nodes.values()].filter(n => n.x != null);
  if (!GR.svg || !ns.length) return;
  const { W, H } = gsize();
  const xs = ns.map(n => n.x),
    ys = ns.map(n => n.y);
  const x0 = Math.min(...xs) - 70,
    x1 = Math.max(...xs) + 90,
    y0 = Math.min(...ys) - 40,
    y1 = Math.max(...ys) + 40;
  const k = Math.max(0.2, Math.min(1.5, W / (x1 - x0), H / (y1 - y0)));
  GR.svg
    .transition()
    .duration(ms)
    .call(GR.zoom.transform, d3.zoomIdentity.scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2));
}
function graphRender(view, { fresh: reset = false } = {}) {
  if (!graphInit()) return;
  if (reset) {
    GR.nodes.clear();
    GR.fitted = false;
  }
  $("#gempty").hidden = view.nodes.length > 0;
  const shown = view.edges.length;
  $("#gmeta").textContent = view.truncated
    ? `showing ${shown} of ${view.total_edges.toLocaleString()} facts, around ${$("#subject").value || "the busiest entity"}`
    : `${view.nodes.length} entities · ${shown} facts`;

  // keep each entity's position across updates, so votes animate in place
  const born = [];
  const nodes = view.nodes.map(n => {
    let o = GR.nodes.get(n.id);
    if (!o) {
      o = { id: n.id };
      born.push(o);
      GR.nodes.set(n.id, o);
    }
    return Object.assign(o, n, { src: false, ans: false, onpath: false });
  });
  const keep = new Set(view.nodes.map(n => n.id));
  [...GR.nodes.keys()].forEach(k => keep.has(k) || GR.nodes.delete(k));
  const byId = new Map(nodes.map(n => [n.id, n]));
  const links = view.edges.map(e => ({ ...e, source: byId.get(e.s), target: byId.get(e.t) }));
  born.forEach(o => {
    const l = links.find(l => (l.source === o && !born.includes(l.target)) || (l.target === o && !born.includes(l.source)));
    const a = l ? (l.source === o ? l.target : l.source) : null;
    o.x = (a ? a.x : 0) + (Math.random() - 0.5) * 60;
    o.y = (a ? a.y : 0) + (Math.random() - 0.5) * 60;
  });
  const deg = new Map();
  links.forEach(l => [l.s, l.t].forEach(k => deg.set(k, (deg.get(k) || 0) + 1)));
  nodes.forEach(n => (n.deg = deg.get(n.id) || 0));
  // parallel facts between the same two entities bend apart
  const pairs = new Map();
  links.forEach(l => {
    const k = Math.min(l.s, l.t) + "-" + Math.max(l.s, l.t);
    pairs.has(k) ? pairs.get(k).push(l) : pairs.set(k, [l]);
  });
  pairs.forEach(g =>
    g.forEach((l, i) => (l.curve = g.length < 2 ? 0 : (i - (g.length - 1) / 2) * 0.34 * (l.s < l.t ? 1 : -1)))
  );
  // highlight: the chosen path (in order) and every other candidate path
  const pathIds = new Set(GR.hl.path);
  const byEdge = new Map(links.map(l => [l.id, l]));
  const first = byEdge.get(GR.hl.path[0]),
    last = byEdge.get(GR.hl.path[GR.hl.path.length - 1]);
  links.forEach(l => {
    l.onpath = pathIds.has(l.id);
    l.alt = !l.onpath && GR.hl.cand.has(l.id);
    if (l.onpath) l.source.onpath = l.target.onpath = true;
  });
  if (first) first.source.src = true;
  if (last) last.target.ans = true;

  const big = links.length > 70;
  const stagger = born.length > 3 ? 900 / born.length : 0;
  GR.link = GR.gL
    .selectAll("path.gl")
    .data(links, d => d.id)
    .join(enter =>
      enter
        .append("path")
        .attr("stroke-width", 0)
        .call(p => p.append("title"))
    )
    .attr("class", d => "gl " + d.state)
    .attr("marker-end", d => `url(#ar-${d.state})`);
  GR.link
    .transition()
    .delay((d, i) => (born.includes(d.source) || born.includes(d.target) ? i * stagger : 0))
    .duration(700)
    .attr("stroke-width", tubeW);
  GR.link
    .select("title")
    .text(
      d =>
        `${d.source.name} — ${d.rel} → ${d.target.name}\n` +
        `tube diameter D ${fmt(d.D)} · length L ${fmt(d.L)}\n👍 ${fmt(d.acc)} · 👎 ${fmt(d.rej)}` +
        (d.state === "bad" ? "\nstarving: probably stale or wrong" : d.state === "fed" ? "\nfed: led to right answers" : "")
    );
  GR.flow = GR.gF
    .selectAll("path.gflow")
    .data(
      links.filter(l => l.onpath || l.alt),
      d => d.id
    )
    .join("path")
    .attr("class", d => "gflow " + (d.onpath ? "path" : "alt"));
  GR.label = GR.gT
    .selectAll("text.gel")
    .data(
      links.filter(l => !big || l.onpath || l.alt || l.state === "bad"),
      d => d.id
    )
    .join("text")
    .attr("class", d => "gel" + (d.onpath ? " hl" : d.state === "bad" ? " bad" : ""))
    .text(d => d.rel);
  GR.node = GR.gN
    .selectAll("g.gn")
    .data(nodes, d => d.id)
    .join(enter => {
      const g = enter.append("g").style("opacity", 0);
      g.append("circle").attr("class", "hit"); // bigger, invisible click target
      g.append("circle").attr("class", "dot");
      g.append("text").attr("dy", 4);
      g.append("title");
      g.transition()
        .delay((d, i) => i * stagger)
        .duration(450)
        .style("opacity", 1);
      return g;
    })
    .attr("class", d => "gn" + (d.out ? " out" : "") + (d.onpath ? " onpath" : "") + (d.src ? " src" : "") + (d.ans ? " ans" : ""))
    .on("click", (ev, d) => askFrom(d.name))
    .call(
      d3
        .drag()
        .on("start", (ev, d) => {
          if (!ev.active) GR.sim.alphaTarget(0.25).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on("drag", (ev, d) => {
          d.fx = ev.x;
          d.fy = ev.y;
        })
        .on("end", (ev, d) => {
          if (!ev.active) GR.sim.alphaTarget(0);
          d.fx = d.fy = null;
        })
    );
  GR.node.select("circle.dot").attr("r", nodeR);
  GR.node.select("circle.hit").attr("r", d => nodeR(d) + 9);
  GR.node
    .select("text")
    .attr("x", d => nodeR(d) + 5)
    .text(d => (nodes.length <= 90 || d.onpath || d.src || d.ans || d.deg >= 4 ? d.name : ""));
  GR.node.select("title").text(d => (d.out ? `${d.name}: click to ask from here` : d.name));

  GR.sim.nodes(nodes);
  GR.sim.force("link").links(links);
  GR.sim.alpha(born.length ? 0.9 : 0.15).restart();
  graphTick();
  if (born.length) {
    setTimeout(() => graphFit(), GR.fitted ? 600 : 1300); // first build: let it unfold, then frame it
    GR.fitted = true;
  }
}
async function refreshGraph(opts) {
  if (!P.schema) return;
  const q = new URLSearchParams({
    focus: $("#subject").value.trim(),
    edges: [...GR.hl.path, ...GR.hl.cand].slice(0, 200).join(","),
  });
  try {
    graphRender(await api("/api/try/graph?" + q), opts);
  } catch (e) {
    $("#gmeta").textContent = e.message;
  }
}
function askFrom(name) {
  if (!P.schema) return;
  const outs = P.schema.out_rels[name];
  if (!outs || !outs.length) return toast(`<b>${esc(name)}</b> has no outgoing facts to follow. Try another entity.`);
  $("#subject").value = name;
  const cur = hops();
  if (!cur.length || !outs.includes(cur[0])) {
    $("#hops").innerHTML = "";
    addHop(outs[0]);
  }
  ask();
}

async function load(text) {
  try {
    const sc = await api("/api/try/load", { text });
    P.schema = sc;
    $("#subjects").innerHTML = sc.subjects.map(s => `<option value="${esc(s)}">`).join("");
    $("#hops").innerHTML = "";
    $("#answer").hidden = true;
    $("#askBtn").disabled = false;
    $("#loadNote").innerHTML =
      `Built: <b>${sc.facts.toLocaleString()}</b> facts · ${sc.entities.toLocaleString()} entities · ${sc.relations.length} relations.`;
    const first = sc.subjects.includes("Bob") ? "Bob" : sc.subjects[0];
    $("#subject").value = first;
    if (first === "Bob" && sc.relations.includes("based_in")) ["reports_to", "leads", "based_in"].forEach(addHop);
    else addHop((sc.out_rels[first] || sc.relations)[0]);
    snippet();
    renderAudit([]);
    GR.hl = { path: [], cand: new Set() };
    refreshGraph({ fresh: true });
  } catch (e) {
    $("#loadNote").textContent = e.message;
  }
}

function render(a, before) {
  P.answer = a;
  $("#answer").hidden = false;
  $("#aq").textContent = `${$("#subject").value} → ${hops().join(" → ")}`;
  $("#aname").textContent = a.answer ?? "No path found";
  const tag = $("#atag");
  tag.textContent = a.answer == null ? "" : a.unsure ? "unsure: tell me" : "confident";
  tag.className = "tag " + (a.unsure ? "unsure" : "sure");
  $("#abar").style.width = pct(a.confidence || 0);
  $("#apath").textContent = a.path.length ? a.path.join("  →  ") : "";
  $("#alts").textContent = a.alternatives.length
    ? "Other candidates: " + a.alternatives.map(x => `${x.name} (${pct(x.confidence)})`).join(", ")
    : "";
  $("#yes").disabled = $("#no").disabled = a.answer == null;
  if (before) {
    $("#delta").innerHTML =
      before.answer !== a.answer
        ? `Healed: <b>${esc(before.answer)}</b> → <b>${esc(a.answer)}</b> after one vote.`
        : `Confidence ${pct(before.confidence)} → <b>${pct(a.confidence)}</b>.`;
  } else $("#delta").textContent = "";
  GR.hl = { path: a.path_edges || [], cand: new Set(a.cand_edges || []) };
  refreshGraph();
}

async function ask() {
  try {
    render(await api("/api/try/ask", { subject: $("#subject").value.trim(), relations: hops() }));
    snippet();
  } catch (e) {
    toast(esc(e.message));
  }
}
async function vote(correct) {
  if (!P.answer) return;
  try {
    const before = P.answer;
    const r = await api("/api/try/feedback", { id: before.id, correct });
    render(r.answer, before);
    const au = await api("/api/try/audit");
    renderAudit(au.items);
    if (!correct) toast("Blame went to the fact where the wrong path split off. Try a related question next.");
    if (!P.surveyed) $("#survey").hidden = false;
  } catch (e) {
    toast(esc(e.message));
  }
}
function renderAudit(items) {
  $("#audit").innerHTML = items.length
    ? items
        .map(
          i =>
            `<li class="flag">${esc(i.fact)}<div class="m">👎 ${i.rej} · 👍 ${i.acc}: probably stale or wrong</div></li>`
        )
        .join("")
    : `<li class="dim">Vote 👎 on a wrong answer to see the culprit here.</li>`;
}
async function addFact() {
  const [s, r, o] = ["#fs", "#fr", "#fo"].map(k => $(k).value.trim());
  if (!s || !r || !o) return toast("Fill in subject, relation and object.");
  try {
    P.schema = await api("/api/try/add", { subject: s, relation: r, object: o });
    $("#subjects").innerHTML = P.schema.subjects.map(x => `<option value="${esc(x)}">`).join("");
    document.querySelectorAll("#hops select").forEach(sel => {
      const v = sel.value;
      sel.innerHTML = relOptions(v);
    });
    ["#fs", "#fr", "#fo"].forEach(k => ($(k).value = ""));
    toast(`Added <b>${esc(s)} — ${esc(r)} → ${esc(o)}</b>. Old facts stay; votes decide.`);
    refreshGraph();
  } catch (e) {
    toast(esc(e.message));
  }
}

async function example() {
  const ex = await api("/api/try/example");
  $("#facts").value = ex.text;
  await load(ex.text);
  $("#loadNote").innerHTML += `<br>${esc(ex.note)} Ask where Bob is based, then vote.`;
}

$("#load").onclick = () => load($("#facts").value);
$("#example").onclick = example;
$("#file").onchange = async e => {
  const f = e.target.files[0];
  if (!f) return;
  if (f.size > 600000) return toast("That file is too big for the playground; use the SDK.");
  $("#facts").value = await f.text();
  load($("#facts").value);
};
$("#addHop").onclick = () => {
  if (P.schema) addHop(P.schema.relations[0]);
  snippet();
};
$("#askBtn").onclick = ask;
$("#subject").addEventListener("keydown", e => e.key === "Enter" && ask());
$("#hops").addEventListener("change", snippet);
$("#yes").onclick = () => vote(true);
$("#no").onclick = () => vote(false);
$("#addFact").onclick = addFact;
$("#survey").addEventListener("submit", async ev => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  try {
    await api("/api/try/survey", {
      would_use: f.get("would_use"),
      quote: f.get("quote") || "",
      name: f.get("name") || "",
      role: f.get("role") || "",
      consent: f.get("consent") === "on",
    });
    P.surveyed = true;
    $("#survey").innerHTML = `<div class="done">Thank you! That genuinely helps.</div>`;
  } catch (e) {
    toast(esc(e.message));
  }
});
example();
