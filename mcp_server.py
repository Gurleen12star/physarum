"""MCP server: lets any agent (Claude, Cursor, …) use the self-healing graph and vote on it.

    PHYSARUM_URL=https://your-deployment  python mcp_server.py

Claude Desktop config:
    {"mcpServers": {"physarum": {"command": "python", "args": ["/path/mcp_server.py"],
                                 "env": {"PHYSARUM_URL": "https://your-deployment"}}}}
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

BASE = os.environ.get("PHYSARUM_URL", "http://localhost:8000").rstrip("/")
mcp = _Server("physarum")


def _call(path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


@mcp.tool()
def ask(question: str) -> dict:
    """Answer a question from the self-healing knowledge graph. Returns the answer, the path of facts
    it used, its confidence, whether it is unsure, and a qid to pass to `feedback`."""
    hits = _call("/api/search?q=" + urllib.parse.quote(question))
    if not hits:
        return {"error": "no matching question; try a person's name"}
    v = _call("/api/ask", {"qid": hits[0]["qid"]})
    a = v["answers"][0] if v["answers"] else None
    return {
        "qid": v["question"]["qid"],
        "question": v["question"]["text"],
        "answer": a and a["name"],
        "path": a and a["path"],
        "confidence": a and a["confidence"],
        "unsure": v["unsure"],
    }


@mcp.tool()
def feedback(qid: int, correct: bool) -> dict:
    """Tell the graph whether its answer was right. One bit; the graph learns from it immediately."""
    r = _call("/api/feedback", {"qid": qid, "correct": correct})
    a = r["after"]["answers"][0] if r["after"]["answers"] else None
    return {
        "learned": True,
        "answer_now": a and a["name"],
        "confidence_now": a and a["confidence"],
        "verdicts_total": r["verdicts"],
    }


@mcp.tool()
def why(qid: int) -> dict:
    """Explain an answer: every fact on the path with its learned length, conductivity and vote history."""
    return _call(f"/api/why?qid={qid}")


@mcp.tool()
def audit() -> dict:
    """List facts the graph has stopped trusting (likely false or outdated), with audit precision."""
    r = _call("/api/audit")
    return {k: r[k] for k in ("flagged", "precision", "lift")} | {
        "facts": [f"{i['src_name']} — {i['rel_label']} → {i['dst_name']}" for i in r["items"][:15]]
    }


if __name__ == "__main__":
    mcp.run()
