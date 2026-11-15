"""Demo service: the shared graph and one learning session per visitor.

The graph (CoDEx + the real 2020→2026 facts, hidden until a session jumps to 2026) lives in
HydraDB and is shared and read-only for visitors: retrieval runs natively on it. Each visitor gets a
private *sandbox* session holding its own learned tube state, so concurrent judges never collide.
The *shared colony* session writes its learning back to HydraDB edges (durable, shared by everyone
in shared mode).

Uses HydraDB when HYDRADB_URL answers /healthz, otherwise the in-memory store with the same
semantics (PHYSARUM_STORE=memory forces it). Verdicts come from an LLM judge when an
ANTHROPIC_API_KEY / OPENAI_API_KEY is set, otherwise from a simulated verifier with 10% noise.
"""

from __future__ import annotations

import collections
import os
import random
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from . import replay as replay_mod
from . import traction, vector_rag
from .audit import audit as run_audit
from .audit import suspicion
from .data import World, load_codex, split
from .engine import Candidate, EdgeState, Engine, Params
from .nl import Parser
from .store import HydraStore, MemoryStore
from .verifier import make_verifier

ROOT = Path(__file__).resolve().parent.parent

# ---- shared, read-only after start-up ---------------------------------------------------------
KG = load_codex()
BASE_NAMES, BASE_EDGES = len(KG.names), len(KG.edges)
STREAM, TRANSFER, UNRELATED = split(KG, seed=0, n_stream=400, n_heldout=100)
HOS = replay_mod.head_of_state_questions(KG, replay_mod.countries_of(KG))  # 339 real questions
HOS_EVAL = random.Random(3).sample(HOS, min(80, len(HOS)))
_world = World(KG)
REPLAY = replay_mod.apply(KG, _world, None)  # the 2026 facts, appended once, hidden per session
TRUTH_2026 = list(_world.edge_truth)
TRUTH_2020 = [t for *_, t in KG.edges[:BASE_EDGES]] + [False] * (len(KG.edges) - BASE_EDGES)
RETIRED = set(REPLAY.retired_edges)
QUESTIONS = {q.qid: q for q in KG.questions + HOS}


def make_store():
    if os.environ.get("PHYSARUM_STORE") != "memory":
        hs = HydraStore(KG)
        if hs.healthy():
            if not hs.is_loaded():
                hs.load()
            else:  # make sure the 2026 facts exist (idempotent MERGE by id)
                hs.add_entities(
                    [dict(id=i, name=KG.names[i], qid=KG.qids[i]) for i in range(BASE_NAMES, len(KG.names))]
                )
                hs.add_edges(REPLAY.edge_rows)
            try:  # probe: this image must run weighted algo.SSpaths, or we serve from memory
                q0 = HOS[0]
                hs.chain(q0.s, q0.r1, q0.r2)
                return hs
            except Exception as err:  # noqa: BLE001
                print(f"[physarum] HydraDB reachable but path query failed ({err}); using the in-memory store")
    return MemoryStore(KG)


STORE = make_store()
STATIC = Engine(Params(mode="static"))
VERIFIER = make_verifier()
RAG = vector_rag.VectorRAG(KG) if vector_rag.available() else None
PARSER = Parser(KG)
_cache: dict = {}
_cache_lock = threading.Lock()


def question(qid):
    q = QUESTIONS.get(qid)
    if q is None:
        raise HTTPException(404, "unknown question")
    return q


def name(v):
    return KG.names[v]


# ---- path structure cache ----------------------------------------------------------------------
_paths: dict[tuple, list] = {}
_paths_lock = threading.Lock()


_db_edges: dict[int, tuple] = {}  # last learned state read from (or written to) the store, for the shared colony


def remember_paths(q, cands):
    key = (q.s, q.r1, q.r2)
    struct = [(tuple(c.nodes), tuple((e.eid, e.src, e.dst, e.rel) for e in c.edges)) for c in cands]
    with _paths_lock:
        _paths[key] = struct
        for c in cands:
            for e in c.edges:
                _db_edges[e.eid] = (e.R, e.acc, e.rej)
    return struct


def paths(q):
    key = (q.s, q.r1, q.r2)
    with _paths_lock:
        hit = _paths.get(key)
    return hit if hit is not None else remember_paths(q, STORE.chain(q.s, q.r1, q.r2))


def _warm():
    """Fetch the path structure of every built-in question once, in the background, at start-up."""
    t0 = time.time()
    for q in TRANSFER + UNRELATED + HOS_EVAL + HOS + STREAM:
        try:
            paths(q)
        except Exception as err:  # noqa: BLE001
            print(f"[physarum] warm-up stopped: {err}")
            return
    print(f"[physarum] path cache warm: {len(_paths)} questions in {time.time() - t0:.1f}s")
    try:  # frozen-graph and RAG baselines for both worlds, so no visitor waits for them
        s = Session("_warm")
        s.measure()
        s.replay()
        s.measure()
        print(f"[physarum] baselines ready in {time.time() - t0:.1f}s")
    except Exception as err:  # noqa: BLE001
        print(f"[physarum] baseline warm-up failed: {err}")


