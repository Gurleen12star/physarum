# Launch kit

Replace `<LIVE>` with your deployed URL and `<REPO>` with the GitHub link.

## 90-second video (record at 1440×900; screen + voice)

| Time | Screen | Say |
|---|---|---|
| 0:00–0:10 | Landing hero | "Knowledge graphs go stale. When the Queen died, every graph that said Elizabeth II became wrong, and nobody re-indexed it." |
| 0:10–0:25 | `/demo`: ask "Who is the king of England?" → Elizabeth II. Click **Real world**, jump to 2026 | "We replay what really changed between 2020 and 2026: 339 head-of-state questions. The frozen graph drops to 1%. Hybrid RAG drops to 17%." |
| 0:25–0:45 | Feed agent traffic; Charles III takes over; tubes thicken | "Physarum turns each 👍 or 👎 into slime-mould physics on HydraDB edges. Right paths thicken; outdated facts starve. 85% after a few hundred agent answers." |
| 0:45–0:55 | **Self-audit** tab | "It also tells you which facts it stopped trusting: 80% precision against human-verified labels." |
| 0:55–1:05 | Two windows side by side, one in 2026, one in 2020 | "Every visitor gets a private sandbox; the shared colony lives in HydraDB." |
| 1:05–1:20 | `/try`: example → ask where Bob is based → 👎 → Berlin; second question right for free | "Paste your own facts. One vote, and a different question is fixed too, because it shares the healed edge." |
| 1:20–1:30 | README numbers table + `pip install physarum-graph` | "Three lines on any graph, an MCP server for agents, and a native HydraDB procedure proposed upstream. Physarum." |

Tip: run `/demo?autoplay=1` once before recording so the graph is warm; keep the cursor slow.

## X / Twitter (tag @hydra_db)

> Knowledge graphs go stale. Ours heals itself.
>
> Physarum turns every 👍/👎 on an agent's answer into slime-mould physics on @hydra_db edges.
> On the real 2020→2026 head-of-state changes: frozen graph 4%, hybrid RAG 17%, Physarum 85%.
>
> Paste your own facts and watch it heal: <LIVE>/try
> Code: <REPO>

## LinkedIn

> For the HydraDB hackathon I built Physarum: a learning layer that makes a knowledge graph fix itself from
> ordinary feedback.
>
> The idea comes from a slime mould that rebuilt the Tokyo rail network in 2010. Paths that lead to right answers
> thicken; paths that lead to wrong ones starve. We tested it on real data (CoDEx/Wikidata, including 3,655
> human-verified false facts) and on what actually changed in the world between 2020 and 2026.
>
> Result: 85% on the changed facts, against 4% for a frozen graph and 17% for hybrid vector RAG, and a self-audit that
> flags untrustworthy facts at 80% precision.
>
> Try it on your own data: <LIVE>/try · Code: <REPO>

## Show HN title

> Show HN: Physarum – a knowledge graph that heals itself from 👍/👎 (slime-mould physics)

## Ask 5 people to try it (real demand signal)

Send to developers or founders who build agents, support bots or internal tools:

> Hey! I built a small thing for a hackathon: a knowledge graph that fixes its own stale facts from 👍/👎 feedback.
> Would you spend 2 minutes pasting a few facts from your own world (org chart, product catalogue, anything) at
> <LIVE>/try and tell me in one line whether you'd use it? Honest "no" is useful too.

Put the real replies (with permission) in the README under "What early testers said", and the real count from
`GET /api/waitlist/count`. Never round up or invent quotes.

## Submission blurb (≤ 100 words)

> Physarum is a learning layer for HydraDB: every 👍/👎 on an agent's answer becomes Kirchhoff-flux physics on the
> graph's edges, so right paths thicken and outdated or hallucinated facts starve. On the real 2020→2026
> head-of-state changes it reaches 85% (frozen graph 4%, hybrid RAG 17%) and its self-audit flags bad facts at 80%
> precision. Runs natively on HydraDB (`algo.SSpaths`, batched writes), with private sandboxes per visitor, a
> paste-your-own-graph playground, a 3-line SDK, an MCP server, a reproducible HydraDB bug report and an RFC for a
> native `algo.physarumUpdate` procedure.
