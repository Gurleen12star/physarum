"""The graph audits itself: tubes that keep starving are facts the graph no longer trusts.

A fact is flagged when its rejection evidence clearly outweighs its support. Because evidence
is plastic (old verdicts fade each time the edge is used), a flag reflects recent experience.
Precision is checked against CoDEx's human-verified labels (and the real 2020→2026 changes).
"""

from __future__ import annotations


def suspicion(e) -> float:
    return (e.rej + 0.5) / (e.acc + e.rej + 1.0)


def flagged(e, min_rej: float = 1.0, ratio: float = 2.0) -> bool:
    return e.rej >= min_rej and e.rej > ratio * e.acc


def audit(edges, truth=None, limit: int = 50):
    """edges: iterable of EdgeState seen by the colony. truth: list indexed by eid (True = fact holds)."""
    seen = [e for e in edges if (e.acc + e.rej) > 0]
    flags = sorted((e for e in seen if flagged(e)), key=suspicion, reverse=True)
    out = dict(examined=len(seen), flagged=len(flags), items=flags[:limit])
    if truth is not None:
        false_seen = [e for e in seen if not truth[e.eid]]
        tp = sum(1 for e in flags if not truth[e.eid])
        out.update(
            true_positives=tp,
            precision=round(tp / len(flags), 4) if flags else None,
            recall=round(tp / len(false_seen), 4) if false_seen else None,
            false_facts_seen=len(false_seen),
        )
    return out