# warm-up thread is started at the end of this module, once Session is defined


class Session:
    def __init__(self, sid: str, shared: bool = False):
        self.sid, self.shared = sid, shared
        self.lock = threading.RLock()
        self.defer, self.pending = False, {}
        self.engine = Engine(Params(mode="physarum"))
        self.edges: dict[int, EdgeState] = {}  # sandbox: private tube state
        self.registry: dict[int, EdgeState] = {}  # every tube this session has fed or starved
        self.history = collections.defaultdict(lambda: collections.deque(maxlen=8))
        self.last_flux: dict[int, float] = {}
        self.curve: list = []
        self.verdicts = 0
        self.replayed = False
        self.replay_log: list = []
        self.replay_verdicts = 0
        self.snapshot = None
        self.timeline: list = []
        self.rng = np.random.default_rng(1)
        self.touched = time.time()
        z = 1 / np.arange(1, len(STREAM) + 1)
        self.zipf = z / z.sum()
        zh = 1 / np.arange(1, len(HOS) + 1)
        self.zipf_h = zh / zh.sum()
        if shared:
            meta = STORE.load_meta()
            self.engine.load_state(meta)
            self.curve = meta.get("curve") or []
            self.verdicts = int(meta.get("verdicts", 0))
            self.replayed = bool(meta.get("replayed"))

    # ---- graph access -------------------------------------------------------------------
    @property
    def truth_vec(self):
        return TRUTH_2026 if self.replayed else TRUTH_2020

    def _edge(self, e: EdgeState) -> EdgeState:
        st = self.edges.get(e.eid)
        if st is None:
            st = self.edges[e.eid] = EdgeState(e.eid, e.src, e.dst, e.rel)
        return st

    def cands(self, q, live: bool = False):
        """Candidate paths for q, seen through this session's learned state.

        `live=True` (every question a visitor asks) runs algo.SSpaths natively on HydraDB. Bulk work
        (measurement sweeps, simulated agent traffic) reuses each question's path structure, which never
        changes while the demo runs; only the learned tube state does. Sandboxes keep that state privately.
        The shared colony keeps it in HydraDB: it is read from the edges at start-up and every change is
        written back.
        """
        if live:
            native = STORE.chain(q.s, q.r1, q.r2)
            structs = remember_paths(q, native)
        else:
            structs = paths(q)
        out = []
        for nodes, edges in structs:
            if not self.replayed and any(e[0] >= BASE_EDGES for e in edges):
                continue  # 2026 facts don't exist yet in this session's world
            out.append(Candidate(list(nodes), [self._edge_t(*e) for e in edges]))
        return out

    def _edge_t(self, eid, src, dst, rel) -> EdgeState:
        st = self.edges.get(eid)
        if st is None:
            seed = _db_edges.get(eid) if self.shared else None
            st = self.edges[eid] = EdgeState(eid, src, dst, rel, *seed) if seed else EdgeState(eid, src, dst, rel)
        return st

    def truth(self, cands):
        t = self.truth_vec
        return {c.answer for c in cands if all(t[e.eid] for e in c.edges)}

    def write(self, touched):
        if not self.shared:
            return
        touched = list(touched)
        if self.defer:
            self.pending.update({e.eid: e for e in touched})
            return
        STORE.write(self.engine, touched)
        for e in touched:
            _db_edges[e.eid] = (e.R, e.acc, e.rej)

    def flush(self):
        """Write batched changes (agent traffic) to HydraDB in one go."""
        self.defer = False
        if self.pending:
            batch, self.pending = list(self.pending.values()), {}
            self.write(batch)

    def persist(self):
        if self.shared:
            STORE.save_meta(
                {
                    **self.engine.state(),
                    "loaded": True,
                    "curve": self.curve[-200:],
                    "verdicts": self.verdicts,
                    "replayed": self.replayed,
                }
            )

    # ---- measurement ------------------------------------------------------------------
    def accuracy(self, engine, qs):
        if not qs:
            return None
        hit = 0
        for q in qs:
            c = self.cands(q)
            a = engine.answers(c)
            hit += bool(a) and a[0]["answer"] in self.truth(c)
        return round(hit / len(qs), 4)

    def baseline(self, kind, qs, label):
        """Frozen-graph and RAG accuracy depend only on the world (2020 or 2026): cached globally."""
        key = (kind, label, self.replayed)
        with _cache_lock:
            if key in _cache:
                return _cache[key]
        if kind == "static":
            val = self.accuracy(STATIC, qs)
        else:
            val = None
            if RAG is not None:
                hit = 0
                for q in qs:
                    a = RAG.answer(q, hide_from=None if self.replayed else BASE_EDGES)
                    hit += a["answer"] is not None and a["answer"] in self.truth(self.cands(q))
                val = round(hit / len(qs), 4)
        with _cache_lock:
            _cache[key] = val
        return val

    def measure(self):
        pt = dict(
            verdicts=self.verdicts,
            transfer=self.accuracy(self.engine, TRANSFER),
            unrelated=self.accuracy(self.engine, UNRELATED),
            static_transfer=self.baseline("static", TRANSFER, "transfer"),
            static_unrelated=self.baseline("static", UNRELATED, "unrelated"),
            rag_transfer=self.baseline("rag", TRANSFER, "transfer"),
        )
        if self.replayed:
            pt["hos"] = self.accuracy(self.engine, HOS_EVAL)
            pt["static_hos"] = self.baseline("static", HOS_EVAL, "hos")
            pt["rag_hos"] = self.baseline("rag", HOS_EVAL, "hos")
            self.replay_log.append(
                dict(v=self.replay_verdicts, physarum=pt["hos"], static=pt["static_hos"], rag=pt["rag_hos"])
            )
        self.curve.append(pt)
        self.persist()
        return pt

    # ---- views ------------------------------------------------------------------------
    def edge_json(self, e, flux=None):
        v = Engine.edge_view(self.engine, e)
        v.update(
            rel_label=KG.rel_label.get(e.rel, e.rel),
            src_name=name(e.src),
            dst_name=name(e.dst),
            flux=round(abs((flux or {}).get(e.eid, 0.0)), 4),
            suspicion=round(suspicion(e), 3),
            new_2026=e.eid >= BASE_EDGES,
        )
        return v

    def view(self, q, flux=None):
        cands = self.cands(q, live=True)
        ranked = self.engine.answers(cands)
        static = STATIC.answers(cands)
        truth = self.truth(cands)
        edges, nodes = {}, {}
        for c in cands:
            for e in c.edges:
                edges[e.eid] = self.edge_json(e, flux)
            for i, n in enumerate(c.nodes):
                role = "source" if i == 0 else ("answer" if i == len(c.nodes) - 1 else "hop")
                nodes.setdefault(n, dict(id=n, name=name(n), role=role))

        def pack(a):
            return dict(
                answer=a["answer"],
                name=name(a["answer"]),
                confidence=round(a["confidence"], 4),
                path=[name(n) for n in a["path"].nodes],
                edges=[e.eid for e in a["path"].edges],
                resistance=round(a["path"].score, 4),
                correct=a["answer"] in truth,
            )

        top = [pack(a) for a in ranked]
        rag = None
        if RAG is not None:
            r = RAG.answer(q, hide_from=None if self.replayed else BASE_EDGES)
            rag = dict(
                name=name(r["answer"]) if r["answer"] is not None else None,
                correct=r["answer"] is not None and r["answer"] in truth,
                tokens=r["tokens"],
                facts=[RAG.fact_text(e) for e in r["facts"]],
            )
        margin = (top[0]["confidence"] - top[1]["confidence"]) if len(top) > 1 else 1.0
        return dict(
            question=dict(
                qid=q.qid,
                text=q.text,
                source=name(q.s),
                kind="hos" if q.qid >= 1_000_000 and q.qid < 2_000_000 else "codex",
                truth=[name(t) for t in sorted(truth)],
            ),
            answers=top,
            static=pack(static[0]) if static else None,
            rag=rag,
            path_tokens=vector_rag.path_tokens(len(top[0]["edges"])) if top else 0,
            unsure=bool(top) and (margin < 0.25),
            margin=round(margin, 4),
            nodes=list(nodes.values()),
            edges=list(edges.values()),
        )

    # ---- learning ---------------------------------------------------------------------
    def apply(self, q, correct=None, live=False):
        cands = self.cands(q)
        ans = self.engine.answers(cands)
        if not ans:
            return {}
        chosen = ans[0]
        truth = self.truth(cands)
        facts = [(name(e.src), KG.rel_label.get(e.rel, e.rel), name(e.dst)) for e in chosen["path"].edges]
        verdict = (
            VERIFIER(truth, chosen["answer"], path_facts=facts, live=live, as_of=2026 if self.replayed else 2020)
            if correct is None
            else bool(correct)
        )
        fb = self.engine.feedback(cands, q.s, chosen["path"], verdict)
        self.write(fb["touched"].values())
        self.registry.update(fb["touched"])
        self.verdicts += 1
        if self.replayed:
            self.replay_verdicts += 1
        self.last_flux.update({k: abs(v) for k, v in fb["flux"].items()})
        for eid in fb["touched"]:
            self.history[eid].append(dict(n=self.verdicts, ok=verdict, q=q.text))
        return dict(flux=fb["flux"], verdict=verdict, was_right=chosen["answer"] in truth)

    def pick(self):
        """Agent traffic: popular questions (Zipf), a quarter of them about heads of state."""
        if self.rng.random() < 0.25:
            return HOS[self.rng.choice(len(HOS), p=self.zipf_h)]
        return STREAM[self.rng.choice(len(STREAM), p=self.zipf)]

    def replay(self):
        if not self.replayed:
            self.replayed = True
            self.replay_verdicts, self.replay_log = 0, []
        return REPLAY

    def audit(self, limit=40):
        res = run_audit(self.registry.values(), self.truth_vec, limit=limit)
        seen = res["examined"] or 1
        base = res.get("false_facts_seen", 0) / seen
        items = []
        for e in res.pop("items"):
            if e.eid in RETIRED and self.replayed:
                label = "2020 fact, replaced in 2026"
            elif e.eid < BASE_EDGES and not KG.edges[e.eid][3]:
                label = "human-verified FALSE (CoDEx)"
            else:
                label = "true fact (false alarm)"
            items.append(dict(**self.edge_json(e), label=label, is_false=not self.truth_vec[e.eid]))
        res.update(
            items=items,
            base_rate=round(base, 4),
            lift=round(res["precision"] / base, 2) if res.get("precision") and base else None,
        )
        return res

    def snap(self, label):
        """Record the colony (strongest + most-starved tubes) for the time-lapse."""
        strength = lambda e: self.engine.conductivity(e) * (1 + e.acc) / (1 + e.rej)
        fed = sorted(self.registry.values(), key=lambda e: -strength(e))[:180]
        starved = sorted((e for e in self.registry.values() if e.rej > e.acc), key=lambda e: -e.rej)[:60]
        es = list({e.eid: e for e in fed + starved}.values())
        self.timeline.append(
            dict(
                v=self.verdicts,
                label=label,
                edges=[
                    dict(
                        eid=e.eid,
                        src=e.src,
                        dst=e.dst,
                        c=round(self.engine.conductivity(e), 3),
                        acc=round(e.acc, 2),
                        rej=round(e.rej, 2),
                        r=round(self.engine.resistance(e), 3),
                        new=e.eid >= BASE_EDGES,
                    )
                    for e in es
                ],
            )
        )
        del self.timeline[:-60]

    def wipe(self):
        self.snapshot = dict(
            scale=self.engine.scale, edges={eid: (e.R, e.acc, e.rej) for eid, e in self.registry.items()}
        )
        for e in self.registry.values():
            e.R, e.acc, e.rej = 0.0, 0.0, 0.0
        if self.shared:
            STORE.reset(list(self.registry.values()))
            for eid in self.registry:
                _db_edges[eid] = (0.0, 0.0, 0.0)
        self.engine.scale = 1.0
        return len(self.snapshot["edges"])

    def restore(self):
        if not self.snapshot:
            raise HTTPException(400, "nothing to restore")
        self.engine.scale = self.snapshot["scale"]
        for eid, (R, acc, rej) in self.snapshot["edges"].items():
            e = self.registry[eid]
            e.R, e.acc, e.rej = R, acc, rej
        self.write(self.registry.values())
        self.snapshot = None
        self.persist()


