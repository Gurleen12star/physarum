"""Learning-curve experiment.

python eval.py                         # 3 learners, in-memory reference store
python eval.py --drift-at 2000         # the world changes after 2,000 questions
python eval.py --store hydradb         # same experiment against a running HydraDB
"""

import argparse
import json
import random
import time

import numpy as np

from physarum.data import World, load_codex, split
from physarum.engine import Engine, Params
from physarum.store import HydraStore, MemoryStore
from physarum.verifier import NoisyOracle


def accuracy(engine, store, world, qs):
    hit = 0
    for q in qs:
        cands = store.chain(q.s, q.r1, q.r2)
        ans = engine.answers(cands)
        hit += bool(ans) and ans[0]["answer"] in world.truth(cands)
    return hit / len(qs)


def run(mode, store, kg, n_queries, window, seed, noise, log=print, drift_at=None, n_drift=40):
    stream, transfer, unrelated = split(kg, seed=seed)
    world = World(kg)
    engine = Engine(Params(mode=mode))
    oracle = NoisyOracle(noise, seed)
    rng = np.random.default_rng(seed)
    zipf = 1 / np.arange(1, len(stream) + 1) ** 1.0
    zipf /= zipf.sum()
    acc = lambda qs: accuracy(engine, store, world, qs)
    curve = [dict(q=0, stream=None, transfer=acc(transfer), unrelated=acc(unrelated), changed_facts=None)]
    changed, hits = [], 0
    for n in range(1, n_queries + 1):
        if drift_at and n == drift_at + 1:
            changed = world.change(store, stream, n_drift, random.Random(seed))
            curve.append(
                dict(
                    q=n - 1,
                    stream=None,
                    transfer=acc(transfer),
                    unrelated=acc(unrelated),
                    changed_facts=acc(changed),
                    event="world changed",
                )
            )
            log(
                f"[{mode}] world changed: {len(changed)} questions affected, right now: {curve[-1]['changed_facts']:.2f}"
            )
        q = stream[rng.choice(len(stream), p=zipf)]
        cands = store.chain(q.s, q.r1, q.r2)
        chosen = engine.answers(cands)[0]
        truth = world.truth(cands)
        hits += chosen["answer"] in truth
        fb = engine.feedback(cands, q.s, chosen["path"], oracle(truth, chosen["answer"]))
        store.write(engine, fb["touched"].values())
        if n % window == 0:
            row = dict(
                q=n,
                stream=hits / window,
                transfer=acc(transfer),
                unrelated=acc(unrelated),
                changed_facts=acc(changed) if changed else None,
            )
            curve.append(row)
            hits = 0
            log(f"[{mode}] {row}")
    store.save_meta(engine.state())
    return curve


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="memory")
    ap.add_argument("--queries", type=int, default=3000)
    ap.add_argument("--window", type=int, default=250)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--noise", type=float, default=0.1)
    ap.add_argument("--modes", default="static,evidence,physarum")
    ap.add_argument("--drift-at", type=int, default=None)
    a = ap.parse_args()
    kg = load_codex()
    print(
        f"{len(kg.names)} entities, {len(kg.edges)} edges "
        f"({sum(not t for *_, t in kg.edges)} false), {len(kg.questions)} contested questions"
    )
    results = {}
    for mode in a.modes.split(","):
        store = MemoryStore(kg) if a.store == "memory" else HydraStore(kg)
        if a.store == "hydradb":
            store.reset()  # every tube back to its initial state
        t0 = time.time()
        results[mode] = run(mode, store, kg, a.queries, a.window, a.seed, a.noise, drift_at=a.drift_at)
        extra = (
            f", {store.requests} HydraDB requests, {store.ms / store.requests:.1f} ms avg"
            if a.store == "hydradb"
            else ""
        )
        print(f"{mode}: {time.time() - t0:.1f}s{extra}")
    tag = f"_drift{a.drift_at}" if a.drift_at else ""
    json.dump(results, open(f"results_{a.store}{tag}_seed{a.seed}.json", "w"), indent=1)
