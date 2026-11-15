/* Physarum live demo: every number on screen comes from the running API (HydraDB-backed). */
const $ = s => document.querySelector(s);
const pct = x => (x == null ? "–" : Math.round(x * 100) + "%");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const store = {
  get(k) {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set(k, v) {
    try {
      localStorage.setItem(k, v);
    } catch {}
  },
};
// One private sandbox per browser tab; ?shared=1 acts on the shared colony stored in HydraDB.
const MODE = new URLSearchParams(location.search).get("shared") ? "shared" : "sandbox";
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
  const headers = { "X-Physarum-Session": SID, "X-Physarum-Mode": MODE };
  const r = await fetch(
    path,
    body === undefined
      ? { headers }
      : { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body) }
  );
  if (!r.ok) {
    let m = await r.text();
    try {
      m = JSON.parse(m).detail || m;
    } catch {}
    throw new Error(m);
  }
  return r.json();
}
const S = {
  info: null,
  view: null,
  mode: "q",
  curve: [],
  replayLog: [],
  replayed: false,
  logSince: 0,
  busy: false,
  auto: false,
  sim: null,
  qid: null,
};

function toast(msg, ms = 3800) {
  const t = $("#toast");
  t.innerHTML = msg;
  t.classList.add("on");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("on"), ms);
}
function markStep(n, state) {
  const el = $("#s" + n);
  if (!el) return;
  if (state === "active") {
    document.querySelectorAll(".mstep.active").forEach(x => x.classList.remove("active"));
    el.classList.add("active");
  } else el.classList.add("done");
}
async function busy(btn, label, fn) {
  if (S.busy && !S.auto) return;
  S.busy = true;
  const old = btn.innerHTML;
  btn.disabled = true;
  const t0 = Date.now();
  const tick = setInterval(
    () => (btn.innerHTML = `<span class="spin"></span> ${label} ${Math.round((Date.now() - t0) / 1000)}s`),
    250
  );
  try {
    return await fn();
  } catch (e) {
    toast("⚠ " + e.message, 6000);
    throw e;
  } finally {
    clearInterval(tick);
    btn.innerHTML = old;
    btn.disabled = false;
    S.busy = false;
  }
}

/* ---------- boot ---------- */
async function boot() {
  const info = await api("/api/info");
  S.info = info;
  S.curve = info.curve;
  S.replayed = info.replayed;
  $("#storeBadge").innerHTML =
    `<i class="dot ${info.store === "hydradb" ? "live" : ""}"></i>${info.store === "hydradb" ? "<b>HydraDB</b> live" : "in-memory mirror"} · ${info.edges.toLocaleString()} edges`;
  $("#verifierBadge").innerHTML = info.verifier.startsWith("AI judge")
    ? `judge: <b>${info.verifier.split(" · ")[0].replace("AI judge", "AI")}</b>`
    : `judge: <b>simulated, wrong 10% on purpose</b>`;
  $("#verifierBadge").title =
    "Verdicts stand in for user 👍/👎. Without an API key a simulated judge answers from ground truth and is deliberately wrong 1 time in 10, like real users. You can also vote yourself.";
  setVerdicts(info.verdicts);
  $(MODE === "shared" ? "#segShared" : "#segSandbox").classList.add("on");
  $("#logTitle").textContent = info.store === "hydradb" ? "HydraDB, live" : "Store calls, live (in-memory fallback)";
  listQuestions(
    info.hos_featured
      .slice(0, 12)
      .map(q => ({ ...q, kind: "hos" }))
      .concat(info.featured.slice(0, 18).map(q => ({ ...q, kind: "codex" })))
  );
  drawCurve();
  drawReplay();
  if (info.replayed) {
    const st = await api("/api/replay/status");
    S.replayLog = st.log;
    renderEvents(st.events);
    drawReplay();
  }
  const first = info.hos_featured.find(q => q.text.includes("Milne")) || info.hos_featured[0];
  await select(first.qid);
  pollLog();
  setInterval(pollLog, 1500);
  loadExamples();
  const qs = new URLSearchParams(location.search);
  if (qs.get("autoplay")) autoplay();
  else if (!store.get("physarum_seen")) $("#welcome").classList.add("on");
}
function setVerdicts(n) {
  $("#verdictBadge").textContent = `${(n || 0).toLocaleString()} verdicts`;
}

/* ---------- questions ---------- */
function listQuestions(items) {
  $("#qlist").innerHTML = "";
  items.forEach(it => {
    const d = document.createElement("div");
    d.dataset.qid = it.qid;
    d.innerHTML = (it.kind === "hos" ? '<span class="rw">2026</span>' : "") + `<span>${it.text}</span>`;
    d.onclick = () => select(it.qid);
    $("#qlist").appendChild(d);
  });
}
let st;
$("#search").addEventListener("input", e => {
  clearTimeout(st);
  const v = e.target.value;
  if (v.split(" ").length > 2) return;
  st = setTimeout(async () => listQuestions(await api("/api/search?q=" + encodeURIComponent(v))), 200);
});
async function askText(text) {
  const r = await api("/api/parse?text=" + encodeURIComponent(text));
  const el = $("#interp");
  if (!r.ok) {
    el.className = "interp mono err";
    el.textContent = r.message;
    if (r.suggestions && r.suggestions.length) listQuestions(r.suggestions);
    return;
  }
  el.className = "interp mono";
  el.textContent = "understood as: " + r.interpreted;
  await select(r.qid);
}
$("#askForm").addEventListener("submit", e => {
  e.preventDefault();
  const t = $("#search").value.trim();
  if (t) askText(t);
});
async function loadExamples() {
  try {
    const ex = await api("/api/examples");
    $("#chips").innerHTML = ex
      .slice(0, 4)
      .map(t => `<button>${t}</button>`)
      .join("");
    $("#chips")
      .querySelectorAll("button")
      .forEach(
        b =>
          (b.onclick = () => {
            $("#search").value = b.textContent;
            askText(b.textContent);
          })
      );
  } catch {}
}

