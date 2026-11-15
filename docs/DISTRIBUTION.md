# Distribution and demand playbook (do this before Monday 9 AM IST)

Everything below is already built. These are the steps only you can take. Replace `YOUR_GITHUB` /
`YOUR-DEPLOYMENT` in `pyproject.toml`, `server.json` and the texts below first.

## A. Publish (about 45 minutes in total)

| # | Channel | How | Time |
|---|---|---|---|
| 1 | **PyPI** | Free account at pypi.org → API token → `python -m build && twine upload dist/*`. Then `pip install physarum-graph` works for everyone. | 10 min |
| 2 | **Official MCP Registry** | After PyPI: add the line `<!-- mcp-name: io.github.YOUR_GITHUB/physarum -->` to the README (the registry checks it on PyPI), then `mcp-publisher login github && mcp-publisher publish` using `server.json`. | 15 min |
| 3 | **awesome-mcp-servers** | PR to github.com/punkpeye/awesome-mcp-servers under *Knowledge & Memory*: `- [YOUR_GITHUB/physarum](https://github.com/YOUR_GITHUB/physarum) 🐍 🏠 - Self-healing knowledge graph: multi-hop answers from your facts that learn from 👍/👎 which facts to trust.` | 5 min |
| 4 | **HydraDB GitHub** | Post `docs/hydradb-issue-localfs.md` as an issue on hydra-db/hydradb. Link the RFC in a comment or discussion. | 5 min |
| 5 | **GitHub repo** | Topics: `knowledge-graph mcp rag agents hydradb self-healing`. Add the demo URL to the repo's About box. Pin the repo on your profile. | 5 min |

## B. Get real demand signal (the part that moves the score most)

Targets for Monday morning: **30+ visitors, 8+ graphs built from visitors' own facts, 8+ survey answers, 3+
quotes people agreed to share.** The site counts all of these itself (`/api/traction`) and the landing page shows
them once there are 10+ visitors. Never add numbers by hand.

| Where | What to post |
|---|---|
| HydraDB hackathon Discord / community | "Built a learning layer on HydraDB: the graph heals itself from 👍/👎. Paste your own facts at YOUR-DEPLOYMENT/try; I'd love one line on whether you'd use it." |
| 5–10 people you know who build bots, agents or internal tools (WhatsApp/DM) | The tester message in `docs/LAUNCH.md`. Personal messages convert far better than posts. |
| X / LinkedIn | Posts in `docs/LAUNCH.md`, with a 20-second screen recording of `/try` healing. Tag @hydra_db. |
| r/LocalLLaMA or r/MachineLearning (weekend "Show" threads) | Show HN title from `docs/LAUNCH.md`, plus one honest paragraph on where it loses. |
| mem0 #4956 thread | Only if you have something genuinely useful to add (e.g. how usage feedback can demote a stale fact without an LLM call). Link once and don't spam. |

When the quotes arrive, they appear on the landing page automatically (only those that ticked "OK to quote me").
Screenshot `/api/traction` for the submission form.

## C. Persistence on Railway

Counts and feedback live in `data/`. Add a Railway **volume** mounted at `/data` and set
`PHYSARUM_DATA_DIR=/data`, so redeploys don't wipe your demand evidence.