# ---- session registry ---------------------------------------------------------------------
MAX_SESSIONS, IDLE_SECONDS = 60, 3 * 3600
_sessions: collections.OrderedDict[str, Session] = collections.OrderedDict()
_sessions_lock = threading.Lock()


def get_session(sid: str | None, shared: bool = False) -> Session:
    key = "shared" if shared else (sid or "anonymous")[:64]
    with _sessions_lock:
        s = _sessions.get(key)
        if s is None:
            s = _sessions[key] = Session(key, shared=shared)
            traction.bump("visitors")
        _sessions.move_to_end(key)
        s.touched = time.time()
        now = time.time()
        for k in [k for k, v in _sessions.items() if k != "shared" and (now - v.touched > IDLE_SECONDS)]:
            _sessions.pop(k, None)
        while len(_sessions) > MAX_SESSIONS:
            oldest = next(k for k in _sessions if k != "shared")
            _sessions.pop(oldest)
        return s


def reset_session(s: Session) -> Session:
    with _sessions_lock:
        if s.shared:
            STORE.reset()
            STORE.save_meta({"loaded": True})
            with _paths_lock:
                _db_edges.clear()
        fresh = _sessions[s.sid] = Session(s.sid, shared=s.shared)
        return fresh


def session_count() -> int:
    return len(_sessions)


threading.Thread(target=_warm, daemon=True).start()  # needs Session, so it starts last
