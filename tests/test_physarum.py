"""Fast tests (in-memory reference store; same semantics as HydraDB)."""

import random
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from physarum import replay  # noqa: E402
from physarum.audit import audit, flagged  # noqa: E402
from physarum.data import World, load_codex, split  # noqa: E402
from physarum.engine import Candidate, EdgeState, Engine, Params  # noqa: E402
from physarum.store import MemoryStore  # noqa: E402
from physarum.verifier import NoisyOracle  # noqa: E402


@pytest.fixture(scope="module")
def kg():
    return load_codex()


def toy():
    # two routes s(0) -> t(3): short via 1, long via 2
    a, b = EdgeState(0, 0, 1, "X"), EdgeState(1, 1, 3, "Y")
    c, d = EdgeState(2, 0, 2, "X"), EdgeState(3, 2, 3, "Y")
    return [Candidate([0, 1, 3], [a, b]), Candidate([0, 2, 3], [c, d])]


def test_kirchhoff_conserves_flow():
    eng = Engine()
    cands = toy()
    q = eng.kirchhoff(cands, 0, 3)
    out_of_source = q[0] + q[2]
    assert out_of_source == pytest.approx(eng.params.inject, rel=1e-9)
    assert q[0] == pytest.approx(q[1]) and q[2] == pytest.approx(q[3])  # conservation at hops


def test_flow_splits_by_conductance():
    eng = Engine()
    cands = toy()
    cands[0].edges[0].acc = 5  # route 1 has shorter tubes now
    q = eng.kirchhoff(cands, 0, 3)
    assert q[0] > q[2]


def test_verified_answer_thickens_and_rejection_retracts():
    eng = Engine()
    cands = toy()
    eng.feedback(cands, 0, cands[0], True)
    assert all(e.R > 0 and e.acc > 0 for e in cands[0].edges)
    r_before = eng.resistance(cands[0].edges[0])
    eng.feedback(cands, 0, cands[0], False)
    assert eng.resistance(cands[0].edges[0]) > r_before


def test_evaporation_is_free_and_rank_preserving():
    eng = Engine()
    cands = toy()
    eng.feedback(cands, 0, cands[0], True)
    order = [c.nodes for c in eng.rank(cands)]
    for _ in range(50):
        eng.scale /= 1 - eng.params.rho  # what feedback does: no edge is written
    assert [c.nodes for c in eng.rank(cands)] == order


def test_static_mode_never_learns():
    eng = Engine(Params(mode="static"))
    cands = toy()
    r0 = [eng.resistance(e) for c in cands for e in c.edges]
    eng.feedback(cands, 0, cands[1], True)
    assert [eng.resistance(e) for c in cands for e in c.edges] == r0


def test_dataset_shape(kg):
    assert len(kg.names) == 2034 and len(kg.edges) == 40198
    assert sum(not t for *_, t in kg.edges) == 3655
    assert len(kg.questions) > 2000


def test_memory_store_paths_are_simple(kg):
    st = MemoryStore(kg)
    for q in kg.questions[:300]:
        for c in st.chain(q.s, q.r1, q.r2):
            assert len(set(c.nodes)) == len(c.nodes)


def test_learning_improves_never_asked_questions(kg):
    st, w, eng = MemoryStore(kg), World(kg), Engine()
    stream, transfer, _ = split(kg, seed=0, n_heldout=100)
    acc = lambda: np.mean(
        [eng.answers(st.chain(q.s, q.r1, q.r2))[0]["answer"] in w.truth(st.chain(q.s, q.r1, q.r2)) for q in transfer]
    )
    before = acc()
    o = NoisyOracle(0.1, 0)
    rng = random.Random(0)
    for _ in range(1500):
        q = rng.choice(stream[:150])
        c = st.chain(q.s, q.r1, q.r2)
        a = eng.answers(c)[0]
        eng.feedback(c, q.s, a["path"], o(w.truth(c), a["answer"]))
    assert acc() > before + 0.25


def test_real_replay_adds_facts_and_moves_truth(kg):
    k = load_codex()
    st, w = MemoryStore(k), World(k)
    n_edges = len(k.edges)
    res = replay.apply(k, w, st)
    assert len(res.new_edges) >= 18 and len(res.retired_edges) >= 15
    assert all(w.edge_truth[e] for e in res.new_edges) and not any(w.edge_truth[e] for e in res.retired_edges)
    assert len(k.edges) == n_edges + len(res.new_edges)
    milne = next(q for q in res.questions if "Milne" in q.text)
    truth = w.truth(st.chain(milne.s, milne.r1, milne.r2))
    assert [k.names[t] for t in truth] == ["Charles III"]


