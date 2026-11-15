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