async function select(qid, flux) {
  S.qid = qid;
  document.querySelectorAll(".qlist div").forEach(d => d.classList.toggle("on", +d.dataset.qid === qid));
  const v = await api("/api/ask", { qid });
  S.view = v;
  renderAnswer(v);
  if (S.mode === "q") drawDish(v, flux);
  loadWhy();
  markStep(1, "done");
}
function verdictTag(el, ok) {
  el.className = "verdict " + (ok ? "ok" : "no");
  el.textContent = ok ? "✓ right" : "✗ wrong";
}
function renderAnswer(v) {
  $("#qText").textContent = v.question.text;
  const a = v.answers[0];
  if (v.static) {
    $("#aStatic").textContent = v.static.name;
    verdictTag($("#vStatic"), v.static.correct);
  }
  if (v.rag) {
    const el = $("#aRag");
    el.textContent = v.rag.name || "no answer retrieved";
    el.classList.toggle("none", !v.rag.name);
    verdictTag($("#vRag"), v.rag.correct);
    $("#ctxRag").textContent = `${v.rag.tokens} context tokens · never learns`;
    $("#rowRag").title = "Retrieved facts used: " + (v.rag.facts.join(" | ") || "none");
  } else $("#rowRag").style.display = "none";
  $("#ctxPhys").textContent = v.path_tokens ? `${v.path_tokens} tokens` : "";
  if (a) {
    const el = $("#aPhys");
    if (el.textContent !== a.name) {
      el.style.color = "var(--gold2)";
      setTimeout(() => (el.style.color = ""), 700);
    }
    el.textContent = a.name;
    verdictTag($("#vPhys"), a.correct);
    $("#confBar").style.width = Math.round(a.confidence * 100) + "%";
    $("#confTxt").textContent = pct(a.confidence);
  }
  $("#unsure").classList.toggle("on", !!v.unsure);
  $("#truthTxt").textContent =
    "ground truth: " +
    (v.question.truth.join(", ") || "–") +
    (v.question.kind === "hos" ? (S.replayed ? " (2026)" : " (2020 snapshot)") : " (CoDEx)");
}

/* ---------- voting ---------- */
async function vote(correct) {
  if (!S.qid) return;
  const btns = ["#bUp", "#bDown", "#bJudge"].map(s => $(s));
  btns.forEach(b => (b.disabled = true));
  try {
    const r = await api("/api/feedback", { qid: S.qid, correct });
    S.view = r.after;
    renderAnswer(r.after);
    if (S.mode === "q") drawDish(r.after, true);
    loadWhy();
    setVerdicts(r.verdicts);
    markStep(2, "done");
    const who = correct === null ? `Verifier said <b>${r.verdict ? "right" : "wrong"}</b>. ` : "";
    toast(
      who +
        (r.verdict
          ? "🍯 Food at the answer: flow ran and the tubes that carried it thickened."
          : "💡 The path that gave this answer was lit up; its tubes retract.")
    );
  } finally {
    btns.forEach(b => (b.disabled = false));
  }
}
$("#bUp").onclick = () => vote(true);
$("#bDown").onclick = () => vote(false);
$("#bJudge").onclick = () => vote(null);
$("#bSuggest").onclick = () =>
  busy($("#bSuggest"), "thinking", async () => {
    const s = await api("/api/suggest");
    if (!s.qid) return toast("Nothing uncertain right now.");
    await select(s.qid);
    toast(`🎯 Closest race in the graph (margin ${pct(s.margin)}). Your vote settles it.`);
  });

/* ---------- traffic, replay, audit, wipe ---------- */
async function feed(n = 200) {
  markStep(3, "active");
  return busy($("#bFeed"), "agents asking", async () => {
    const r = await api("/api/feed", { n });
    S.curve = r.curve;
    S.replayLog = r.replay_log || S.replayLog;
    setVerdicts(r.verdicts);
    drawCurve();
    drawReplay();
    toast(
      `${n} agent questions answered in ${r.seconds}s (${pct(r.stream_accuracy)} right while learning). Never-asked accuracy is now <b>${pct(r.point.transfer)}</b>.`
    );
    markStep(3, "done");
    if (S.qid) await select(S.qid);
    if (S.mode === "c") drawColony();
    if (S.replayed) loadWorld();
  });
}
$("#bFeed").onclick = () => feed(200);

function renderEvents(events) {
  $("#events").innerHTML = (events || [])
    .map(
      e =>
        `<div class="ev"><b>${e.new}</b> replaces ${e.old} · ${e.date}<br>${e.countries.length} countr${e.countries.length > 1 ? "ies" : "y"}</div>`
    )
    .join("");
}
async function doReplay() {
  markStep(4, "active");
  return busy($("#bReplay"), "writing 2026", async () => {
    const r = await api("/api/replay", {});
    S.replayed = true;
    renderEvents(r.events);
    markStep(4, "done");
    const st = await api("/api/replay/status");
    S.replayLog = st.log;
    drawReplay();
    loadWorld();
    showTab("world");
    if (!r.already)
      toast(
        `🌍 ${r.new_edges} new facts written into HydraDB. The old ones stay. Right now: Physarum <b>${pct(r.right_after)}</b>, frozen graph <b>${pct(r.static_after)}</b>.`,
        6000
      );
    if (S.qid) await select(S.qid);
  });
}
$("#bReplay").onclick = doReplay;

