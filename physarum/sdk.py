"""Physarum SDK: a self-healing knowledge graph for any app, in three lines.

    from physarum import Graph
    g = Graph.from_csv("facts.csv")                 # subject,relation,object  (or Graph.from_triples([...]))
    a = g.ask("Bob", "reports_to")                  # follow one or more relations
    g.feedback(a, correct=False)                    # one bit; the graph learns immediately

Backends: in-memory (default) or HydraDB (`hydradb_url=...`), where the learned state lives on the
edges in object storage and is shared by every process that reads the graph.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field

from .audit import audit as _audit
from .data import KG
from .engine import Candidate, Engine, Params
from .store import HydraStore, MemoryStore


@dataclass
class Answer:
    answer: str | None
    path: list[str]
    confidence: float
    unsure: bool
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    _cands: list = field(default_factory=list, repr=False)
    _source: int = 0
    _chosen: Candidate | None = field(default=None, repr=False)


class Graph:
    def __init__(self, kg: KG, hydradb_url: str | None = None, token: str | None = None, params: Params | None = None):
        self.kg = kg
        self.index = {n.lower(): i for i, n in enumerate(kg.names)}
        if hydradb_url:
            self.store = HydraStore(kg, url=hydradb_url, token=token)
            if not self.store.is_loaded():
                self.store.load()
        else:
            self.store = MemoryStore(kg)
        self.engine = Engine(params or Params())
        self.seen = {}

    # ---- construction --------------------------------------------------------
    @classmethod
    def from_triples(cls, triples, **kw) -> Graph:
        """triples: iterable of (subject, relation, object) strings."""
        names, idx, edges, rels = [], {}, [], {}
        for s, r, o in triples:
            for n in (s, o):
                if n not in idx:
                    idx[n] = len(names)
                    names.append(n)
            rid = rels.setdefault(r, "R" + "".join(ch for ch in r.upper() if ch.isalnum())[:40] or f"R{len(rels)}")
            edges.append((idx[s], idx[o], rid, True))
        kg = KG(
            names=names, qids=[""] * len(names), rel_label={v: k for k, v in rels.items()}, edges=edges, questions=[]
        )
        g = cls(kg, **kw)
        g.rel_id = rels
        return g

    @classmethod
    def from_csv(cls, path, **kw) -> Graph:
        with open(path, newline="") as f:
            rows = [r for r in csv.reader(f) if r and not r[0].startswith("#")]
        if rows and [c.strip().lower() for c in rows[0][:3]] == ["subject", "relation", "object"]:
            rows = rows[1:]
        return cls.from_triples(((a.strip(), b.strip(), c.strip()) for a, b, c, *_ in rows), **kw)

    # ---- use -----------------------------------------------------------------
    def _rid(self, r):
        return getattr(self, "rel_id", {}).get(r, r)

    def ask(self, subject: str, *relations: str) -> Answer:
        s = self.index.get(subject.lower())
        if s is None:
            raise KeyError(f"unknown entity: {subject}")
        cands = self.store.chain_rels(s, [self._rid(r) for r in relations])
        for c in cands:
            for e in c.edges:
                self.seen[e.eid] = e
        ranked = self.engine.answers(cands)
        if not ranked:
            return Answer(None, [], 0.0, True, _cands=cands, _source=s)
        top = ranked[0]
        margin = top["confidence"] - (ranked[1]["confidence"] if len(ranked) > 1 else 0)
        return Answer(
            self.kg.names[top["answer"]],
            [self.kg.names[n] for n in top["path"].nodes],
            round(top["confidence"], 4),
            margin < 0.25,
            [(self.kg.names[a["answer"]], round(a["confidence"], 4)) for a in ranked[1:]],
            _cands=cands,
            _source=s,
            _chosen=top["path"],
        )

    def feedback(self, answer: Answer, correct: bool) -> Answer:
        """One bit about an answer. Returns the answer the graph would give now."""
        if answer._chosen is None:
            return answer
        fb = self.engine.feedback(answer._cands, answer._source, answer._chosen, correct)
        self.store.write(self.engine, fb["touched"].values())
        rels = [self.kg.rel_label.get(e.rel, e.rel) for e in answer._chosen.edges]
        return self.ask(self.kg.names[answer._source], *rels)

    def add(self, subject: str, relation: str, obj: str):
        """Append a new fact (old facts stay, like a real ingestion pipeline)."""
        for n in (subject, obj):
            if n.lower() not in self.index:
                self.index[n.lower()] = len(self.kg.names)
                self.kg.names.append(n)
                self.kg.qids.append("")
                self.store.add_entities([dict(id=self.index[n.lower()], name=n, qid="")])
        rid = self._rid(relation)
        if not hasattr(self, "rel_id"):
            self.rel_id = {}
        self.rel_id.setdefault(relation, rid)
        self.kg.rel_label.setdefault(rid, relation)
        eid = len(self.kg.edges)
        self.kg.edges.append((self.index[subject.lower()], self.index[obj.lower()], rid, True))
        self.store.add_edges([dict(s=self.index[subject.lower()], d=self.index[obj.lower()], rel=rid, e=eid)])

    def audit(self):
        """Facts the graph has stopped trusting, most suspicious first."""
        res = _audit(self.seen.values())
        return [
            (
                self.kg.names[e.src],
                self.kg.rel_label.get(e.rel, e.rel),
                self.kg.names[e.dst],
                round(e.rej, 2),
                round(e.acc, 2),
            )
            for e in res["items"]
        ]
