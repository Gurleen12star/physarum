"""Browser playground: anyone pastes their own facts and gets a private self-healing graph.

Each visitor's graph is an SDK `Graph` (in-memory, private to their session, capped in size).
"""

from __future__ import annotations

import collections
import csv
import io
import itertools
import threading
import time

from .sdk import Graph

MAX_FACTS, MAX_GRAPHS, IDLE_SECONDS = 5000, 80, 2 * 3600
MAX_VIEW_EDGES = 220  # the diagram shows at most this many facts (a neighbourhood, for big graphs)

EXAMPLE = """subject,relation,object
Bob,reports_to,Carol
Bob,reports_to,Dana
Erin,reports_to,Carol
Frank,reports_to,Dana
Carol,leads,Payments
Dana,leads,Growth
Payments,based_in,London
Growth,based_in,Berlin
Payments,uses,Stripe
Growth,uses,Amplitude
"""
EXAMPLE_NOTE = "Bob moved from Carol's team to Dana's last week; both facts are still in the data."


class Playground:
    def __init__(self, graph: Graph, n_facts: int):
        self.g, self.n_facts = graph, n_facts
        self.answers: dict[int, object] = {}
        self.ids = itertools.count(1)
        self.votes = 0
        self.lock = threading.RLock()
        self.touched = time.time()

    @classmethod
    def from_text(cls, text: str) -> Playground:
        rows = [r for r in csv.reader(io.StringIO(text.strip())) if len(r) >= 3 and not r[0].lstrip().startswith("#")]
        if rows and [c.strip().lower() for c in rows[0][:3]] == ["subject", "relation", "object"]:
            rows = rows[1:]
        triples = [(a.strip(), b.strip(), c.strip()) for a, b, c, *_ in rows if a.strip() and b.strip() and c.strip()]
        if not triples:
            raise ValueError("No facts found. Use one fact per line: subject,relation,object")
        if len(triples) > MAX_FACTS:
            raise ValueError(f"The playground takes up to {MAX_FACTS:,} facts; use the SDK for bigger graphs.")
        return cls(Graph.from_triples(triples), len(triples))

    # ---- views ------------------------------------------------------------------------
    def schema(self):
        g = self.g
        rels = sorted(set(g.rel_id))
        out_rels = collections.defaultdict(set)
        for s, _d, r, _t in g.kg.edges:
            out_rels[g.kg.names[s]].add(g.kg.rel_label.get(r, r))
        subjects = sorted(out_rels)
        return dict(
            facts=self.n_facts,
            entities=len(g.kg.names),
            relations=rels,
            subjects=subjects[:2000],
            out_rels={k: sorted(v) for k, v in out_rels.items()},
            votes=self.votes,
        )

    def pack(self, a):
        aid = next(self.ids)
        self.answers[aid] = a
        if len(self.answers) > 200:
            self.answers.pop(next(iter(self.answers)))
        return dict(
            id=aid,
            answer=a.answer,
            path=a.path,
            confidence=a.confidence,
            unsure=a.unsure,
            alternatives=[dict(name=n, confidence=c) for n, c in a.alternatives[:5]],
            # edge ids, so the diagram can light up the chosen path and the other candidate paths
            path_edges=[e.eid for e in a._chosen.edges] if a._chosen is not None else [],
            cand_edges=sorted({e.eid for c in a._cands for e in c.edges}),
        )

    def graph_view(self, focus: str | None = None, extra=()):
        """Nodes and edges with their learned tube state, for the diagram.

        Small graphs are returned whole. Big ones are cut to the neighbourhood of `focus` (plus the
        edges in `extra` and every edge that has received a vote), so the picture stays readable.
        """
        g, eng, names = self.g, self.g.engine, self.g.kg.names
        edges = [e for e in g.store.edges if e is not None]
        total = len(edges)
        if total > MAX_VIEW_EDGES:
            by_id = {e.eid: e for e in edges}
            keep = {}
            for eid in extra:
                if eid in by_id:
                    keep[eid] = by_id[eid]
            for e in g.seen.values():
                if (e.acc or e.rej) and len(keep) < MAX_VIEW_EDGES // 2:
                    keep[e.eid] = e
            adj = collections.defaultdict(list)
            for e in edges:
                adj[e.src].append(e)
                adj[e.dst].append(e)
            start = g.index.get((focus or "").lower())
            if start is None:
                start = max(adj, key=lambda n: len(adj[n]))
            seen_nodes, frontier = {start}, collections.deque([start])
            while frontier and len(keep) < MAX_VIEW_EDGES:
                n = frontier.popleft()
                for e in adj[n]:
                    if len(keep) >= MAX_VIEW_EDGES:
                        break
                    keep.setdefault(e.eid, e)
                    for m in (e.src, e.dst):
                        if m not in seen_nodes:
                            seen_nodes.add(m)
                            frontier.append(m)
            edges = list(keep.values())
        node_ids = sorted({n for e in edges for n in (e.src, e.dst)})
        has_out = {e.src for e in g.store.edges if e is not None}

        def state(e, d):
            if e.rej > e.acc and e.rej >= 0.5:
                return "bad"
            if e.rej > e.acc:
                return "weak"
            if d > 1.05 or e.acc > e.rej:
                return "fed"
            return "idle"

        out_edges = []
        for e in edges:
            d = eng.conductivity(e)
            out_edges.append(
                dict(
                    id=e.eid,
                    s=e.src,
                    t=e.dst,
                    rel=g.kg.rel_label.get(e.rel, e.rel),
                    D=round(d, 3),
                    L=round(eng.length(e), 3),
                    acc=round(e.acc, 2),
                    rej=round(e.rej, 2),
                    state=state(e, d),
                )
            )
        return dict(
            nodes=[dict(id=n, name=names[n], out=n in has_out) for n in node_ids],
            edges=out_edges,
            total_edges=total,
            total_nodes=len(names),
            truncated=total > len(edges),
        )

    # ---- actions ----------------------------------------------------------------------
    def ask(self, subject: str, relations: list[str]):
        return self.pack(self.g.ask(subject, *relations))

    def feedback(self, aid: int, correct: bool):
        a = self.answers.get(aid)
        if a is None:
            raise KeyError("answer expired; ask again")
        self.votes += 1
        return self.pack(self.g.feedback(a, correct))

    def add(self, s: str, r: str, o: str):
        if self.n_facts >= MAX_FACTS:
            raise ValueError("playground limit reached")
        self.g.add(s, r, o)
        self.n_facts += 1

    def audit(self):
        return [dict(fact=f"{s} — {r} → {o}", rej=rej, acc=acc) for s, r, o, rej, acc in self.g.audit()]


_graphs: collections.OrderedDict[str, Playground] = collections.OrderedDict()
_lock = threading.Lock()


def get(sid: str) -> Playground | None:
    with _lock:
        p = _graphs.get(sid)
        if p:
            p.touched = time.time()
            _graphs.move_to_end(sid)
        return p


def put(sid: str, p: Playground):
    with _lock:
        _graphs[sid] = p
        _graphs.move_to_end(sid)
        now = time.time()
        for k in [k for k, v in _graphs.items() if now - v.touched > IDLE_SECONDS]:
            _graphs.pop(k, None)
        while len(_graphs) > MAX_GRAPHS:
            _graphs.popitem(last=False)
