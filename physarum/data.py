"""CoDEx-S (Safavi & Koutra, EMNLP 2020): a Wikidata KG with human-verified hard negatives.

True triples become trusted edges. Hard negatives -- plausible facts that annotators
verified as FALSE -- are loaded as edges too, playing the hallucinations an LLM
extractor would write into a real knowledge graph. The database never learns which
is which; only the (noisy) verifier holds ground truth.
"""

from __future__ import annotations

import collections
import json
import random
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Question:
    qid: int
    s: int
    r1: str
    r2: str
    truth: frozenset  # correct answers
    text: str
    _fake: frozenset = frozenset()  # answers reachable only through a false edge


@dataclass
class KG:
    names: list[str]  # vertex id -> label
    qids: list[str]
    rel_label: dict[str, str]
    edges: list[tuple[int, int, str, bool]]  # (src, dst, rel, is_true) ; eid = index
    questions: list[Question]

    def describe(self, q: Question) -> str:
        return q.text


TEMPLATES = {
    ("P27", "P30"): "Which continent is {s}'s country of citizenship on?",
    ("P27", "P37"): "What is the official language of {s}'s country of citizenship?",
    ("P27", "P172"): "Which ethnic group lives in {s}'s country of citizenship?",
    ("P27", "P35"): "Who is the head of state of {s}'s country of citizenship?",
    ("P27", "P361"): "What is {s}'s country of citizenship part of?",
    ("P27", "P463"): "Which organisation is {s}'s country of citizenship a member of?",
    ("P27", "P38"): "What currency is used in {s}'s country of citizenship?",
    ("P27", "P36"): "What is the capital of {s}'s country of citizenship?",
    ("P19", "P17"): "In which country was {s} born?",
    ("P19", "P30"): "On which continent was {s} born?",
    ("P20", "P17"): "In which country did {s} die?",
    ("P264", "P17"): "Which country is {s}'s record label from?",
    ("P264", "P749"): "What is the parent organisation of {s}'s record label?",
    ("P264", "P112"): "Who founded {s}'s record label?",
    ("P264", "P740"): "Where was {s}'s record label formed?",
    ("P264", "P159"): "Where is {s}'s record label headquartered?",
    ("P20", "P30"): "On which continent did {s} die?",
    ("P551", "P17"): "In which country does {s} live?",
    ("P551", "P30"): "On which continent does {s} live?",
    ("P463", "P17"): "Which country is home to an organisation {s} is a member of?",
    ("P17", "P30"): "On which continent is {s}'s country?",
    ("P17", "P37"): "What is the official language of {s}'s country?",
    ("P26", "P27"): "What is the citizenship of {s}'s spouse?",
    ("P3373", "P27"): "What is the citizenship of {s}'s sibling?",
    ("P108", "P17"): "In which country is {s}'s employer?",
    ("P159", "P17"): "In which country is {s} headquartered?",
    ("P737", "P27"): "What was the citizenship of someone who influenced {s}?",
}


def _text(kg_rel: dict, names, s, r1, r2):
    t = TEMPLATES.get((r1, r2))
    if t:
        return t.format(s=names[s])
    return f"{names[s]} → {kg_rel[r1]} → {kg_rel[r2]} → ?"