async function loadAudit() {
  const a = await api("/api/audit");
  const p = $("#p-audit");
  if (!a.flagged) {
    p.innerHTML = `<div class="empty">Nothing flagged yet. The graph needs some traffic first (step 3).</div>`;
    return a;
  }
  p.innerHTML =
    `<a class="dl" href="/api/report" download>⬇ Download graph health report (.md)</a><div class="audit-head"><div><b>${a.flagged}</b><span>facts flagged</span></div><div><b>${pct(a.precision)}</b><span>truly false</span></div><div><b>${a.lift}×</b><span>vs chance ${pct(a.base_rate)}</span></div></div>
   <div class="why-head">Facts whose tubes keep starving, checked against human-verified labels and the real 2026 changes:</div>` +
    a.items
      .map(
        (
          e,
          i
        ) => `<div class="aitem" style="animation-delay:${i * 30}ms"><span>${e.src_name} <span class="dim">— ${e.rel_label} →</span> <b>${e.dst_name}</b></span>
      <span class="lab ${e.label.startsWith("2020") ? "r" : e.is_false ? "f" : "t"}">${e.label.startsWith("2020") ? "replaced in 2026" : e.is_false ? "false (human-verified)" : "false alarm"}</span>
      <span class="sus"><i style="width:${Math.round(e.suspicion * 100)}%"></i></span></div>`
      )
      .join("");
  return a;
}
$("#bAudit").onclick = () =>
  busy($("#bAudit"), "auditing", async () => {
    markStep(5, "active");
    showTab("audit");
    const a = await loadAudit();
    if (a.flagged) {
      markStep(5, "done");
      toast(
        `🔎 Flagged ${a.flagged} facts; <b>${pct(a.precision)}</b> are really false (${a.lift}× better than chance).`
      );
    }
  });

const doWipe = () =>
  busy($("#bWipe"), "wiping", async () => {
    markStep(6, "active");
    const r = await api("/api/wipe", {});
    $("#wipeRes").innerHTML =
      `learned <b>${pct(r.before)}</b> → wiped <b>${pct(r.after)}</b><br>${r.edges} tubes forgotten`;
    $("#bRestore").disabled = false;
    toast(`🧽 Memory wiped: never-asked accuracy fell from <b>${pct(r.before)}</b> to <b>${pct(r.after)}</b>.`);
    if (S.qid) await select(S.qid);
  });
$("#bWipe").onclick = doWipe;
const doRestore = () =>
  busy($("#bRestore"), "restoring", async () => {
    const r = await api("/api/restore", {});
    $("#wipeRes").innerHTML += `<br>restored → <b>${pct(r.after)}</b>`;
    markStep(6, "done");
    toast(
      r.shared
        ? `♻ Restored from HydraDB: back to <b>${pct(r.after)}</b>. The learning lives in the graph.`
        : `♻ Restored: back to <b>${pct(r.after)}</b>, identical. The intelligence is the learned tube state, nothing else.`
    );
    if (S.qid) await select(S.qid);
    $("#bRestore").disabled = true;
  });
$("#bRestore").onclick = doRestore;
$("#bReset").onclick = () =>
  busy($("#bReset"), "resetting", async () => {
    if (!S.auto && !confirmReset()) return;
    await api("/api/reset", {});
    S.replayed = false;
    S.replayLog = [];
    $("#events").innerHTML = "";
    $("#wipeRes").innerHTML = "";
    $("#bRestore").disabled = true;
    document.querySelectorAll(".mstep").forEach(x => x.classList.remove("done", "active"));
    const info = await api("/api/info");
    S.curve = info.curve;
    setVerdicts(0);
    drawCurve();
    drawReplay();
    $("#p-audit").innerHTML = '<div class="empty">Run the self-audit (step 5).</div>';
    $("#p-world").innerHTML =
      '<div class="empty">Replay 2020 → 2026 (step 4) to see the real changes and how fast the graph heals.</div>';
    if (S.qid) await select(S.qid);
    toast("Fresh start: everything forgotten, back in 2020.");
  });
function confirmReset() {
  const b = $("#bReset");
  if (b.dataset.armed) {
    delete b.dataset.armed;
    return true;
  }
  b.dataset.armed = 1;
  toast("Click Reset again to confirm.");
  setTimeout(() => delete b.dataset.armed, 3000);
  return false;
}

/* ---------- side panels ---------- */
function showTab(t) {
  document.querySelectorAll(".stabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  document.querySelectorAll(".spane").forEach(p => p.classList.toggle("on", p.id === "p-" + t));
  if (t === "audit") loadAudit();
  if (t === "world") loadWorld();
}
document.querySelectorAll(".stabs button").forEach(b => (b.onclick = () => showTab(b.dataset.t)));
async function loadWhy() {
  if (!S.qid) return;
  const w = await api("/api/why?qid=" + S.qid);
  if (!w.steps.length) {
    $("#p-why").innerHTML = '<div class="empty">No path found.</div>';
    return;
  }
  $("#p-why").innerHTML =
    `<div class="why-head">Answer <b>${w.answer}</b> · path resistance <b>${w.resistance}</b> · confidence <b>${pct(w.confidence)}</b></div>` +
    w.steps
      .map(
        s => `<div class="wstep"><div class="wt">${s.src_name} → <b>${s.dst_name}</b> ${s.new_2026 ? '<span class="lab r" style="font:10px var(--mono);padding:1px 6px;border-radius:99px">new 2026</span>' : ""}</div><div class="wr">${s.rel_label}</div>
      <div class="metrics"><div>L<b>${s.length.toFixed(2)}</b></div><div>D<b>${s.conductivity.toFixed(2)}</b></div><div>r = L/D<b>${s.resistance.toFixed(2)}</b></div><div>👍/👎<b>${s.acc.toFixed(1)}/${s.rej.toFixed(1)}</b></div></div>
      <div class="hist">${s.history.length ? "votes:" + s.history.map(h => `<i class="${h.ok ? "ok" : "no"}" title="#${h.n}: ${h.q.replace(/"/g, "")}"></i>`).join("") : "no votes on this tube yet"}</div></div>`
      )
      .join("") +
    (w.runner_up
      ? `<div class="runner">Runner-up: <b>${w.runner_up.name}</b>, resistance ${w.runner_up.resistance} (${pct(w.runner_up.confidence)}). Lower resistance wins.</div>`
      : "") +
    `<div class="formula">L = 1 + 8·(👎+½)/(👍+👎+1) · tube length (hostile terrain)<br>D = 1 + R · diameter grown by Kirchhoff flow<br>HydraDB ranks paths by Σ r with algo.SSpaths weightProp</div>`;
}
async function loadWorld() {
  const st = await api("/api/replay/status");
  if (!st.replayed) return;
  S.replayLog = st.log;
  drawReplay();
  const last = st.log[st.log.length - 1] || {},
    first = st.log[0] || {};
  $("#p-world").innerHTML =
    `<div class="bignum"><div><b class="y">${pct(last.physarum)}</b><span>Physarum now</span></div><div><b class="r">${pct(last.static)}</b><span>frozen graph</span></div><div><b>${st.verdicts_since}</b><span>votes since 2026</span></div></div>
   <div class="why-head">Right after the replay Physarum was at <b>${pct(first.physarum)}</b>. These changes really happened:</div>` +
    st.events
      .map(
        e =>
          `<div class="world-ev"><div class="d">${e.date}</div><b>${e.event}</b><div class="c">${e.old} → <b>${e.new}</b> · ${e.countries.join(", ")}</div>${e.source ? `<a href="${e.source}" target="_blank" rel="noopener">source ↗</a>` : ""}</div>`
      )
      .join("");
}

