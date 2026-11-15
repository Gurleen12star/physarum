# RFC: `algo.physarumUpdate`, a native self-improving edge weight for HydraDB

**Status:** draft proposal from the Physarum hackathon project · **Author:** Physarum team

## Summary

Add one procedure that applies a feedback verdict to a retrieved path *inside* the database:

```cypher
CALL algo.physarumUpdate({
  path: $path,             // the path returned by algo.SSpaths
  alternatives: $paths,    // the competing candidate paths (same source)
  verdict: false,          // the answer's 👍 / 👎
  weightProp: 'r',         // resistance written back, readable by SSpaths
  params: {h: 0.8, light: 8, gamma: 0.5, rho: 0.002, sideBlame: 0.25}
}) YIELD touched, flux
```

Any HydraDB graph would then learn from use with no external service, and `algo.SSpaths(... weightProp:'r')`
would immediately route around facts that keep producing wrong answers.

## Motivation

Today Physarum does this from the client: one `SSpaths` read, Kirchhoff flux plus blame in Python, then one batched
`UNWIND ... MERGE ... SET` write (2.6 edges per verdict on average). It works, and it is measured on real HydraDB
in our repo. But the read-modify-write round trip:

- is racy when several agents give feedback at once (last write wins on `R`, `acc`, `rej`);
- costs two network hops per verdict;
- has to be re-implemented by every client.

## Semantics (what we have validated)

Per edge, three properties: `R` (reinforcement), `acc`, `rej` (plastic evidence), plus a graph-level `scale`.

| Quantity | Formula |
|---|---|
| Tube length | `L = 1 + light·(rej+½)/(acc+rej+1)` |
| Conductivity | `D = 1 + R/scale` |
| Resistance (written to `weightProp`) | `r = L/D` |
| Verified answer | Kirchhoff flux `Q` over the candidate network; `R += h·|Q|·scale`; `acc *= γ`, then `acc += 1` |
| Rejected answer | blame `w` = 1 at the divergence edge, `sideBlame` downstream, 0 on the shared prefix; `rej += w`, `R *= γ^w` |
| Forgetting | lazy: `scale /= (1-ρ)` per verdict, **zero edge writes** |

The procedure must apply these atomically per call (edge-level locks or one write transaction), so concurrent
verdicts compose instead of overwriting each other.

## Evidence it is worth it

On CoDEx-S (40,198 edges, 3,655 human-verified false facts) and the real 2020→2026 head-of-state changes, run on
HydraDB:

- 85% on never-asked questions vs 32% for the frozen graph;
- 85% on the real 2026 changes vs 4% frozen and 17% for hybrid vector RAG;
- the self-audit flags untrustworthy facts at 80% precision vs 25% chance.

Full method and seeds: `NUMBERS.md`.

## Open questions

1. Should `scale` live on a meta node (what we do now, id `10000000`) or in graph-level config?
2. Should the procedure also return `suspicion = (rej+½)/(acc+rej+1)` for a companion `algo.physarumAudit`?
3. Per-tenant or per-agent weight namespaces (`weightProp: 'r_agentA'`) for sandboxed learning, which Physarum's demo
   does client-side today.
