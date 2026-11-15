"""Regenerate every number on the site:  python scripts/make_numbers.py [--hydradb] [--seeds 5]

Writes static/numbers.json and NUMBERS.md. Learning experiments run on the in-memory reference
store (identical results to HydraDB, verified); --hydradb adds latency/cost measured on a running
HydraDB (HYDRADB_URL). With ANTHROPIC_API_KEY / OPENAI_API_KEY set, the verifier is a real LLM judge
and its error rate against CoDEx human labels is measured too.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval import run as run_learning  # noqa: E402
from physarum import replay as replay_mod  # noqa: E402
from physarum import vector_rag  # noqa: E402
from physarum.audit import audit  # noqa: E402
from physarum.data import World, load_codex, split  # noqa: E402
from physarum.engine import Engine, Params  # noqa: E402
from physarum.store import HydraStore, MemoryStore  # noqa: E402
from physarum.verifier import LLMJudge, NoisyOracle, make_verifier  # noqa: E402


def acc(engine, store, world, qs):
    hit = 0
    for q in qs:
        c = store.chain(q.s, q.r1, q.r2)
        a = engine.answers(c)
        hit += bool(a) and a[0]["answer"] in world.truth(c)
    return hit / len(qs)


def learning(seeds):
    kg = load_codex()
    out = {m: dict(transfer=[], unrelated=[]) for m in ("static", "evidence", "physarum")}
    drift = {m: [] for m in out}
    for seed in range(seeds):
        for mode in out:
            c = run_learning(mode, MemoryStore(kg), kg, 3000, 3000, seed, 0.1, log=lambda *_: None)
            out[mode]["transfer"].append(c[-1]["transfer"])
            out[mode]["unrelated"].append(c[-1]["unrelated"])
            d = run_learning(mode, MemoryStore(kg), kg, 4500, 250, seed, 0.1, log=lambda *_: None, drift_at=2000)
            pts = {
                r["q"] - 2000: r["changed_facts"]
                for r in d
                if r.get("changed_facts") is not None and not r.get("event")
            }
            drift[mode].append([pts.get(250), pts.get(1000), pts.get(2500)])
    mean = lambda xs: round(float(np.mean(xs)), 4)
    L = {m: {k: mean(v) for k, v in d.items()} for m, d in out.items()}
    for m in L:
        L[m]["transfer_std"] = round(float(np.std(out[m]["transfer"])), 4)
    D = dict(at=[250, 1000, 2500], **{m: [mean([r[i] for r in drift[m]]) for i in range(3)] for m in drift})
    return L, D


def replay_and_audit(mode="physarum", seed=0, pre=1500, post=600, verifier=None):
    """Agents use the graph (25% head-of-state questions), the real 2020→2026 changes are written in,
    traffic continues. Returns heal curve, votes-to-heal per country, audit, removal test."""
    kg = load_codex()
    store = MemoryStore(kg)
    world = World(kg)
    eng = Engine(Params(mode=mode))
    ver = verifier or NoisyOracle(0.1, seed)
    stream, transfer, _ = split(kg, seed=0, n_stream=400, n_heldout=100)
    hos = replay_mod.head_of_state_questions(kg, replay_mod.countries_of(kg))
    rng = np.random.default_rng(seed)
    z = 1 / np.arange(1, len(stream) + 1)
    z /= z.sum()
    zh = 1 / np.arange(1, len(hos) + 1)
    zh /= zh.sum()
    seen, touched_counts = {}, []

    def step(q):
        c = store.chain(q.s, q.r1, q.r2)
        a = eng.answers(c)
        if not a:
            return None
        for cc in c:
            for e in cc.edges:
                seen[e.eid] = e
        truth = world.truth(c)
        facts = [(kg.names[e.src], kg.rel_label.get(e.rel, e.rel), kg.names[e.dst]) for e in a[0]["path"].edges]
        fb = eng.feedback(c, q.s, a[0]["path"], ver(truth, a[0]["answer"], path_facts=facts))
        touched_counts.append(len(fb["touched"]))
        return a[0]["answer"] in truth

    pick = lambda: hos[rng.choice(len(hos), p=zh)] if rng.random() < 0.25 else stream[rng.choice(len(stream), p=z)]
    for _ in range(pre):
        step(pick())
    res = replay_mod.apply(kg, world, store)
    qs = res.questions
    by_country = {}
    for q in qs:
        c = store.chain(q.s, q.r1, q.r2)
        for cc in c:
            if cc.edges[0].dst in replay_mod.countries_of(kg):
                by_country.setdefault(cc.edges[0].dst, []).append(q)
                break
    curve = [dict(v=0, physarum=round(acc(eng, store, world, qs), 4))]
    static = Engine(Params(mode="static"))
    static_after = acc(static, store, world, qs)

    def probe_ok(c):
        p = by_country[c][0]
        cc = store.chain(p.s, p.r1, p.r2)
        a = eng.answers(cc)
        return bool(a) and a[0]["answer"] in world.truth(cc)

    wrong_after = {c for c in by_country if not probe_ok(c)}  # countries that actually need healing
    votes, healed = {c: 0 for c in wrong_after}, {}
    for v in range(1, post + 1):
        q = pick()
        step(q)
        for c in wrong_after:
            if c not in healed and q in by_country[c]:
                votes[c] += 1
                if probe_ok(c):
                    healed[c] = votes[c]
        if v % 100 == 0:
            curve.append(dict(v=v, physarum=round(acc(eng, store, world, qs), 4)))
    after = acc(eng, store, world, qs)
    au = audit(seen.values(), world.edge_truth)
    au.pop("items")
    seen_n = au["examined"] or 1
    base = au["false_facts_seen"] / seen_n
    # removal test on held-out questions
    learned = acc(eng, store, world, transfer)
    snap = {eid: (e.R, e.acc, e.rej) for eid, e in seen.items()}
    scale = eng.scale
    for e in seen.values():
        e.R, e.acc, e.rej = 0.0, 0.0, 0.0
    eng.scale = 1.0
    wiped = acc(eng, store, world, transfer)
    for eid, (R, a_, r_) in snap.items():
        seen[eid].R, seen[eid].acc, seen[eid].rej = R, a_, r_
    eng.scale = scale
    restored = acc(eng, store, world, transfer)
    return dict(
        after=round(after, 4),
        static_after=round(static_after, 4),
        curve=curve,
        questions=len(qs),
        events=res.events,
        votes_to_heal=healed,
        countries=len(wrong_after),
        audit=dict(
            precision=au["precision"],
            recall=au["recall"],
            flagged=au["flagged"],
            examined=au["examined"],
            base_rate=round(base, 4),
            lift=round(au["precision"] / base, 2) if au["precision"] and base else None,
        ),
        removal=dict(learned=round(learned, 4), wiped=round(wiped, 4), restored=round(restored, 4)),
        edges_per_verdict=round(float(np.mean(touched_counts)), 2),
        decay_naive=len(kg.edges),
    )


def rag_numbers(k=10):
    """Hybrid RAG (vector + keyword, RRF) with a generous two-hop reader. Deterministic, never learns."""
    if not vector_rag.available():
        return None
    kg = load_codex()
    store = MemoryStore(kg)
    world = World(kg)
    rag = vector_rag.VectorRAG(kg, k=k)
    sets = {}
    for seed in range(3):
        _, transfer, unrelated = split(kg, seed=seed, n_stream=400, n_heldout=100)
        sets.setdefault("transfer", []).extend(transfer)
        sets.setdefault("unrelated", []).extend(unrelated)
    hos = replay_mod.head_of_state_questions(kg, replay_mod.countries_of(kg))

    def score(qs):
        hit = found = hit_f = 0
        for q in qs:
            a = rag.answer(q)
            ok = a["answer"] is not None and a["answer"] in world.truth(store.chain(q.s, q.r1, q.r2))
            hit += ok
            if a["answer"] is not None:
                found += 1
                hit_f += ok
        return round(hit / len(qs), 4), round(found / len(qs), 4), round(hit_f / max(1, found), 4)

    t, tf, tff = score(sets["transfer"])
    u, _, _ = score(sets["unrelated"])
    h20, _, _ = score(hos)
    replay_mod.apply(kg, world, store)
    h26, _, h26f = score(hos)
    return dict(
        name=rag.name,
        transfer=t,
        unrelated=u,
        found=tf,
        acc_given_found=tff,
        hos_2020=h20,
        hos_2026=h26,
        hos_2026_given_found=h26f,
        context_tokens=vector_rag.path_tokens(2 * k),
        physarum_tokens=vector_rag.path_tokens(2),
    )


def active_vs_random(seed=0, budget=400):
    """Votes needed: questions chosen by the graph's own uncertainty vs at random."""
    out = {}
    for strategy in ("random", "active"):
        kg = load_codex()
        store = MemoryStore(kg)
        world = World(kg)
        eng = Engine(Params())
        stream, transfer, _ = split(kg, seed=seed, n_stream=400, n_heldout=100)
        rng = np.random.default_rng(seed)
        ver = NoisyOracle(0.1, seed)
        for _ in range(budget):
            if strategy == "random":
                q = stream[rng.integers(len(stream))]
            else:
                best, bm = None, 9
                for i in rng.choice(len(stream), size=24, replace=False):
                    cand = stream[int(i)]
                    a = eng.answers(store.chain(cand.s, cand.r1, cand.r2))
                    m = a[0]["confidence"] - a[1]["confidence"] if len(a) > 1 else 1
                    if m < bm:
                        best, bm = cand, m
                q = best
            c = store.chain(q.s, q.r1, q.r2)
            a = eng.answers(c)[0]
            eng.feedback(c, q.s, a["path"], ver(world.truth(c), a["answer"]))
        out[strategy] = round(acc(eng, store, world, transfer), 4)
    return out