/* ---------- graph ---------- */
const tubeW = d => Math.max(1, Math.min(9, 1.2 + 4.5 * Math.log(d.conductivity) + 2.2 / d.length));
const tubeC = d =>
  d.new_2026 && d.acc + d.rej === 0
    ? cssVar("--blue")
    : d3.interpolateRgb(cssVar("--tube-lo"), cssVar("--tube-hi"))(Math.max(0, Math.min(1, (6 - d.resistance) / 5.4)));
function drawDish(v, animate) {
  const svg = d3.select("#svg");
  svg.selectAll("*").remove();
  if (S.sim) S.sim.stop();
  const W = $("#svg").clientWidth,
    H = $("#svg").clientHeight;
  const nodes = v.nodes.map(n => ({ ...n })),
    byId = new Map(nodes.map(n => [n.id, n]));
  const truth = new Set(v.question.truth),
    chosen = new Set(v.answers[0] ? v.answers[0].edges : []);
  const links = v.edges.map(e => ({ ...e, source: byId.get(e.src), target: byId.get(e.dst) }));
  const src = nodes.find(n => n.role === "source");
  if (src) {
    src.fx = W * 0.14;
    src.fy = H * 0.5;
  }
  const defs = svg.append("defs");
  defs
    .append("filter")
    .attr("id", "glow")
    .html(
      '<feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
    );
  S.sim = d3
    .forceSimulation(nodes)
    .force(
      "link",
      d3
        .forceLink(links)
        .distance(d => 110 + 30 * Math.min(3, d.resistance / 2))
        .strength(0.5)
    )
    .force("charge", d3.forceManyBody().strength(-520))
    .force("x", d3.forceX(d => (d.role === "source" ? W * 0.14 : d.role === "hop" ? W * 0.47 : W * 0.8)).strength(0.4))
    .force("y", d3.forceY(H / 2).strength(0.07))
    .force("collide", d3.forceCollide(30));
  const g = svg.append("g");
  const tube = g
    .selectAll(".t")
    .data(links)
    .join("path")
    .attr("fill", "none")
    .attr("stroke-linecap", "round")
    .attr("stroke", tubeC)
    .attr("stroke-width", 0)
    .attr("opacity", d => (chosen.has(d.eid) ? 1 : 0.75))
    .attr("filter", d => (chosen.has(d.eid) ? "url(#glow)" : null))
    .attr("stroke-dasharray", d => (d.new_2026 && d.acc + d.rej === 0 ? "6 5" : null))
    .attr("class", d => (d.new_2026 ? "newedge" : null));
  tube.transition().duration(700).attr("stroke-width", tubeW);
  const flux = g
    .selectAll(".fx")
    .data(links.filter(l => animate && l.flux > 0.02))
    .join("path")
    .attr("class", "fluxline")
    .attr("stroke-width", d => 1.2 + 3 * d.flux);
  const lab = g
    .selectAll(".el")
    .data(links)
    .join("text")
    .attr("class", "edge-label")
    .text(d => d.rel_label);
  const node = g.selectAll(".n").data(nodes).join("g");
  node
    .append("circle")
    .attr("r", d => (d.role === "source" ? 15 : d.role === "hop" ? 8 : 12))
    .attr("fill", d => (d.role === "source" ? cssVar("--gold") : d.role === "hop" ? cssVar("--hop") : cssVar("--leaf")))
    .attr("stroke", d =>
      d.role === "answer" ? (truth.has(d.name) ? cssVar("--good") : cssVar("--bad")) : cssVar("--gold")
    )
    .attr("stroke-width", d => (d.role === "answer" ? 2.5 : 1.3));
  node
    .filter(d => d.role === "answer" && v.answers[0] && d.name === v.answers[0].name)
    .append("circle")
    .attr("r", 19)
    .attr("fill", "none")
    .attr("stroke", cssVar("--gold"))
    .attr("stroke-dasharray", "2 4")
    .attr("opacity", 0.8);
  node
    .append("text")
    .attr("class", "node-label")
    .attr("dy", d => (d.role === "source" ? -23 : -17))
    .attr("text-anchor", "middle")
    .text(d => (d.name.length > 26 ? d.name.slice(0, 25) + "…" : d.name));
  const arc = d => {
    const dx = d.target.x - d.source.x,
      dy = d.target.y - d.source.y,
      dr = Math.hypot(dx, dy) * 1.7;
    return `M${d.source.x},${d.source.y}A${dr},${dr} 0 0,1 ${d.target.x},${d.target.y}`;
  };
  S.sim.on("tick", () => {
    nodes.forEach(n => {
      n.x = Math.max(50, Math.min(W - 60, n.x));
      n.y = Math.max(50, Math.min(H - 40, n.y));
    });
    tube.attr("d", arc);
    flux.attr("d", arc);
    lab.attr("x", d => (d.source.x + d.target.x) / 2).attr("y", d => (d.source.y + d.target.y) / 2 - 7);
    node.attr("transform", d => `translate(${d.x},${d.y})`);
  });
}
let TL = null,
  tlTimer = null;
