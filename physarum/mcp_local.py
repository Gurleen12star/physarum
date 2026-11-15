"""Local MCP server: give any agent (Claude Desktop, Cursor, Claude Code…) a self-healing graph of your facts.

    pip install "physarum-graph[mcp]"
    physarum-mcp --facts facts.csv              # in-memory
    physarum-mcp --facts facts.csv --hydradb-url https://your-hydradb   # learning persists on HydraDB edges

Claude Desktop / Cursor config:
    {"mcpServers": {"physarum": {"command": "physarum-mcp", "args": ["--facts", "/path/facts.csv"]}}}
"""

from __future__ import annotations

import argparse
import itertools
import os

from .sdk import Graph


def build(facts: str, hydradb_url: str | None = None, token: str | None = None):
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server

    kw = dict(hydradb_url=hydradb_url, token=token) if hydradb_url else {}
    g = Graph.from_csv(facts, **kw)
    answers: dict[int, object] = {}
    ids = itertools.count(1)
    mcp = Server("physarum")

    def pack(a):
        aid = next(ids)
        answers[aid] = a
        return dict(
            answer_id=aid,
            answer=a.answer,
            path=a.path,
            confidence=a.confidence,
            unsure=a.unsure,
            alternatives=a.alternatives[:5],
        )

    @mcp.tool()
    def relations() -> dict:
        """List the relation names in the graph and a sample of entities, to build `ask` calls."""
        return dict(relations=sorted(getattr(g, "rel_id", {})), entities=g.kg.names[:200], total=len(g.kg.names))

    @mcp.tool()
    def ask(subject: str, relations: list[str]) -> dict:
        """Follow one or more relations from `subject` (e.g. "Bob", ["reports_to", "leads"]). Returns the
        answer, the path of facts used, confidence, and an answer_id. If `unsure` is true, confirm with the
        user. Always report back with `feedback` once you learn whether the answer was right."""
        return pack(g.ask(subject, *relations))

    @mcp.tool()
    def feedback(answer_id: int, correct: bool) -> dict:
        """Tell the graph whether an answer was right. One bit; the graph re-weights its paths immediately
        and returns the answer it would give now."""
        a = answers.get(answer_id)
        if a is None:
            return dict(error="unknown answer_id; ask again")
        return pack(g.feedback(a, correct))

    @mcp.tool()
    def add_fact(subject: str, relation: str, object: str) -> dict:
        """Append a new fact. Old facts stay; feedback decides which ones the graph trusts."""
        g.add(subject, relation, object)
        return dict(ok=True, facts=len(g.kg.edges))

    @mcp.tool()
    def audit() -> list:
        """Facts the graph has stopped trusting (likely stale or wrong), most suspicious first."""
        return [dict(fact=f"{s} {r} {o}", rejected=rej, accepted=acc) for s, r, o, rej, acc in g.audit()]

    return mcp


def main(argv=None):
    ap = argparse.ArgumentParser(prog="physarum-mcp", description="Self-healing knowledge graph as an MCP server")
    ap.add_argument("--facts", default=os.environ.get("PHYSARUM_FACTS"), help="CSV: subject,relation,object")
    ap.add_argument("--hydradb-url", default=os.environ.get("HYDRADB_URL"))
    ap.add_argument("--token", default=os.environ.get("HYDRADB_AUTH_TOKEN"))
    a = ap.parse_args(argv)
    if not a.facts:
        ap.error("--facts (or PHYSARUM_FACTS) is required")
    build(a.facts, a.hydradb_url, a.token).run()


if __name__ == "__main__":
    main()
