"""HTTP API tests (in-memory store): the demo flow, sandboxes, playground and early-access form."""

import os
import tempfile

os.environ["PHYSARUM_STORE"] = "memory"
os.environ.setdefault("PHYSARUM_DATA_DIR", tempfile.mkdtemp(prefix="physarum-test-"))

import pytest
from fastapi.testclient import TestClient

import app as app_mod

client = TestClient(app_mod.app)


def h(sid, mode="sandbox"):
    return {"X-Physarum-Session": sid, "X-Physarum-Mode": mode}


def hos_qid(text="Milne"):
    return next(q["qid"] for q in client.get(f"/api/search?q={text}").json() if q["kind"] == "hos")


def test_pages_and_health():
    for path in ("/", "/demo", "/try"):
        assert client.get(path).status_code == 200
    assert client.get("/api/health").json()["ok"] is True


def test_info_has_real_numbers():
    info = client.get("/api/info", headers=h("t-info")).json()
    assert info["edges"] == 40198 and info["false_edges"] == 3655 and info["hos_questions"] == 339
    assert info["mode"] == "sandbox" and info["curve"]


def test_ask_and_vote_changes_the_answer():
    qid = hos_qid()
    v = client.post("/api/ask", json={"qid": qid}, headers=h("t-vote")).json()
    assert v["answers"][0]["name"] == "Elizabeth II" and v["answers"][0]["correct"]
    r = client.post("/api/feedback", json={"qid": qid, "correct": True}, headers=h("t-vote")).json()
    assert r["verdicts"] == 1 and r["after"]["answers"][0]["confidence"] >= r["before"]["answers"][0]["confidence"]


def test_replay_then_learning_heals_and_is_private():
    qid = hos_qid()
    r = client.post("/api/replay", json={}, headers=h("t-heal")).json()
    assert r["new_edges"] > 0 and r["right_after"] < 0.2
    pt = client.post("/api/feed", json={"n": 300}, headers=h("t-heal")).json()["point"]
    assert pt["hos"] > 0.6 > pt["static_hos"]
    other = client.post("/api/ask", json={"qid": qid}, headers=h("t-other")).json()
    assert other["answers"][0]["name"] == "Elizabeth II"  # another visitor is still in 2020
    audit = client.get("/api/audit", headers=h("t-heal")).json()
    assert audit["flagged"] >= 1
    rep = client.get("/api/report", headers=h("t-heal"))
    assert rep.status_code == 200 and "Graph health report" in rep.text


def test_wipe_restore_is_exact():
    client.post("/api/feed", json={"n": 120}, headers=h("t-wipe"))
    w = client.post("/api/wipe", json={}, headers=h("t-wipe")).json()
    r = client.post("/api/restore", json={}, headers=h("t-wipe")).json()
    assert w["after"] < w["before"] and r["after"] == w["before"]


def test_unknown_question_is_404():
    assert client.post("/api/ask", json={"qid": -5}, headers=h("t-404")).status_code == 404


def test_playground_heals_multi_hop_and_audits():
    s = h("t-play")
    ex = client.get("/api/try/example").json()["text"]
    assert client.post("/api/try/load", json={"text": ex}, headers=s).json()["facts"] == 10
    a = client.post(
        "/api/try/ask", json={"subject": "Bob", "relations": ["reports_to", "leads", "based_in"]}, headers=s
    ).json()
    if a["answer"] != "Berlin":
        a = client.post("/api/try/feedback", json={"id": a["id"], "correct": False}, headers=s).json()["answer"]
    assert a["answer"] == "Berlin"
    tool = client.post("/api/try/ask", json={"subject": "Bob", "relations": ["reports_to", "leads", "uses"]}, headers=s)
    assert tool.json()["answer"] == "Amplitude"  # right for free: the shared hop was healed
    client.post("/api/try/add", json={"subject": "Bob", "relation": "reports_to", "object": "Erin"}, headers=s)
    assert client.get("/api/try/audit", headers=s).status_code == 200


@pytest.mark.parametrize("text", ["", "just words", "a,b"])
def test_playground_rejects_bad_input(text):
    assert client.post("/api/try/load", json={"text": text}, headers=h("t-bad")).status_code == 400


def test_playground_needs_a_graph():
    assert client.post("/api/try/ask", json={"subject": "x", "relations": []}, headers=h("t-none")).status_code == 400
    assert client.get("/api/try/graph", headers=h("t-none")).status_code == 400


def test_playground_graph_view_shows_learning():
    s = h("t-graph")
    client.post("/api/try/load", json={"text": client.get("/api/try/example").json()["text"]}, headers=s)
    g = client.get("/api/try/graph?focus=Bob", headers=s).json()
    assert len(g["edges"]) == 10 and len(g["nodes"]) == 11 and not g["truncated"]
    assert {e["state"] for e in g["edges"]} == {"idle"}
    a = client.post(
        "/api/try/ask", json={"subject": "Bob", "relations": ["reports_to", "leads", "based_in"]}, headers=s
    ).json()
    assert len(a["path_edges"]) == 3 and set(a["path_edges"]) <= set(a["cand_edges"])
    client.post("/api/try/feedback", json={"id": a["id"], "correct": a["answer"] == "Berlin"}, headers=s)
    names = {n["id"]: n["name"] for n in g["nodes"]}
    after = client.get("/api/try/graph", headers=s).json()["edges"]
    states = {(names[e["s"]], e["rel"], names[e["t"]]): e["state"] for e in after}
    if a["answer"] == "Berlin":
        assert states[("Growth", "based_in", "Berlin")] == "fed"
    else:  # the stale fact is where the wrong path split off
        assert states[("Bob", "reports_to", "Carol")] == "bad"
        assert states[("Erin", "reports_to", "Carol")] == "idle"


def test_playground_graph_view_is_capped_for_big_graphs():
    from physarum.playground import MAX_VIEW_EDGES

    s = h("t-big")
    rows = "\n".join(f"n{i},link,n{i + 1}" for i in range(600))
    client.post("/api/try/load", json={"text": rows}, headers=s)
    g = client.get("/api/try/graph?focus=n300", headers=s).json()
    assert g["truncated"] and len(g["edges"]) <= MAX_VIEW_EDGES
    assert any(n["name"] == "n300" for n in g["nodes"])


def test_waitlist_survey_and_traction(tmp_path, monkeypatch):
    from physarum import traction

    for k, v in dict(
        DIR=tmp_path, WAITLIST=tmp_path / "w.jsonl", FEEDBACK=tmp_path / "f.jsonl", COUNTS=tmp_path / "c.json"
    ).items():
        monkeypatch.setattr(traction, k, v)
    assert client.post("/api/waitlist", json={"email": "nope"}).status_code == 400
    assert client.post("/api/waitlist", json={"email": "a@b.co", "use_case": "org chart"}).json()["ok"]
    assert client.get("/api/waitlist/count").json()["count"] == 1
    assert client.post("/api/try/survey", json={"would_use": "sure"}).status_code == 400
    client.post("/api/try/survey", json={"would_use": "yes", "quote": "private note", "consent": False})
    client.post(
        "/api/try/survey",
        json={"would_use": "maybe", "quote": "Useful for our org chart", "name": "A", "consent": True},
    )
    tr = client.get("/api/traction").json()
    assert tr["would_use"] == {"yes": 1, "maybe": 1, "no": 0} and tr["waitlist"] == 1
    assert [x["quote"] for x in tr["testimonials"]] == ["Useful for our org chart"]  # only with consent
    assert tr["visitors"] >= 1 and tr["graphs_built"] >= 1