async function drawColony() {
  const tl = await api("/api/timeline");
  if (tl.snapshots.length >= 2) return drawTimelapse(tl);
  $("#tlapse").classList.remove("on");
  const v = await api("/api/colony");
  const svg = d3.select("#svg");
  svg.selectAll("*").remove();
  if (S.sim) S.sim.stop();
  const W = $("#svg").clientWidth,
    H = $("#svg").clientHeight;
  if (!v.edges.length) {
    svg
      .append("text")
      .attr("x", W / 2)
      .attr("y", H / 2)
      .attr("text-anchor", "middle")
      .attr("fill", cssVar("--dim"))
      .text("No tubes yet. Feed some agent questions (step 3).");
    return;
  }
  const nodes = v.nodes.map(n => ({ ...n })),
    byId = new Map(nodes.map(n => [n.id, n]));
  const links = v.edges.map(e => ({ ...e, source: byId.get(e.src), target: byId.get(e.dst) }));
  const deg = d3.rollup(
    links.flatMap(l => [l.src, l.dst]),
    x => x.length,
    x => x
  );
  svg
    .append("defs")
    .append("filter")
    .attr("id", "glow2")
    .html(
      '<feGaussianBlur stdDeviation="3.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
    );
  S.sim = d3
    .forceSimulation(nodes)
    .force(
      "link",
      d3
        .forceLink(links)
        .distance(d => (d.rej > d.acc ? 60 : 26))
        .strength(0.5)
    )
    .force("charge", d3.forceManyBody().strength(-40).distanceMax(260))
    .force("x", d3.forceX(W / 2).strength(0.06))
    .force("y", d3.forceY(H / 2).strength(0.09))
    .force("collide", d3.forceCollide(5));
  const g = svg.append("g");
  const st = links.filter(l => l.rej > l.acc),
    fd = links.filter(l => !(l.rej > l.acc));
  const thr = g
    .selectAll(".s")
    .data(st)
    .join("line")
    .attr("stroke", cssVar("--bad"))
    .attr("stroke-width", 1)
    .attr("stroke-dasharray", "2 3")
    .attr("opacity", 0.7);
  const tub = g
    .selectAll(".f")
    .data(fd)
    .join("line")
    .attr("stroke", tubeC)
    .attr("stroke-width", d => Math.min(7, tubeW(d) * 0.6))
    .attr("stroke-linecap", "round")
    .attr("filter", "url(#glow2)");
  const node = g
    .selectAll("circle")
    .data(nodes)
    .join("circle")
    .attr("r", d => 2 + Math.sqrt(deg.get(d.id) || 1) * 1.6)
    .attr("fill", cssVar("--gold"));
  node.append("title").text(d => d.name);
  const lab = g
    .selectAll("text")
    .data(nodes.filter(n => (deg.get(n.id) || 0) >= 6))
    .join("text")
    .attr("class", "node-label")
    .attr("font-size", 11)
    .text(d => d.name);
  S.sim.on("tick", () => {
    nodes.forEach(n => {
      n.x = Math.max(24, Math.min(W - 24, n.x));
      n.y = Math.max(50, Math.min(H - 30, n.y));
    });
    [thr, tub].forEach(s =>
      s
        .attr("x1", d => d.source.x)
        .attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x)
        .attr("y2", d => d.target.y)
    );
    node.attr("cx", d => d.x).attr("cy", d => d.y);
    lab.attr("x", d => d.x + 8).attr("y", d => d.y - 6);
  });
}
function drawTimelapse(tl) {
  const svg = d3.select("#svg");
  svg.selectAll("*").remove();
  if (S.sim) S.sim.stop();
  const W = $("#svg").clientWidth,
    H = $("#svg").clientHeight;
  const nodes = tl.nodes.map(n => ({ ...n })),
    byId = new Map(nodes.map(n => [n.id, n]));
  const all = new Map();
  tl.snapshots.forEach(sn => sn.edges.forEach(e => all.set(e.eid, e)));
  const links = [...all.values()].map(e => ({ eid: e.eid, source: byId.get(e.src), target: byId.get(e.dst) }));
  const sim = d3
    .forceSimulation(nodes)
    .force("link", d3.forceLink(links).distance(30).strength(0.45))
    .force("charge", d3.forceManyBody().strength(-34).distanceMax(240))
    .force("x", d3.forceX(W / 2).strength(0.06))
    .force("y", d3.forceY(H / 2 + 10).strength(0.09))
    .force("collide", d3.forceCollide(5))
    .stop();
  for (let i = 0; i < 260; i++) sim.tick();
  nodes.forEach(n => {
    n.x = Math.max(24, Math.min(W - 24, n.x));
    n.y = Math.max(56, Math.min(H - 30, n.y));
  });
  svg
    .append("defs")
    .append("filter")
    .attr("id", "glow3")
    .html(
      '<feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
    );
  const g = svg.append("g");
  const line = g
    .selectAll("line")
    .data(links)
    .join("line")
    .attr("x1", d => d.source.x)
    .attr("y1", d => d.source.y)
    .attr("x2", d => d.target.x)
    .attr("y2", d => d.target.y)
    .attr("stroke-linecap", "round")
    .attr("filter", "url(#glow3)");
  const deg = d3.rollup(
    links.flatMap(l => [l.source.id, l.target.id]),
    x => x.length,
    x => x
  );
  const dots = g
    .selectAll("circle")
    .data(nodes)
    .join("circle")
    .attr("cx", d => d.x)
    .attr("cy", d => d.y)
    .attr("r", d => 1.8 + Math.sqrt(deg.get(d.id) || 1) * 1.4)
    .attr("fill", cssVar("--gold"));
  dots.append("title").text(d => d.name);
  g.selectAll("text")
    .data(nodes.filter(n => (deg.get(n.id) || 0) >= 7))
    .join("text")
    .attr("class", "node-label")
    .attr("font-size", 11)
    .attr("x", d => d.x + 7)
    .attr("y", d => d.y - 6)
    .text(d => d.name);
  TL = { tl, line };
  const sl = $("#tlSlider");
  sl.max = tl.snapshots.length - 1;
  sl.value = sl.max;
  $("#tlapse").classList.add("on");
  showFrame(+sl.value);
}
function showFrame(i) {
  if (!TL) return;
  const sn = TL.tl.snapshots[i],
    m = new Map(sn.edges.map(e => [e.eid, e]));
  TL.line
    .transition()
    .duration(450)
    .attr("stroke", d => {
      const e = m.get(d.eid);
      if (!e) return "transparent";
      if (e.rej > e.acc) return cssVar("--bad");
      return e.new && e.acc === 0 ? cssVar("--blue") : tubeC({ resistance: e.r, new_2026: false });
    })
    .attr("stroke-width", d => {
      const e = m.get(d.eid);
      return e ? (e.rej > e.acc ? 1 : Math.min(7, Math.max(0.8, 1 + 3.2 * Math.log(e.c)))) : 0;
    })
    .attr("stroke-dasharray", d => {
      const e = m.get(d.eid);
      return e && e.rej > e.acc ? "2 3" : null;
    });
  $("#tlLabel").textContent = `#${i + 1}/${TL.tl.snapshots.length} · ${sn.v} verdicts · ${sn.label}`;
}
$("#tlSlider").addEventListener("input", e => {
  clearInterval(tlTimer);
  $("#tlPlay").textContent = "▶";
  showFrame(+e.target.value);
});
$("#tlPlay").onclick = () => {
  if (tlTimer) {
    clearInterval(tlTimer);
    tlTimer = null;
    $("#tlPlay").textContent = "▶";
    return;
  }
  const sl = $("#tlSlider");
  let i = 0;
  sl.value = 0;
  showFrame(0);
  $("#tlPlay").textContent = "❚❚";
  tlTimer = setInterval(() => {
    i++;
    if (i > +sl.max) {
      clearInterval(tlTimer);
      tlTimer = null;
      $("#tlPlay").textContent = "▶";
      return;
    }
    sl.value = i;
    showFrame(i);
  }, 650);
};
$("#tQ").onclick = () => {
  S.mode = "q";
  $("#tlapse").classList.remove("on");
  clearInterval(tlTimer);
  $("#tQ").classList.add("on");
  $("#tC").classList.remove("on");
  if (S.view) drawDish(S.view);
};
$("#tC").onclick = () => {
  S.mode = "c";
  $("#tC").classList.add("on");
  $("#tQ").classList.remove("on");
  drawColony();
};

