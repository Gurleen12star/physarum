"""Replay what really changed in the world between the CoDEx snapshot (~2020) and 2026.

Append-only, like a real ingestion pipeline: the new fact is written into the graph, the old
one is NOT deleted (extractors rarely know a fact was superseded). Ground truth moves to the
new edge. A frozen graph cannot tell which head of state is current; Physarum has to learn it
from 👍/👎 on its own answers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .data import DATA, KG, Question, World, _text

REL = "P35"


@dataclass
class ReplayResult:
    events: list
    questions: list  # head-of-state questions whose answer changed
    new_edges: list  # eids added
    retired_edges: list  # eids that stopped being true
    edge_rows: list = None  # rows written to the store (for undo)


def load_changes(path: Path | None = None) -> dict:
    return json.loads((path or DATA / "real_changes.json").read_text())


def head_of_state_questions(kg: KG, countries: set[int]) -> list[Question]:
    """Every 'Who is the head of state of X's country of citizenship?' question touching `countries`."""
    out, seen = [], set()
    by_src = {}
    for s, d, r, _ in kg.edges:
        by_src.setdefault(s, []).append((d, r))
    for s, outs in by_src.items():
        cits = [d for d, r in outs if r == "P27" and d in countries]
        if not cits or s in seen:
            continue
        seen.add(s)
        qid = 1_000_000 + s
        out.append(Question(qid, s, "P27", REL, frozenset(), _text(kg.rel_label, kg.names, s, "P27", REL)))
    return sorted(out, key=lambda q: kg.names[q.s])


def countries_of(kg: KG, changes: dict | None = None) -> set[int]:
    changes = changes or load_changes()
    qid_to_vid = {q: i for i, q in enumerate(kg.qids)}
    return {qid_to_vid[c] for ch in changes["changes"] for c in ch["countries"] if c in qid_to_vid}


def apply(kg: KG, world: World, store, changes: dict | None = None) -> ReplayResult:
    changes = changes or load_changes()
    qid_to_vid = {q: i for i, q in enumerate(kg.qids)}
    events, new_edges, retired, countries = [], [], [], set()
    new_entities, new_edge_rows = [], []
    for ch in changes["changes"]:
        old_vid = qid_to_vid.get(ch["old"])
        name = ch["new"]["name"]
        key = ch["new"].get("qid") or f"new:{name}"
        if key in qid_to_vid:
            new_vid = qid_to_vid[key]
        else:
            new_vid = len(kg.names)
            kg.names.append(name)
            kg.qids.append(key)
            qid_to_vid[key] = new_vid
            new_entities.append(dict(id=new_vid, name=name, qid=ch["new"].get("qid") or ""))
        touched = []
        for cq in ch["countries"]:
            c = qid_to_vid.get(cq)
            if c is None:
                continue
            countries.add(c)
            for eid, (s, d, r, _) in enumerate(kg.edges):
                if s == c and r == REL and d == old_vid and world.edge_truth[eid]:
                    world.edge_truth[eid] = False
                    retired.append(eid)
            eid = len(kg.edges)
            kg.edges.append((c, new_vid, REL, True))
            world.edge_truth.append(True)
            new_edges.append(eid)
            new_edge_rows.append(dict(s=c, d=new_vid, rel=REL, e=eid))
            touched.append(kg.names[c])
        events.append(
            dict(
                event=ch["event"],
                date=ch["date"],
                new=name,
                old=kg.names[old_vid] if old_vid is not None else ch["old"],
                countries=touched,
                source=ch.get("source"),
            )
        )
    if store is not None and new_entities:
        store.add_entities(new_entities)
    if store is not None and new_edge_rows:
        store.add_edges(new_edge_rows)
    qs = head_of_state_questions(kg, countries)
    return ReplayResult(events, qs, new_edges, retired, new_edge_rows)
