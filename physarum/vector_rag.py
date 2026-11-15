"""Vector RAG baseline: what most agent stacks do today.

Every fact is embedded as a sentence ("Canada · head of state · Elizabeth II"). For a question we
retrieve the top-k facts by cosine similarity, then a *generous* two-hop reader (it is told which
relation to follow at each hop, and re-retrieves for hop 2) picks the most similar matching fact.

Embeddings: OpenAI `text-embedding-3-small` when OPENAI_API_KEY is set and PHYSARUM_EMBED=openai,
otherwise spaCy `en_core_web_md` 300-d vectors (mean of word vectors). Similarity has no notion of
truth or time, which is the point being measured.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

import numpy as np

_TOKEN = re.compile(r"[A-Za-z0-9]+")


class Embedder:
    def __init__(self):
        self.kind = None
        if os.environ.get("PHYSARUM_EMBED") == "openai" and os.environ.get("OPENAI_API_KEY"):
            self.kind, self.name, self.dim = "openai", "text-embedding-3-small", 1536
        else:
            import spacy  # noqa: WPS433 (optional dependency)

            self.nlp = spacy.load(
                "en_core_web_md",
                exclude=["tagger", "parser", "ner", "lemmatizer", "attribute_ruler", "senter", "tok2vec"],
            )
            self.kind, self.name, self.dim = "spacy", "spaCy en_core_web_md (300-d)", 300
            self._cache = {}

    def _word(self, w):
        v = self._cache.get(w)
        if v is None:
            lex = self.nlp.vocab[w]
            v = lex.vector if lex.has_vector else None
            if v is None and w.lower() != w:
                lex = self.nlp.vocab[w.lower()]
                v = lex.vector if lex.has_vector else None
            self._cache[w] = v
        return v

    def encode(self, texts: list[str]) -> np.ndarray:
        if self.kind == "openai":
            out = []
            for i in range(0, len(texts), 512):
                req = urllib.request.Request(
                    "https://api.openai.com/v1/embeddings",
                    method="POST",
                    data=json.dumps({"model": self.name, "input": texts[i : i + 512]}).encode(),
                    headers={
                        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    out += [d["embedding"] for d in json.loads(r.read())["data"]]
            m = np.array(out, dtype=np.float32)
        else:
            m = np.zeros((len(texts), self.dim), dtype=np.float32)
            for i, t in enumerate(texts):
                vs = [v for v in (self._word(w) for w in _TOKEN.findall(t)) if v is not None]
                if vs:
                    m[i] = np.mean(vs, axis=0)
        n = np.linalg.norm(m, axis=1, keepdims=True)
        n[n == 0] = 1
        return m / n


def available() -> bool:
    try:
        import spacy  # noqa: F401

        spacy.util.get_package_path("en_core_web_md")
        return True
    except Exception:
        return bool(os.environ.get("PHYSARUM_EMBED") == "openai" and os.environ.get("OPENAI_API_KEY"))


class VectorRAG:
    """Hybrid RAG: dense vectors + TF-IDF keywords, fused with reciprocal rank fusion (the usual
    strong production baseline). mode: 'hybrid' (default), 'vector' or 'keyword'."""

    def __init__(self, kg, k: int = 10, mode: str = "hybrid"):
        self.kg, self.k, self.mode = kg, k, mode
        self.emb = Embedder()
        label = {"hybrid": "hybrid RAG (vector + keyword)", "vector": "vector RAG", "keyword": "keyword RAG (TF-IDF)"}[
            mode
        ]
        self.name = f"{label} · {self.emb.name} · top-{k}"
        self.mat = np.zeros((0, self.emb.dim), dtype=np.float32)
        self._tf_n = -1
        self.sync()

    def fact_text(self, eid):
        s, d, r, _ = self.kg.edges[eid]
        return f"{self.kg.names[s]} {self.kg.rel_label.get(r, r)} {self.kg.names[d]}"

    def sync(self):
        """Embed facts added since last time (e.g. the 2026 replay); drop rows if facts were removed."""
        n = len(self.kg.edges)
        if n < len(self.mat):
            self.mat = self.mat[:n]
        if n > len(self.mat):
            new = self.emb.encode([self.fact_text(e) for e in range(len(self.mat), n)])
            self.mat = np.vstack([self.mat, new])
        if self.mode != "vector" and self._tf_n != n:  # re-index keywords on change
            from sklearn.feature_extraction.text import TfidfVectorizer

            texts = [self.fact_text(e) for e in range(n)]
            self.tf = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True).fit(texts)
            self.tfm = self.tf.transform(texts)
            self._tf_n = n

    @staticmethod
    def _top(scores, k):
        top = np.argpartition(-scores, k)[:k]
        return [int(i) for i in top[np.argsort(-scores[top])]]

    def retrieve(self, text: str, k: int | None = None):
        self.sync()
        k = k or self.k
        lists = []
        if self.mode in ("hybrid", "vector"):
            lists.append(self._top(self.mat @ self.emb.encode([text])[0], max(k, 50)))
        if self.mode in ("hybrid", "keyword"):
            lists.append(self._top((self.tfm @ self.tf.transform([text]).T).toarray().ravel(), max(k, 50)))
        if len(lists) == 1:
            return lists[0][:k]
        fused = {}
        for lst in lists:  # reciprocal rank fusion, k0 = 60
            for rank, e in enumerate(lst):
                fused[e] = fused.get(e, 0.0) + 1.0 / (60 + rank)
        return sorted(fused, key=fused.get, reverse=True)[:k]

    def answer(self, q, hide_from: int | None = None):
        """Two-hop reader over retrieved facts. `hide_from`: ignore facts with eid >= this (not yet in the world)."""
        kg = self.kg
        ok = (lambda e: e < hide_from) if hide_from is not None else (lambda e: True)
        hop1 = [e for e in self.retrieve(q.text) if ok(e) and kg.edges[e][0] == q.s and kg.edges[e][2] == q.r1]
        if not hop1:
            return dict(answer=None, facts=[], tokens=self._tokens(self.k))
        m = kg.edges[hop1[0]][1]
        hop2_q = f"{kg.names[m]} {kg.rel_label.get(q.r2, q.r2)}"
        hop2 = [e for e in self.retrieve(hop2_q) if ok(e) and kg.edges[e][0] == m and kg.edges[e][2] == q.r2]
        if not hop2:
            return dict(answer=None, facts=[hop1[0]], tokens=self._tokens(2 * self.k))
        return dict(answer=kg.edges[hop2[0]][1], facts=[hop1[0], hop2[0]], tokens=self._tokens(2 * self.k))

    def _tokens(self, n_facts):
        return int(n_facts * 9)  # ≈ tokens per fact sentence (avg ~7 words → ~9 tokens)


def path_tokens(n_facts: int) -> int:
    return int(n_facts * 9)