def test_audit_flags_rejected_facts():
    e1, e2 = EdgeState(0, 0, 1, "X", acc=0, rej=2), EdgeState(1, 0, 2, "X", acc=2, rej=0)
    assert flagged(e1) and not flagged(e2)
    res = audit([e1, e2], truth=[False, True])
    assert res["precision"] == 1.0 and res["flagged"] == 1


def test_hybrid_rag_baseline_answers_and_cannot_learn(kg):
    from physarum import vector_rag

    if not vector_rag.available():
        pytest.skip("spaCy model not installed")
    rag = vector_rag.VectorRAG(kg)
    q = next(q for q in kg.questions if q.r1 == "P27")
    a1, a2 = rag.answer(q), rag.answer(q)
    assert a1 == a2 and a1["tokens"] == 180  # deterministic: nothing to learn from


def test_sdk_bring_your_own_graph_heals():
    from physarum import Graph

    g = Graph.from_triples(
        [("Bob", "reports_to", "Carol"), ("Carol", "leads", "Payments"), ("Dana", "leads", "Growth")]
    )
    assert g.ask("Bob", "reports_to", "leads").answer == "Payments"
    g.add("Bob", "reports_to", "Dana")
    a = g.ask("Bob", "reports_to", "leads")
    for _ in range(5):
        if a.answer == "Growth":
            break
        a = g.feedback(a, correct=False)
    assert a.answer == "Growth"
    assert ("Bob", "reports_to", "Carol") in [f[:3] for f in g.audit()]


def test_nl_parser_maps_plain_english(kg):
    from physarum.nl import Parser

    q, interp = Parser(kg).parse("Who is the head of state of A. A. Milne's country?", MemoryStore(kg))
    assert q is not None and q.r2 == "P35" and "Milne" in interp


def test_sessions_are_isolated():
    import os

    os.environ["PHYSARUM_STORE"] = "memory"
    from physarum import service as svc

    a, b = svc.get_session("iso-a"), svc.get_session("iso-b")
    q = svc.HOS[0]
    before = b.view(q)["answers"][0]["name"]
    a.replay()
    for _ in range(40):
        a.apply(a.pick())
    assert a.verdicts == 40 and b.verdicts == 0 and not b.replayed
    assert b.view(q)["answers"][0]["name"] == before
    a = svc.reset_session(a)
    assert a.verdicts == 0 and not a.replayed


def test_cli_ask(tmp_path, capsys):
    from physarum import cli
    from physarum.playground import EXAMPLE

    f = tmp_path / "facts.csv"
    f.write_text(EXAMPLE)
    cli.main(["ask", str(f), "Bob", "reports_to", "leads"])
    out = capsys.readouterr().out
    assert "→" in out and ("Payments" in out or "Growth" in out)


def test_langchain_tools_and_retriever(tmp_path):
    import pytest

    pytest.importorskip("langchain_core")
    from physarum import Graph
    from physarum.langchain import PhysarumRetriever, physarum_tools
    from physarum.playground import EXAMPLE

    f = tmp_path / "facts.csv"
    f.write_text(EXAMPLE)
    g = Graph.from_csv(f)
    ask, fb, audit = physarum_tools(g)
    a = ask.invoke({"subject": "Bob", "relations": ["reports_to", "leads", "based_in"]})
    if a["answer"] != "Berlin":
        assert fb.invoke({"answer_id": a["answer_id"], "correct": False})["answer"] == "Berlin"
        assert audit.invoke({}) == ["Bob reports_to Carol"]
    docs = PhysarumRetriever(graph=g, relations=["reports_to", "leads"]).invoke("Bob")
    assert docs and docs[0].metadata["answer"] == "Growth"


def test_mcp_local_builds(tmp_path):
    import pytest

    pytest.importorskip("mcp")
    from physarum.mcp_local import build
    from physarum.playground import EXAMPLE

    f = tmp_path / "facts.csv"
    f.write_text(EXAMPLE)
    assert build(str(f)) is not None