def load_codex() -> KG:
    rel = {k: v["label"] for k, v in json.loads((DATA / "relations.json").read_text()).items()}
    labels = json.loads((DATA / "entity_labels.json").read_text())

    def read(f):
        return [tuple(line.split()) for line in (DATA / f).read_text().splitlines() if line.strip()]

    true = read("train.txt") + read("valid.txt") + read("test.txt")
    neg = read("valid_negatives.txt") + read("test_negatives.txt")
    qs = sorted({x for h, _, t in true + neg for x in (h, t)})
    vid = {q: i for i, q in enumerate(qs)}
    names = [labels.get(q, q) for q in qs]
    edges, seen = [], set()
    for trip, ok in [(t, True) for t in true] + [(t, False) for t in neg]:
        key = (vid[trip[0]], vid[trip[2]], trip[1])
        if key in seen:
            continue
        seen.add(key)
        edges.append((key[0], key[1], key[2], ok))

    out = collections.defaultdict(list)
    for s, d, r, ok in edges:
        out[s].append((r, d, ok))
    questions = []
    for s in sorted(out):
        by_r1 = collections.defaultdict(list)
        for r, m, ok in out[s]:
            by_r1[r].append((m, ok))
        for r1 in sorted(by_r1):
            ms = by_r1[r1]
            r2s = sorted({r2 for m, _ in ms for r2, _, _ in out.get(m, [])})
            for r2 in r2s:
                truth, fake = set(), set()
                for m, ok1 in ms:
                    for rr, t, ok2 in out.get(m, []):
                        if rr == r2:
                            (truth if ok1 and ok2 else fake).add(t)
                fake -= truth
                if (r1, r2) in TEMPLATES and truth and fake and len(truth) <= 3 and s not in truth:
                    questions.append(
                        Question(
                            len(questions), s, r1, r2, frozenset(truth), _text(rel, names, s, r1, r2), frozenset(fake)
                        )
                    )
    return KG(names, qs, rel, edges, questions)


def split(kg: KG, seed: int = 0, n_stream: int = 400, n_heldout: int = 300):
    """Stream = questions agents keep asking (Zipf-popular).
    Held-out 'transfer' = never-asked questions that share the first hop (same person, same
    relation) with a stream question: fixing a fact once should fix them too.
    Held-out 'unrelated' = never-asked questions about other people: learning must not hurt them."""
    rng = random.Random(seed)
    qs = list(kg.questions)
    rng.shuffle(qs)
    stream, used_keys = [], set()
    for q in qs:
        if (q.s, q.r1) in used_keys:
            continue
        stream.append(q)
        used_keys.add((q.s, q.r1))
        if len(stream) >= n_stream:
            break
    stream_ids = {q.qid for q in stream}
    stream_subjects = {q.s for q in stream}
    transfer = [q for q in qs if q.qid not in stream_ids and (q.s, q.r1) in used_keys][:n_heldout]
    unrelated = [q for q in qs if q.s not in stream_subjects][:n_heldout]
    return stream, transfer, unrelated


class World:
    """Ground truth lives on edges, as in the real world. An answer is right iff some
    candidate path to it uses only currently-true facts. Changing the world flips facts,
    so every question touching a changed fact changes consistently."""

    def __init__(self, kg: KG):
        self.kg = kg
        self.edge_truth = [t for *_, t in kg.edges]

    def truth(self, cands) -> set:
        return {c.answer for c in cands if all(self.edge_truth[e.eid] for e in c.edges)}

    def change(self, store, questions, n: int, rng: random.Random):
        """For up to n questions: one alternative path becomes true and the edge(s) it
        replaces become false (e.g. a label moved its headquarters, a person changed citizenship)."""
        changed = []
        for q in questions:
            if len(changed) >= n:
                break
            cands = store.chain(q.s, q.r1, q.r2)
            now = self.truth(cands)
            alt = [c for c in cands if c.answer not in now and any(not self.edge_truth[e.eid] for e in c.edges)]
            old = [c for c in cands if c.answer in now]
            if not alt or not old:
                continue
            new = rng.choice(alt)
            for i, e in enumerate(new.edges):
                if self.edge_truth[e.eid]:
                    continue
                self.edge_truth[e.eid] = True  # the new fact becomes true...
                for c in old:  # ...and replaces the old one
                    o = c.edges[i]
                    if o.src == e.src and o.rel == e.rel and o.eid != e.eid:
                        self.edge_truth[o.eid] = False
            if self.truth(cands) != now:
                changed.append(q)
        return changed