/* ---------- charts ---------- */
function lineChart(sel, series, xKey, xMax) {
  const svg = d3.select(sel);
  svg.selectAll("*").remove();
  const W = svg.node().clientWidth,
    H = svg.node().clientHeight,
    m = { l: 34, r: 10, t: 8, b: 20 };
  const x = d3
    .scaleLinear()
    .domain([
      0,
      Math.max(
        xMax,
        d3.max(
          series.flatMap(s => s.data),
          d => d[xKey]
        ) || 1
      ),
    ])
    .range([m.l, W - m.r]);
  const y = d3
    .scaleLinear()
    .domain([0, 1])
    .range([H - m.b, m.t]);
  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(0,${H - m.b})`)
    .call(d3.axisBottom(x).ticks(5).tickFormat(d3.format("~s")));
  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(${m.l},0)`)
    .call(d3.axisLeft(y).ticks(4).tickFormat(d3.format(".0%")));
  series.forEach((s, i) => {
    let data = s.data.filter(d => d[s.key] != null);
    if (data.length === 1) data = [data[0], { ...data[0], [xKey]: x.domain()[1] }];
    if (!data.length) return;
    const p = svg
      .append("path")
      .datum(data)
      .attr("fill", "none")
      .attr("stroke", s.color)
      .attr("stroke-width", s.w || 2)
      .attr("stroke-dasharray", s.dash || null)
      .attr(
        "d",
        d3
          .line()
          .x(d => x(d[xKey]))
          .y(d => y(d[s.key]))
          .curve(d3.curveMonotoneX)
      );
    if (!s.dash) {
      const L = p.node().getTotalLength();
      p.attr("stroke-dasharray", `${L} ${L}`)
        .attr("stroke-dashoffset", L)
        .transition()
        .duration(900)
        .attr("stroke-dashoffset", 0);
    }
    const last = data[data.length - 1];
    svg
      .append("text")
      .attr("x", W - m.r - 4 - i * 64)
      .attr("y", y(last[s.key]) - 5)
      .attr("text-anchor", "end")
      .attr("fill", s.color)
      .attr("font-size", 10.5)

      .text(`${s.label} ${pct(last[s.key])}`);
  });
}
function drawCurve() {
  lineChart(
    "#chartL",
    [
      { data: S.curve, key: "rag_transfer", color: cssVar("--blue"), dash: "2 4", label: "RAG", w: 1.5 },
      { data: S.curve, key: "static_transfer", color: cssVar("--faint"), dash: "4 4", label: "frozen", w: 1.5 },
      { data: S.curve, key: "transfer", color: cssVar("--gold"), label: "Physarum", w: 2.6 },
    ],
    "verdicts",
    300
  );
}
function drawReplay() {
  const svg = d3.select("#chartR");
  if (!S.replayLog.length) {
    svg.selectAll("*").remove();
    const W = svg.node().clientWidth,
      H = svg.node().clientHeight;
    svg
      .append("text")
      .attr("x", W / 2)
      .attr("y", H / 2)
      .attr("text-anchor", "middle")
      .attr("fill", cssVar("--faint"))
      .attr("font-size", 12)
      .text("waiting for 2026 (step 4)");
    return;
  }
  lineChart(
    "#chartR",
    [
      { data: S.replayLog, key: "rag", color: cssVar("--blue"), dash: "2 4", label: "RAG", w: 1.5 },
      { data: S.replayLog, key: "static", color: cssVar("--bad"), dash: "4 4", label: "frozen", w: 1.5 },
      { data: S.replayLog, key: "physarum", color: cssVar("--gold"), label: "Physarum", w: 2.6 },
    ],
    "v",
    200
  );
}
function redrawAll() {
  drawCurve();
  drawReplay();
  if (S.mode === "q" && S.view) drawDish(S.view);
  else if (S.mode === "c") drawColony();
}
window.addEventListener("resize", redrawAll);
window.addEventListener("themechange", redrawAll);

