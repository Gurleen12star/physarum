"""Graph stores. HydraDB is the source of truth in production; MemoryStore mirrors it
for tests and for running the demo without a database.

Edge properties kept in the graph (per relationship):
    id   stable edge id          R    Physarum reinforcement (scaled, see engine)
    acc  verified-useful count   rej  rejected count
    r    resistance L/D, ready for algo.SPpaths({weightProp: 'r'})
"""

from __future__ import annotations

import collections
import json
import os
import time
import urllib.request
from collections.abc import Iterable

from .engine import Candidate, EdgeState, Engine


class QueryLog:
    """Ring buffer of the last store calls, shown live in the demo."""

    def __init__(self, n=40):
        self.items, self.n, self.total = [], n, 0

    def add(self, op: str, ms: float, rows: int):
        self.total += 1
        self.items.append(dict(i=self.total, op=op[:220], ms=round(ms, 2), rows=rows, t=time.time()))
        del self.items[: -self.n]


class MemoryStore:
    name = "memory"

    def __init__(self, kg):
        self.kg = kg
        self.log = QueryLog()
        self.edges = [EdgeState(eid, s, d, r) for eid, (s, d, r, _) in enumerate(kg.edges)]
        self.out = collections.defaultdict(lambda: collections.defaultdict(list))
        for e in self.edges:
            self.out[e.src][e.rel].append(e)
        self.meta = {}

    def add_entities(self, rows):  # rows: [{id, name, qid}]
        for r in rows:
            while len(self.kg.names) <= r["id"]:
                self.kg.names.append("")
                self.kg.qids.append("")
            self.kg.names[r["id"]] = r["name"]
            self.kg.qids[r["id"]] = r.get("qid") or ""

    def add_edges(self, rows):  # rows: [{s, d, rel, e}]
        for r in rows:
            e = EdgeState(r["e"], r["s"], r["d"], r["rel"])
            while len(self.edges) <= r["e"]:
                self.edges.append(None)
            self.edges[r["e"]] = e
            self.out[e.src][e.rel].append(e)

    def chain(self, s: int, r1: str, r2: str) -> list[Candidate]:
        t0 = time.perf_counter()
        cands = self._chain(s, r1, r2)
        self.log.add(f"memory.chain({s}, {r1}, {r2})", (time.perf_counter() - t0) * 1000, len(cands))
        return cands

    def chain_rels(self, s: int, rels: list[str]) -> list[Candidate]:
        """k-hop: every simple path from s following the relation sequence `rels`."""
        t0 = time.perf_counter()
        out = []

        def walk(node, depth, nodes, edges):
            if depth == len(rels):
                out.append(Candidate(list(nodes), list(edges)))
                return
            for e in self.out[node].get(rels[depth], []):
                if e.dst in nodes:
                    continue
                walk(e.dst, depth + 1, nodes + [e.dst], edges + [e])

        walk(s, 0, [s], [])
        self.log.add(f"memory.chain_rels({s}, {rels})", (time.perf_counter() - t0) * 1000, len(out))
        return out

    def _chain(self, s: int, r1: str, r2: str) -> list[Candidate]:
        cands = []
        for a in self.out[s].get(r1, []):
            for b in self.out[a.dst].get(r2, []):
                if b.dst == s or a.dst == s:  # simple paths only, same as HydraDB's algo.* procedures
                    continue
                cands.append(Candidate([s, a.dst, b.dst], [a, b]))
        return cands

    def write(self, engine: Engine, touched: Iterable[EdgeState]):
        n = len(list(touched))
        self.log.add(f"memory.write({n} edges)", 0.0, n)  # objects are shared; nothing to persist

    def save_meta(self, d: dict):
        self.meta = dict(d)

    def load_meta(self) -> dict:
        return dict(self.meta)

    def neighbourhood(self, s: int, limit: int = 60):
        return [e for es in self.out[s].values() for e in es][:limit]

    def reset(self, edges=None):
        for e in edges if edges is not None else self.edges:
            if e is not None:
                e.R, e.acc, e.rej = 0.0, 0.0, 0.0
        if edges is None:
            self.meta = {}

    def delete_edges(self, rows):
        for r in rows:
            e = self.edges[r["e"]] if r["e"] < len(self.edges) else None
            if e is not None:
                self.out[e.src][e.rel] = [x for x in self.out[e.src][e.rel] if x.eid != e.eid]
                self.edges[r["e"]] = None
        while self.edges and self.edges[-1] is None:
            self.edges.pop()