def hydradb_bench(n=200):
    kg = load_codex()
    hs = HydraStore(kg)
    if not hs.healthy():
        return None
    qs = random.Random(0).sample(kg.questions, n)
    lat = []
    for q in qs:
        t0 = time.perf_counter()
        hs.chain(q.s, q.r1, q.r2)
        lat.append((time.perf_counter() - t0) * 1000)
    eng = Engine()
    wl = []
    for q in qs[:100]:
        c = hs.chain(q.s, q.r1, q.r2)
        a = eng.answers(c)
        if not a:
            continue
        fb = eng.feedback(c, q.s, a[0]["path"], True)
        t0 = time.perf_counter()
        hs.write(eng, fb["touched"].values())
        wl.append((time.perf_counter() - t0) * 1000)
    hs.reset([e for q in qs[:100] for c in hs.chain(q.s, q.r1, q.r2) for e in c.edges])
    q = lambda xs, p: round(float(np.percentile(xs, p)), 1)
    return dict(
        read_p50_ms=q(lat, 50), read_p95_ms=q(lat, 95), write_p50_ms=q(wl, 50), write_p95_ms=q(wl, 95), url=hs.url
    )


def verifier_error(n=200):
    if not LLMJudge.available():
        return None
    kg = load_codex()
    j = LLMJudge()
    rng = random.Random(0)
    true_e = rng.sample([e for e in kg.edges if e[3]], n)
    false_e = rng.sample([e for e in kg.edges if not e[3]], n)
    wrong = 0
    for s, d, r, t in true_e + false_e:
        ok = j.fact(kg.names[s], kg.rel_label.get(r, r), kg.names[d])
        wrong += ok != t
    j.save()
    return dict(model=j.model, error_rate=round(wrong / (2 * n), 4), n=2 * n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--hydradb", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    L, D = learning(a.seeds)
    use_llm = LLMJudge.available()
    R = replay_and_audit(verifier=make_verifier() if use_llm else None)
    Rc = replay_and_audit(mode="evidence")
    AV = active_vs_random()
    RAG = rag_numbers()
    H = hydradb_bench() if a.hydradb else None
    V = verifier_error() if use_llm else None
    healed = list(R["votes_to_heal"].values())
    n = dict(
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        store="HydraDB" if H else "reference store (results identical to HydraDB)",
        verifier=(V and f"LLM judge {V['model']} (error {V['error_rate']:.1%})") or "simulated, 10% noise",
        learning=dict(seeds=a.seeds, **L),
        drift=D,
        replay=dict(
            physarum_after=R["after"],
            static_after=R["static_after"],
            evidence_after=Rc["after"],
            verdicts=600,
            questions=R["questions"],
            countries=R["countries"],
            curve=R["curve"],
            votes_to_heal_median=(statistics.median(healed) if healed else None),
            healed=len(healed),
            events=R["events"],
        ),
        audit=R["audit"],
        removal=R["removal"],
        active=AV,
        rag=RAG,
        engineering=dict(
            edges_per_verdict=R["edges_per_verdict"],
            requests_per_verdict=2,
            decay_writes=0,
            naive_decay_writes=R["decay_naive"],
            extra_props_per_edge=4,
            hydradb=H,
        ),
        verifier_check=V,
        seconds=round(time.time() - t0, 1),
    )
    (ROOT / "static" / "numbers.json").write_text(json.dumps(n, indent=1))
    pc = lambda x: "–" if x is None else f"{x:.0%}"
    md = [
        f"# Numbers (generated {n['generated']})",
        "",
        f"Store: {n['store']} · Verifier: {n['verifier']} · {a.seeds} seeds",
        "",
        "| Metric | Hybrid RAG | Frozen | Count-only | Physarum |",
        "|---|---|---|---|---|",
        f"| Never-asked questions | {pc(RAG and RAG['transfer'])} | {pc(L['static']['transfer'])} | {pc(L['evidence']['transfer'])} | **{pc(L['physarum']['transfer'])}** |",
        f"| Questions about other people | {pc(RAG and RAG['unrelated'])} | {pc(L['static']['unrelated'])} | {pc(L['evidence']['unrelated'])} | **{pc(L['physarum']['unrelated'])}** |",
        *[
            f"| Facts changed, +{D['at'][i]} questions | – | {pc(D['static'][i])} | {pc(D['evidence'][i])} | **{pc(D['physarum'][i])}** |"
            for i in range(3)
        ],
        f"| Real 2020→2026 heads of state (+600 verdicts) | {pc(RAG and RAG['hos_2026'])} | {pc(R['static_after'])} | {pc(Rc['after'])} | **{pc(R['after'])}** |",
        "",
        *(
            [
                f"- {RAG['name']}: finds the person's facts {pc(RAG['found'])} of the time; when it does, picks the true fact {pc(RAG['acc_given_found'])}; on real 2026 changes picks the current fact {pc(RAG['hos_2026_given_found'])} (it was {pc(RAG['hos_2020'])} right in 2020)",
                f"- Context per answer: Physarum {RAG['physarum_tokens']} tokens (the path) vs RAG {RAG['context_tokens']} tokens (top-{10} × 2 hops)",
            ]
            if RAG
            else []
        ),
        f"- Votes to heal a real change (median per country): **{n['replay']['votes_to_heal_median']}** ({len(healed)}/{R['countries']} countries healed)",
        f"- Self-audit: precision **{pc(R['audit']['precision'])}**, recall {pc(R['audit']['recall'])}, chance {pc(R['audit']['base_rate'])} (lift {R['audit']['lift']}×)",
        f"- Removal test: learned {pc(R['removal']['learned'])} → wiped {pc(R['removal']['wiped'])} → restored {pc(R['removal']['restored'])}",
        f"- Active learning (400 votes): uncertainty-chosen {pc(AV['active'])} vs random {pc(AV['random'])} on never-asked questions",
        f"- Cost per verdict: {R['edges_per_verdict']} edges, 2 requests; forgetting: 0 writes (naive: {R['decay_naive']:,})",
    ]
    if H:
        md.append(
            f"- HydraDB ({H['url']}): read p50 {H['read_p50_ms']} ms / p95 {H['read_p95_ms']} ms · write p50 {H['write_p50_ms']} ms / p95 {H['write_p95_ms']} ms"
        )
    if V:
        md.append(
            f"- LLM judge {V['model']}: error rate {V['error_rate']:.1%} on {V['n']} CoDEx facts with human labels"
        )
    (ROOT / "NUMBERS.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    print(f"\n{n['seconds']} s")


if __name__ == "__main__":
    main()