/* ---------- live HydraDB log ---------- */
async function pollLog() {
  try {
    const l = await api("/api/log?since=" + S.logSince);
    if (!l.items.length) return;
    S.logSince = l.items[l.items.length - 1].i;
    $("#logCount").textContent = `· ${l.total.toLocaleString()} queries`;
    const box = $("#log");
    l.items.slice(-8).forEach(it => {
      const d = document.createElement("div");
      const op = it.op.replace(/\s+/g, " ").slice(0, 120);
      d.innerHTML = `<b>${it.ms.toFixed(1)}ms</b><span>${it.rows}r</span>${op.replace(/</g, "&lt;")}`;
      box.appendChild(d);
    });
    while (box.children.length > 12) box.removeChild(box.firstChild);
  } catch {}
}

/* ---------- tour ---------- */
const TOUR = [
  [
    "#badges",
    "It's live",
    "This graph lives in HydraDB: 40,198 real Wikidata facts, 3,655 of them human-verified as false. Every query you trigger shows up in the live log, bottom right.",
  ],
  [
    "#s1",
    "1 · Ask anything",
    "Type a question in plain English (no LLM needed: it's parsed onto the graph), click an example, or pick one tagged 2026: heads of state that really changed since 2020.",
  ],
  [
    "#answerCard",
    "Three readers, same data",
    "Hybrid RAG (vector + keyword search over every fact), the frozen graph, and Physarum. Only Physarum learns. It ranks paths by learned resistance r = L/D, natively in HydraDB, and needs a fraction of RAG's context tokens.",
  ],
  [
    "#votes",
    "2 · Teach it",
    "👍 or 👎 is the only signal it learns from. Or let the verifier judge. Watch the confidence bar and the tubes change.",
  ],
  [
    "#dish",
    "The tubes",
    "Each edge is a slime-mould tube. Thick gold tubes carried right answers; faint red ones led to wrong answers; dashed blue ones are brand-new 2026 facts.",
  ],
  [
    "#s3",
    "3 · Agent traffic",
    "Simulate 200 agents using the graph. The learning curve below shows accuracy on questions it was never asked.",
  ],
  [
    "#s4",
    "4 · Jump to 2026",
    "Write the real changes since 2020 into the graph (Elizabeth II → Charles III, and more). The old facts stay, just like a real pipeline. Then feed traffic and watch it heal.",
  ],
  [
    "#side",
    "Why, audit, real world",
    "Why? explains any answer tube by tube. Self-audit lists facts the graph stopped trusting, with a downloadable health report. Real world tracks the heal.",
  ],
  [
    "#dish",
    "Time-lapse",
    "After some traffic, open The colony and press ▶: watch the network grow highways and starve false facts, like the slime-mould films.",
  ],
  [
    "#s6",
    "6 · Prove it",
    "Wipe what it learned: it gets dumb again. Restore: identical accuracy. The intelligence lives in the graph.",
  ],
  [
    "#logPanel",
    "HydraDB, live",
    "Every native call (algo.SSpaths reads, batched UNWIND writes) with its latency. Have fun.",
  ],
];
let tIdx = 0;
function tour(start = 0) {
  closeWelcome();
  tIdx = start;
  $("#tour").classList.add("on");
  showTip();
}
function showTip() {
  const [sel, h, p] = TOUR[tIdx];
  const el = $(sel);
  if (!el) return endTour();
  el.scrollIntoView({ block: "nearest" });
  const r = el.getBoundingClientRect(),
    pad = 6,
    spot = $("#spot");
  Object.assign(spot.style, {
    left: r.left - pad + "px",
    top: r.top - pad + "px",
    width: r.width + pad * 2 + "px",
    height: r.height + pad * 2 + "px",
  });
  $("#tipN").textContent = `${tIdx + 1} / ${TOUR.length}`;
  $("#tipH").textContent = h;
  $("#tipP").textContent = p;
  $("#tipBack").style.visibility = tIdx ? "visible" : "hidden";
  $("#tipNext").textContent = tIdx === TOUR.length - 1 ? "Start exploring" : "Next";
  const tip = $("#tip"),
    tw = 340,
    th = tip.offsetHeight || 180;
  let x = r.right + 16,
    y = r.top;
  if (x + tw > innerWidth - 10) x = r.left - tw - 16;
  if (x < 10) {
    x = Math.min(innerWidth - tw - 10, Math.max(10, r.left));
    y = r.bottom + 14;
  }
  if (y + th > innerHeight - 10) y = Math.max(10, innerHeight - th - 10);
  Object.assign(tip.style, { left: x + "px", top: y + "px" });
}
function endTour() {
  $("#tour").classList.remove("on");
  store.set("physarum_seen", "1");
}
$("#tipNext").onclick = () => {
  if (tIdx < TOUR.length - 1) {
    tIdx++;
    showTip();
  } else endTour();
};
$("#tipBack").onclick = () => {
  if (tIdx > 0) {
    tIdx--;
    showTip();
  }
};
$("#tipSkip").onclick = endTour;
$("#bTour").onclick = () => tour(0);
document.addEventListener("keydown", e => {
  if (!$("#tour").classList.contains("on")) return;
  if (e.key === "ArrowRight" || e.key === "Enter") $("#tipNext").click();
  if (e.key === "ArrowLeft") $("#tipBack").click();
  if (e.key === "Escape") endTour();
});
window.addEventListener("resize", () => {
  if ($("#tour").classList.contains("on")) showTip();
});
function closeWelcome() {
  $("#welcome").classList.remove("on");
  store.set("physarum_seen", "1");
}
$("#wAuto").onclick = () => {
  closeWelcome();
  autoplay();
};
$("#wTour").onclick = () => tour(0);
$("#wSkip").onclick = closeWelcome;