class HydraStore:
    """Talks to HydraDB over its HTTP query API (POST /v1/graphs/{graph}/query)."""

    name = "hydradb"

    def __init__(self, kg, url=None, token=None, graph=None, cell=None, namespace=None):
        self.kg = kg
        self.url = (url or os.environ.get("HYDRADB_URL", "http://127.0.0.1:8443")).rstrip("/")
        self.token = token or os.environ.get("HYDRADB_AUTH_TOKEN", "local-development-token-32-bytes")
        self.graph = graph or os.environ.get("HYDRADB_GRAPH", "default")
        self.cell = cell or os.environ.get("HYDRADB_CELL", "cell-0")
        self.namespace = namespace or os.environ.get("HYDRADB_NAMESPACE", "default")
        self.bookmark = None
        self.requests = 0
        self.ms = 0.0
        self.log = QueryLog()

    # ---- transport ------------------------------------------------------------
    def q(self, query: str, params: dict | None = None, page_size: int = 4096) -> list[dict]:
        body = {"cell_id": self.cell, "query": query, "page_size": page_size}
        if params:
            body["parameters"] = params
        if self.bookmark:
            body["bookmark"] = self.bookmark
        rows, cursor, qid = [], None, None
        while True:
            if cursor is not None:
                body["cursor"], body["query_id"] = cursor, qid
            req = urllib.request.Request(
                f"{self.url}/v1/graphs/{self.graph}/query",
                data=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Graph-Namespace": self.namespace,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    res = json.loads(r.read())
            except urllib.error.HTTPError as err:
                raise RuntimeError(f"HydraDB {err.code}: {err.read().decode()[:400]}\nquery: {query}") from None
            dt = (time.perf_counter() - t0) * 1000
            self.ms += dt
            self.requests += 1
            self.log.add(" ".join(query.split()), dt, len(res.get("rows", []) or []))
            if res.get("bookmark"):
                self.bookmark = res["bookmark"]
            if "error" in res or "rows" not in res:
                raise RuntimeError(f"HydraDB error: {str(res)[:400]}\nquery: {query}")
            rows += [self._row(res, row) for row in res["rows"]]
            cursor, qid = res.get("next_cursor"), res.get("query_id")
            if cursor is None:
                return rows

    @staticmethod
    def _cell(c):
        if isinstance(c, dict) and "type" in c:
            return c.get("value")  # {"type": "null"} carries no value key
        return c

    def _row(self, res, row):
        cols = res.get("columns") or []
        names = [c["name"] if isinstance(c, dict) else c for c in cols]
        if isinstance(row, dict) and "values" in row:
            row = row["values"]
        if isinstance(row, dict):
            return {k: self._cell(v) for k, v in row.items()}
        return {n: self._cell(v) for n, v in zip(names, row)}

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/healthz", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    # ---- load -----------------------------------------------------------------
    def reset(self, edges=None):
        """Tubes back to their initial state. With `edges`, only those (fast); else all (~40 batches)."""
        if edges is None:
            edges = [EdgeState(eid, s, d, r) for eid, (s, d, r, _) in enumerate(self.kg.edges)]
        fresh = [EdgeState(e.eid, e.src, e.dst, e.rel) for e in edges]
        self.write(Engine(), fresh)

    def add_entities(self, rows):
        self.q(
            "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Entity, n.name = row.name, n.qid = row.qid",
            {"rows": [dict(id=r["id"], name=r["name"], qid=r.get("qid") or "") for r in rows]},
        )

    def add_edges(self, rows):
        by_rel = collections.defaultdict(list)
        for r in rows:
            by_rel[r["rel"]].append({"s": r["s"], "d": r["d"], "e": r["e"], "R": 0.0, "acc": 0.0, "rej": 0.0, "r": 5.0})
        for rel, rr in by_rel.items():
            self.q(
                f"UNWIND $rows AS row MATCH (s:Entity {{id: row.s}}), (d:Entity {{id: row.d}}) "
                f"MERGE (s)-[r:{rel} {{id: row.e}}]->(d) SET r.R = row.R, r.acc = row.acc, r.rej = row.rej, r.r = row.r",
                {"rows": rr},
            )

    def delete_edges(self, rows):
        by_rel = collections.defaultdict(list)
        for r in rows:
            by_rel[r["rel"]].append({"s": r["s"], "e": r["e"]})
        for rel, rr in by_rel.items():
            self.q(f"UNWIND $rows AS row MATCH ()-[r:{rel} {{id: row.e}}]->() DELETE r", {"rows": rr})

    def has_entity(self, vid: int) -> bool:
        rows = self.q("MATCH (n:Entity {id: $i}) RETURN n.name AS name", {"i": vid})
        return bool(rows) and rows[0].get("name") is not None

    def is_loaded(self) -> bool:
        rows = self.q("MATCH (n:Entity {id: 0}) RETURN n.name AS name")
        return bool(rows) and rows[0].get("name") is not None

    def count_edges(self) -> int:
        total = 0
        for rel in sorted({r for _, _, r, _ in self.kg.edges}):
            rows = self.q(f"MATCH (a:Entity)-[:{rel}]->(b) RETURN count(*) AS n")
            total += int(rows[0]["n"]) if rows else 0
        return total

    def load(self, batch: int = 1000, log=print):
        kg = self.kg
        nodes = [{"id": i, "name": n, "qid": q} for i, (n, q) in enumerate(zip(kg.names, kg.qids))]
        for i in range(0, len(nodes), batch):
            self.q(
                "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Entity, n.name = row.name, n.qid = row.qid",
                {"rows": nodes[i : i + batch]},
            )
        self.q(
            "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Meta, n.state = row.state",
            {"rows": [{"id": 10_000_000, "state": "{}"}]},
        )
        by_rel = collections.defaultdict(list)
        for eid, (s, d, r, _) in enumerate(kg.edges):
            by_rel[r].append({"s": s, "d": d, "e": eid, "R": 0.0, "acc": 0.0, "rej": 0.0, "r": 5.0})
        for rel, rows in sorted(by_rel.items()):
            for i in range(0, len(rows), batch):
                self.q(
                    f"UNWIND $rows AS row MATCH (s:Entity {{id: row.s}}), (d:Entity {{id: row.d}}) "
                    f"CREATE (s)-[:{rel} {{id: row.e, R: row.R, acc: row.acc, rej: row.rej, r: row.r}}]->(d)",
                    {"rows": rows[i : i + batch]},
                )
        log(f"loaded {len(nodes)} entities and {len(kg.edges)} edges into HydraDB")

    # ---- retrieval ------------------------------------------------------------
    def chain(self, s: int, r1: str, r2: str) -> list[Candidate]:
        """One native HydraDB call: algo.SSpaths over the two relation types, ranked inside
        the database by the learned resistance `r` (weightProp). The engine re-scores with the
        exact current evaporation scale."""
        rows = self.q(
            f"CALL algo.SSpaths({{sourceNode: $s, relTypes: ['{r1}', '{r2}'], relDirection: 'outgoing', "
            f"maxLen: 2, pathCount: 1000, weightProp: 'r'}}) YIELD path RETURN path",
            {"s": s},
        )
        cache: dict[int, EdgeState] = {}
        out = []
        for row in rows:
            p = row["path"]
            rels = p["relationships"]
            if [x["edge_type"] for x in rels] != [r1, r2]:
                continue
            edges = []
            for x in rels:
                pr = {k: next(iter(v.values())) for k, v in x["properties"].items()}
                eid = int(pr["id"])
                if eid not in cache:
                    cache[eid] = EdgeState(
                        eid,
                        int(x["src"]),
                        int(x["dst"]),
                        x["edge_type"],
                        float(pr.get("R", 0)),
                        float(pr.get("acc", 0)),
                        float(pr.get("rej", 0)),
                    )
                edges.append(cache[eid])
            out.append(Candidate([int(n["id"]) for n in p["nodes"]], edges))
        return out

    def chain_rels(self, s: int, rels: list[str]) -> list[Candidate]:
        """k-hop via one native algo.SSpaths call, filtered to the relation sequence."""
        rel_list = ", ".join(f"'{r}'" for r in dict.fromkeys(rels))
        rows = self.q(
            f"CALL algo.SSpaths({{sourceNode: $s, relTypes: [{rel_list}], relDirection: 'outgoing', "
            f"maxLen: {len(rels)}, pathCount: 1000, weightProp: 'r'}}) YIELD path RETURN path",
            {"s": s},
        )
        out, cache = [], {}
        for row in rows:
            p = row["path"]
            rs = p["relationships"]
            if [x["edge_type"] for x in rs] != list(rels):
                continue
            edges = []
            for x in rs:
                pr = {k: next(iter(v.values())) for k, v in x["properties"].items()}
                eid = int(pr["id"])
                if eid not in cache:
                    cache[eid] = EdgeState(
                        eid,
                        int(x["src"]),
                        int(x["dst"]),
                        x["edge_type"],
                        float(pr.get("R", 0)),
                        float(pr.get("acc", 0)),
                        float(pr.get("rej", 0)),
                    )
                edges.append(cache[eid])
            out.append(Candidate([int(n["id"]) for n in p["nodes"]], edges))
        return out

    def write(self, engine: Engine, touched: Iterable[EdgeState]):
        by_rel = collections.defaultdict(list)
        for e in touched:
            by_rel[e.rel].append(
                {
                    "s": e.src,
                    "d": e.dst,
                    "e": e.eid,
                    "R": float(e.R),
                    "acc": e.acc,
                    "rej": e.rej,
                    "r": float(engine.resistance(e)),
                }
            )
        for rel, rows in by_rel.items():
            for i in range(0, len(rows), 1000):  # HydraDB admits <= 1024 batch items
                self.q(
                    f"UNWIND $rows AS row MATCH (s:Entity {{id: row.s}}), (d:Entity {{id: row.d}}) "
                    f"MERGE (s)-[r:{rel} {{id: row.e}}]->(d) "
                    f"SET r.R = row.R, r.acc = row.acc, r.rej = row.rej, r.r = row.r",
                    {"rows": rows[i : i + 1000]},
                )

    def save_meta(self, d: dict):
        self.q(
            "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Meta, n.state = row.state",
            {"rows": [{"id": 10_000_000, "state": json.dumps(d)}]},
        )

    def load_meta(self) -> dict:
        rows = self.q("MATCH (n:Meta {id: 10000000}) RETURN n.state AS state")
        try:
            return json.loads(rows[0]["state"]) if rows else {}
        except Exception:
            return {}

    def path(self, s: int, t: int, rels: list[str], max_len: int = 3):
        """Native weighted path search inside HydraDB, ranked by learned resistance."""
        rel_list = "[" + ", ".join(f"'{r}'" for r in rels) + "]"
        return self.q(
            f"CALL algo.SPpaths({{sourceNode: $s, targetNode: $t, relTypes: {rel_list}, "
            f"relDirection: 'outgoing', maxLen: $m, pathCount: 1, weightProp: 'r'}}) "
            f"YIELD path RETURN path",
            {"s": s, "t": t, "m": max_len},
        )
