# Numbers (generated 2026-11-15 06:21 UTC)

Store: reference store (results identical to HydraDB) · Verifier: simulated, 10% noise · 5 seeds

| Metric | Hybrid RAG | Frozen | Count-only | Physarum |
|---|---|---|---|---|
| Never-asked questions | 37% | 32% | 86% | **85%** |
| Questions about other people | 24% | 38% | 89% | **87%** |
| Facts changed, +250 questions | – | 45% | 38% | **59%** |
| Facts changed, +1000 questions | – | 45% | 46% | **76%** |
| Facts changed, +2500 questions | – | 45% | 58% | **87%** |
| Real 2020→2026 heads of state (+600 verdicts) | 17% | 4% | 76% | **85%** |

- hybrid RAG (vector + keyword) · spaCy en_core_web_md (300-d) · top-10: finds the person's facts 67% of the time; when it does, picks the true fact 55%; on real 2026 changes picks the current fact 24% (it was 66% right in 2020)
- Context per answer: Physarum 18 tokens (the path) vs RAG 180 tokens (top-10 × 2 hops)
- Votes to heal a real change (median per country): **5.0** (2/3 countries healed)
- Self-audit: precision **80%**, recall 35%, chance 25% (lift 3.14×)
- Removal test: learned 86% → wiped 27% → restored 86%
- Active learning (400 votes): uncertainty-chosen 73% vs random 78% on never-asked questions
- Cost per verdict: 2.56 edges, 2 requests; forgetting: 0 writes (naive: 40,216)