/* ---------- autoplay: the 90-second story ---------- */
let stopAuto = false;
function cap(html, prog) {
  $("#capTxt").innerHTML = html;
  $("#capProg").style.width = prog + "%";
}
async function autoplay() {
  if (S.auto) return;
  S.auto = true;
  stopAuto = false;
  closeWelcome();
  $("#caption").classList.add("on");
  const chk = () => {
    if (stopAuto) throw new Error("stopped");
  };
  try {
    cap("Resetting to 2020…", 2);
    await api("/api/reset", {});
    S.replayed = false;
    S.replayLog = [];
    $("#events").innerHTML = "";
    document.querySelectorAll(".mstep").forEach(x => x.classList.remove("done", "active"));
    const info = await api("/api/info");
    S.curve = info.curve;
    setVerdicts(0);
    drawCurve();
    drawReplay();
    chk();
    const milne = info.hos_featured.find(q => q.text.includes("Milne")) || info.hos_featured[0];
    markStep(1, "active");
    await select(milne.qid);
    cap(
      "This graph lives in <b>HydraDB</b>: 40,198 Wikidata facts as of 2020. We ask: <em>who is the head of state of A. A. Milne's country?</em>",
      10
    );
    await sleep(5200);
    chk();
    cap(
      `Three readers on the same facts: <b>hybrid RAG</b> (vector + keyword search), the <b>frozen graph</b>, and <b>Physarum</b>. In 2020 the answer is Elizabeth II. Now agents start using the graph, and every answer gets a 👍 or 👎.`,
      18
    );
    await sleep(3800);
    chk();
    await feed(200);
    chk();
    cap(
      `On questions it was <em>never asked</em>, Physarum jumped from ${pct(S.curve[0].transfer)} to <b>${pct(S.curve[S.curve.length - 1].transfer)}</b>. Hybrid RAG stays at <b>${pct(S.curve[S.curve.length - 1].rag_transfer)}</b>: a false fact looks exactly as relevant as a true one.`,
      35
    );
    await sleep(5200);
    chk();
    cap(
      "Now 2026 arrives. We write what <em>really</em> changed since 2020 into the graph: Elizabeth II → Charles III in 16 realms, Havel → Petr Pavel, John Paul II → Leo XIV.",
      45
    );
    await sleep(3500);
    chk();
    await doReplay();
    await select(milne.qid);
    chk();
    cap(
      `The old facts stay in the graph, as they would in a real pipeline. Physarum still trusts its old highway: <b>${$("#aPhys").textContent}</b>. The new fact is the dashed blue tube. Let's vote on this exact answer…`,
      55
    );
    await sleep(5000);
    chk();
    for (let i = 0; i < 8; i++) {
      const v = S.view && S.view.answers[0];
      if (v && v.correct) break;
      await vote(false);
      chk();
      cap(`👎 #${i + 1}: the path to <b>${v ? v.name : "?"}</b> is lit and retracts. Watch the blue tube…`, 57 + i);
      await sleep(2300);
      chk();
    }
    await vote(true);
    chk();
    cap(
      `Healed after a handful of votes: <b>${$("#aPhys").textContent}</b>, and the 👍 thickened the new tube. Because <i>United Kingdom → head of state</i> is shared, every Briton's answer changes too. Now agents keep using the graph…`,
      62
    );
    await sleep(5200);
    chk();
    await feed(200);
    chk();
    await select(milne.qid);
    showTab("world");
    {
      const L = S.replayLog[S.replayLog.length - 1] || {};
      cap(
        `Across all real 2026 questions: Physarum <b>${pct(L.physarum)}</b>. Hybrid RAG <b>${pct(L.rag)}</b>, because similarity can't tell 2020 from 2026. Frozen graph <b>${pct(L.static)}</b>. Neither of them will ever improve.`,
        68
      );
    }
    await sleep(5500);
    chk();
    showTab("why");
    cap(
      "<em>Why?</em> Every tube on the path, its length L, diameter D, and every vote that shaped it. Fully explainable.",
      74
    );
    await sleep(4800);
    chk();
    showTab("audit");
    markStep(5, "active");
    const a = await loadAudit();
    markStep(5, "done");
    cap(
      `The graph audits itself: it flagged <b>${a.flagged}</b> facts it stopped trusting. <b>${pct(a.precision)}</b> are really false, ${a.lift}× better than chance, including the outdated 2020 heads of state.`,
      84
    );
    await sleep(6500);
    chk();
    cap("Last proof that the graph does the learning: wipe its memory…", 90);
    await sleep(1500);
    await doWipe();
    chk();
    cap(`…accuracy collapses: ${$("#wipeRes").innerText.split("\n")[0]}. Now restore from HydraDB…`, 95);
    await sleep(3200);
    await doRestore();
    chk();
    await sleep(1500);
    $("#tC").click();
    await sleep(1800);
    if (TL) {
      $("#tlPlay").click();
    }
    cap(
      "…and it's back, identical. Here's the whole run as a time-lapse: highways growing, false facts starving. <b>A graph that improves itself, natively on HydraDB.</b> Your turn: ask it anything.",
      100
    );
    await sleep(9000);
    $("#tQ").click();
  } catch (e) {
    if (e.message !== "stopped") toast("⚠ " + e.message, 6000);
  }
  $("#caption").classList.remove("on");
  S.auto = false;
  S.busy = false;
}
$("#capStop").onclick = () => {
  stopAuto = true;
};
$("#bAuto").onclick = autoplay;

boot().catch(e => toast("⚠ " + e.message, 8000));
