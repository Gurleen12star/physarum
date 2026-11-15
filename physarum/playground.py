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
