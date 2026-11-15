"""
Physarum engine: a knowledge graph that sharpens itself from answer-level feedback.

Model (Tero, Kobayashi & Nakagaki 2007; Tero et al., Science 2010)
-------------------------------------------------------------------
Every edge is a tube with
  length      L_e  -- how hostile the terrain is. Tero modelled mountains with light;
                     we use accumulated evidence: L_e = 1 + LIGHT * (rej+.5)/(acc+rej+1)
  conductivity D_e -- tube diameter, grown by flux: D_e = 1 + R_e
Resistance r_e = L_e / D_e is what retrieval minimises (sum over a path), exactly the
`weightProp` that HydraDB's native path procedures rank by.

After an agent answers a question that started at entity s:
  * answer verified  -> "food" at the answer node. Solve Kirchhoff's laws on the candidate
                        subgraph with conductances D_e/L_e, inject flux at s, drain at the
                        answer, and grow every tube by its flux:  R_e += H * |Q_e|.
                        Tubes that carried flow also get an `acc`.
  * answer rejected  -> the path that produced it is "lit": rej += 1 on its edges.
  * evaporation      -> every R_e decays by (1 - RHO) per update. Done lazily with one global
                        scale factor, so it costs O(1) and zero database writes:
                        R_true = R_stored / scale,  scale /= (1 - RHO) each update.

Convergence of these dynamics to shortest paths is proven for any graph
(Bonifaci, Mehlhorn & Varma, SODA 2012; Becchetti et al., ICALP 2013).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve


@dataclass
class Params:
    light: float = 8.0  # max extra tube length from rejections
    h: float = 0.8  # growth step
    inject: float = 1.0  # flux injected per verified answer
    rho: float = 0.002  # evaporation per update
    acc_flux: float = 0.05  # min |flux| for a tube to count as "fed"
    gamma: float = 0.5  # use-dependent plasticity: old evidence on an edge fades each time it is used
    mode: str = "physarum"  # "physarum" | "evidence" (length only) | "static"
    blame: str = "divergence"  # on 👎: full blame where the wrong path diverges from the runner-up, 0.25 elsewhere ("uniform" = all edges)
    side_blame: float = 0.25


@dataclass
class EdgeState:
    eid: int
    src: int
    dst: int
    rel: str
    R: float = 0.0  # stored reinforcement (scaled)
    acc: float = 0.0
    rej: float = 0.0


@dataclass
class Candidate:
    """One path s -> ... -> answer, as returned by the store."""

    nodes: list[int]
    edges: list[EdgeState]
    score: float = 0.0

    @property
    def answer(self) -> int:
        return self.nodes[-1]


@dataclass
class Engine:
    params: Params = field(default_factory=Params)
    scale: float = 1.0
    updates: int = 0

    # ---- tube physics -------------------------------------------------------
    def length(self, e: EdgeState) -> float:
        if self.params.mode == "static":
            return 1.0
        return 1.0 + self.params.light * (e.rej + 0.5) / (e.acc + e.rej + 1.0)

    def conductivity(self, e: EdgeState) -> float:
        if self.params.mode != "physarum":
            return 1.0
        return 1.0 + e.R / self.scale

    def _plastic(self, e: EdgeState):
        """Physarum tubes are plastic: each use re-weighs old evidence, so a fact that stops
        being true is unlearned in a few verdicts instead of hundreds. 'evidence' mode keeps
        raw counts forever (the ablation)."""
        if self.params.mode == "physarum":
            g = self.params.gamma
            e.acc *= g
            e.rej *= g

    def resistance(self, e: EdgeState) -> float:
        return self.length(e) / self.conductivity(e)

    # ---- retrieval ----------------------------------------------------------
    def rank(self, cands: list[Candidate]) -> list[Candidate]:
        for c in cands:
            c.score = sum(self.resistance(e) for e in c.edges)
        # deterministic tie-break on node ids, same as HydraDB's path ordering
        return sorted(cands, key=lambda c: (round(c.score, 9), c.nodes))

    def answers(self, cands: list[Candidate]) -> list[dict]:
        """Collapse ranked paths into ranked answers with a confidence from flow share."""
        ranked = self.rank(cands)
        if not ranked:
            return []
        best: dict[int, Candidate] = {}
        for c in ranked:
            best.setdefault(c.answer, c)
        # softmax over negative path resistance = how flux would split between answers
        scores = np.array([-best[a].score for a in best])
        p = np.exp(scores - scores.max())
        p /= p.sum()
        return [dict(answer=a, path=best[a], confidence=float(pi)) for a, pi in zip(best, p)]

    # ---- learning -----------------------------------------------------------
    def kirchhoff(self, cands: list[Candidate], source: int, sink: int) -> dict[int, float]:
        """Flux on every candidate edge when `inject` units flow source -> sink."""
        edges: dict[int, EdgeState] = {}
        for c in cands:
            for e in c.edges:
                edges[e.eid] = e
        nodes = sorted({n for e in edges.values() for n in (e.src, e.dst)})
        if sink not in nodes or source not in nodes or source == sink:
            return {}
        idx = {n: i for i, n in enumerate(nodes)}
        rows, cols, vals, cond = [], [], [], {}
        for e in edges.values():
            c = self.conductivity(e) / self.length(e)
            cond[e.eid] = c
            i, j = idx[e.src], idx[e.dst]
            rows += [i, j, i, j]
            cols += [i, j, j, i]
            vals += [c, c, -c, -c]
        n = len(nodes)
        Lap = coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsr()
        b = np.zeros(n)
        b[idx[source]] = self.params.inject
        b[idx[sink]] = -self.params.inject
        keep = [i for i in range(n) if i != idx[sink]]
        p = np.zeros(n)
        sol = spsolve(Lap[keep][:, keep].tocsc(), b[keep])
        p[keep] = np.nan_to_num(np.atleast_1d(sol))
        return {eid: cond[eid] * (p[idx[e.src]] - p[idx[e.dst]]) for eid, e in edges.items()}

    def feedback(self, cands: list[Candidate], source: int, chosen: Candidate, correct: bool) -> dict:
        """Apply one verdict. Returns {eid: EdgeState} of touched edges plus the flux map."""
        touched: dict[int, EdgeState] = {}
        flux: dict[int, float] = {}
        self.updates += 1
        if self.params.mode == "physarum":
            # float64 headroom: ~350k updates at rho=0.002 before R_stored overflows
            self.scale /= 1.0 - self.params.rho
        if self.params.mode == "static":
            return dict(touched=touched, flux=flux)
        if correct:
            fed = [c for c in cands if c.answer == chosen.answer]
            flux = self.kirchhoff(fed, source, chosen.answer)
            for c in fed:
                for e in c.edges:
                    q = abs(flux.get(e.eid, 0.0))
                    if q < self.params.acc_flux or e.eid in touched:
                        continue
                    self._plastic(e)
                    if self.params.mode == "physarum":
                        e.R += self.params.h * q * self.scale
                    e.acc += 1
                    touched[e.eid] = e
        else:
            weights = self._blame(cands, chosen)
            for e, w in zip(chosen.edges, weights):
                if w <= 0:
                    continue
                if w >= 1:
                    self._plastic(e)
                if self.params.mode == "physarum":
                    e.R *= self.params.gamma**w  # a starved tube retracts
                e.rej += w
                touched[e.eid] = e
        return dict(touched=touched, flux=flux)

    def _blame(self, cands, chosen):
        """Credit assignment for a rejection: the edge where the wrong path parts ways with the best
        alternative answer is the likely culprit; shared prefix edges are not blamed."""
        if self.params.blame == "uniform":
            return [1.0] * len(chosen.edges)
        alts = [c for c in self.rank([c for c in cands if c.answer != chosen.answer])]
        if not alts:
            return [1.0] * len(chosen.edges)
        alt = alts[0]
        out, diverged = [], False
        for i, e in enumerate(chosen.edges):
            same = i < len(alt.edges) and alt.edges[i].eid == e.eid
            if same and not diverged:
                out.append(0.0)  # shared with the alternative: not the culprit
            elif not diverged:
                out.append(1.0)
                diverged = True
            else:
                out.append(self.params.side_blame)
        return out

    def state(self) -> dict:
        return dict(scale=self.scale, updates=self.updates, mode=self.params.mode)

    def load_state(self, d: dict):
        self.scale = float(d.get("scale", 1.0))
        self.updates = int(d.get("updates", 0))

    @staticmethod
    def edge_view(engine: Engine, e: EdgeState) -> dict:
        return dict(
            eid=e.eid,
            src=e.src,
            dst=e.dst,
            rel=e.rel,
            acc=e.acc,
            rej=e.rej,
            length=round(engine.length(e), 4),
            conductivity=round(engine.conductivity(e), 4),
            resistance=round(engine.resistance(e), 4),
        )
