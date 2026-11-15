"""Ask anything: map a plain-English question onto a graph question, no LLM required.

"What continent was Freddie Mercury born on?" -> entity Freddie Mercury, chain place of birth -> continent.
Entity = longest known name in the text. Target relation = keyword match. The first hop is inferred
from hints (born / died / lives / label / spouse ...) and defaults to country of citizenship, keeping
the first chain that actually exists in the graph.
"""

from __future__ import annotations

import re
import unicodedata

from .data import Question

TARGET = [  # (keywords, relation) — order matters: first match wins
    (("head of state", "president", "king", "queen", "monarch", "ruler", "pope"), "P35"),
    (("continent",), "P30"),
    (("official language", "language", "speak"), "P37"),
    (("currency", "money"), "P38"),
    (("capital",), "P36"),
    (("ethnic",), "P172"),
    (("parent organi", "parent company", "owns", "owner"), "P749"),
    (("founded", "founder"), "P112"),
    (("formed",), "P740"),
    (("headquarter",), "P159"),
    (("member of", "organisation", "organization", "alliance"), "P463"),
    (("part of",), "P361"),
]
FIRST = [
    (("born", "birth"), ["P19"]),
    (("died", "death", "die "), ["P20"]),
    (("live", "lives", "living", "residence", "reside"), ["P551"]),
    (("record label", "label", "signed"), ["P264"]),
    (("spouse", "wife", "husband", "married"), ["P26"]),
    (("sibling", "brother", "sister"), ["P3373"]),
    (("employer", "work for", "works for", "worked for"), ["P108"]),
    (("influenced",), ["P737"]),
    (("member of", "organisation"), ["P463"]),
]
COUNTRY_WORDS = ("country", "citizenship", "nationality", "nation")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


class Parser:
    def __init__(self, kg):
        self.kg = kg
        self.names = {}
        for i, n in enumerate(kg.names):
            k = _norm(n)
            if len(k) >= 3 and k not in self.names:
                self.names[k] = i
        self.by_len = sorted(self.names, key=len, reverse=True)

    def entity(self, text: str):
        t = " " + _norm(text).replace(" s ", " ") + " "
        for k in self.by_len:
            if f" {k} " in t:
                return self.names[k], k
        return None, None

    def parse(self, text: str, store):
        low = " " + text.lower() + " "
        s, matched = self.entity(text)
        if s is None:
            return (
                None,
                "I couldn't find a person or place from the graph in that question. Try a name like Freddie Mercury or A. A. Milne.",
            )
        target = next((r for kws, r in TARGET if any(k in low for k in kws)), None)
        firsts = next((r for kws, r in FIRST if any(k in low for k in kws)), None) or []
        if target is None:
            if any(w in low for w in COUNTRY_WORDS):  # "which country was X born in" -> place of birth -> country
                target = "P17"
            else:
                return (
                    None,
                    "I found the entity, but not what you're asking about. Try: head of state, continent, language, capital, currency, country…",
                )
        if target == "P17" and not firsts:
            firsts = ["P19", "P551", "P159", "P108"]
        chains = [(r1, target) for r1 in firsts + ["P27", "P17", "P19", "P264"] if r1 != target]
        for r1, r2 in chains:
            if store.chain(s, r1, r2):
                qid = 2_000_000 + s * 100 + abs(hash((r1, r2))) % 97
                shown = text.strip().rstrip("?") + "?"
                q = Question(qid, s, r1, r2, frozenset(), shown[0].upper() + shown[1:])
                interp = f"{self.kg.names[s]} → {self.kg.rel_label.get(r1, r1)} → {self.kg.rel_label.get(r2, r2)}"
                return q, interp
        return None, f"The graph has no path for that about {self.kg.names[s]}. Try another relation."
