"""Physarum HTTP API, landing page (/) and live demo (/demo).

    uvicorn app:app --port 8000

Every request carries `X-Physarum-Session` (one private sandbox per browser tab) and optionally
`X-Physarum-Mode: shared` to act on the shared colony whose learning is written to HydraDB.
"""

from __future__ import annotations

import json
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from physarum import playground as pg
from physarum import service as svc
from physarum import traction
from physarum.service import (
    BASE_EDGES,
    HOS,
    KG,
    PARSER,
    QUESTIONS,
    RAG,
    REPLAY,
    ROOT,
    STORE,
    STREAM,
    TRANSFER,
    VERIFIER,
)

app = FastAPI(title="Physarum", version="1.1")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

EXAMPLES = [
    "Who is the king of England?",
    "Who is the head of state of A. A. Milne's country?",
    "What language is spoken in David Bowie's country?",
    "Which continent is Kelly Rowland's country on?",
    "Who is the head of state of Canada?",
    "Which currency is used in Bob Marley's country?",
]


class Ask(BaseModel):
    qid: int


class Verdict(BaseModel):
    qid: int
    correct: bool | None = None


class Feed(BaseModel):
    n: int = 200


def session(request: Request) -> svc.Session:
    return svc.get_session(
        request.headers.get("x-physarum-session"), shared=request.headers.get("x-physarum-mode") == "shared"
    )


# ---- pages ------------------------------------------------------------------------------------
@app.get("/")
def landing():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/demo")
def demo_page():
    return FileResponse(ROOT / "static" / "demo.html")


@app.get("/try")
def try_page():
    return FileResponse(ROOT / "static" / "try.html")


@app.get("/api/health")
def health():
    return dict(ok=True, store=STORE.name, sessions=svc.session_count())


# ---- session state ----------------------------------------------------------------------------
@app.get("/api/info")
def info(request: Request):
    s = session(request)
    with s.lock:
        if not s.curve:
            s.measure()
        nf = ROOT / "static" / "numbers.json"
        return dict(
            store=STORE.name,
            mode="shared" if s.shared else "sandbox",
            entities=svc.BASE_NAMES,
            edges=BASE_EDGES,
            false_edges=sum(not t for *_, t in KG.edges[:BASE_EDGES]),
            questions=len(KG.questions),
            hos_questions=len(HOS),
            verifier=VERIFIER.name,
            rag=RAG.name if RAG else None,
            verdicts=s.verdicts,
            curve=s.curve,
            replayed=s.replayed,
            engine=s.engine.state(),
            numbers=json.loads(nf.read_text()) if nf.exists() else None,
            hos_featured=[dict(qid=q.qid, text=q.text) for q in HOS[:60]],
            featured=[dict(qid=q.qid, text=q.text) for q in STREAM[:40]],
        )


@app.get("/api/search")
def search(q: str = "", limit: int = 30):
    ql = q.lower().strip()
    pool = HOS + KG.questions
    hits = [x for x in pool if ql in x.text.lower()] if ql else pool[:limit]
    return [
        dict(qid=x.qid, text=x.text, kind="hos" if 1_000_000 <= x.qid < 2_000_000 else "codex") for x in hits[:limit]
    ]


@app.get("/api/parse")
def parse(text: str):
    """Ask anything: plain English → a graph question (no LLM needed)."""
    q, msg = PARSER.parse(text, STORE)
    if q is None:
        return dict(ok=False, message=msg, suggestions=search(text.split("'")[0][-30:], 6))
    QUESTIONS[q.qid] = q
    return dict(ok=True, qid=q.qid, interpreted=msg, text=q.text)


@app.get("/api/examples")
def examples():
    return [t for t in EXAMPLES if PARSER.parse(t, STORE)[0] is not None]


@app.post("/api/ask")
def ask(body: Ask, request: Request):
    s = session(request)
    with s.lock:
        return s.view(svc.question(body.qid))


@app.post("/api/feedback")
def feedback(body: Verdict, request: Request):
    s = session(request)
    with s.lock:
        q = svc.question(body.qid)
        before = s.view(q)
        res = s.apply(q, body.correct, live=True)
        traction.bump("demo_verdicts")
        after = s.view(q, res.get("flux"))
        s.snap("👍 vote" if res.get("verdict") else "👎 vote")
        s.persist()
        return dict(
            before=before,
            after=after,
            verdict=res.get("verdict"),
            verdicts=s.verdicts,
            verifier="you" if body.correct is not None else VERIFIER.name,
        )


@app.post("/api/feed")
def feed(body: Feed, request: Request):
    """Simulated agent traffic: n popular questions, each judged by the verifier."""
    s = session(request)
    n = max(1, min(body.n, 1000))
    with s.lock:
        t0, right = time.time(), 0
        for _ in range(n):
            right += s.apply(s.pick()).get("was_right", False)
        pt = s.measure()
        traction.bump("demo_verdicts", n)
        s.snap(f"{n} agent questions")
        return dict(
            point=pt,
            stream_accuracy=round(right / n, 4),
            curve=s.curve,
            verdicts=s.verdicts,
            seconds=round(time.time() - t0, 2),
            replay_log=s.replay_log,
        )


@app.post("/api/replay")
def replay(request: Request):
    """Jump this session's world to 2026: the real head-of-state changes become visible (old facts stay)."""
    s = session(request)
    with s.lock:
        already = s.replayed
        r = s.replay()
        pt = s.measure() if not already else s.curve[-1]
        if not already:
            s.snap("2026 facts written")
        return dict(
            already=already,
            events=r.events,
            questions=len(r.questions),
            new_edges=len(r.new_edges),
            retired=len(r.retired_edges),
            right_after=pt.get("hos"),
            static_after=pt.get("static_hos"),
            rag_after=pt.get("rag_hos"),
        )


@app.get("/api/replay/status")
def replay_status(request: Request):
    s = session(request)
    if not s.replayed:
        return dict(replayed=False)
    return dict(replayed=True, events=REPLAY.events, log=s.replay_log, verdicts_since=s.replay_verdicts)


@app.get("/api/audit")
def audit(request: Request):
    s = session(request)
    with s.lock:
        return s.audit()


@app.get("/api/why")
def why(qid: int, request: Request):
    s = session(request)
    with s.lock:
        q = svc.question(qid)
        ranked = s.engine.answers(s.cands(q, live=True))
        if not ranked:
            return dict(question=q.text, steps=[])
        best = ranked[0]
        steps = []
        for e in best["path"].edges:
            j = s.edge_json(e)
            j.update(history=list(s.history.get(e.eid, [])), last_flux=round(s.last_flux.get(e.eid, 0.0), 4))
            steps.append(j)
        alt = ranked[1] if len(ranked) > 1 else None
        return dict(
            question=q.text,
            answer=svc.name(best["answer"]),
            resistance=round(best["path"].score, 4),
            confidence=round(best["confidence"], 4),
            steps=steps,
            runner_up=dict(
                name=svc.name(alt["answer"]),
                resistance=round(alt["path"].score, 4),
                confidence=round(alt["confidence"], 4),
            )
            if alt
            else None,
        )


@app.get("/api/suggest")
def suggest(request: Request, k: int = 30):
    """The question whose answer is the closest race in the graph right now."""
    s = session(request)
    with s.lock:
        pool = HOS + STREAM
        best, best_m = None, 9.0
        for i in s.rng.choice(len(pool), size=min(k, len(pool)), replace=False):
            q = pool[int(i)]
            a = s.engine.answers(s.cands(q))
            if len(a) < 2:
                continue
            m = a[0]["confidence"] - a[1]["confidence"]
            if m < best_m:
                best, best_m = q, m
        return dict(qid=best.qid, text=best.text, margin=round(best_m, 4)) if best else dict(qid=None)


@app.post("/api/wipe")
def wipe(request: Request):
    """Removal test: forget everything this session learned (snapshot kept), then measure."""
    s = session(request)
    with s.lock:
        before = s.accuracy(s.engine, TRANSFER)
        n = s.wipe()
        return dict(before=before, after=s.accuracy(s.engine, TRANSFER), edges=n, shared=s.shared)


@app.post("/api/restore")
def restore(request: Request):
    s = session(request)
    with s.lock:
        s.restore()
        return dict(after=s.accuracy(s.engine, TRANSFER), shared=s.shared)


@app.post("/api/reset")
def reset(request: Request):
    s = svc.reset_session(session(request))
    with s.lock:
        s.measure()
        return dict(ok=True, mode="shared" if s.shared else "sandbox")


# ---- visualisation ----------------------------------------------------------------------------
@app.get("/api/log")
def log(since: int = 0):
    lg = STORE.log
    return dict(store=STORE.name, total=lg.total, items=[x for x in lg.items if x["i"] > since][-25:])


@app.get("/api/colony")
def colony(request: Request, limit: int = 260):
    s = session(request)
    with s.lock:
        strength = lambda e: s.engine.conductivity(e) * (1 + e.acc) / (1 + e.rej)
        fed = sorted(s.registry.values(), key=lambda e: -strength(e))[: int(limit * 0.7)]
        starved = sorted((e for e in s.registry.values() if e.rej > e.acc), key=lambda e: -e.rej)[: int(limit * 0.3)]
        es = list({e.eid: e for e in fed + starved}.values())
        nodes = {n: dict(id=n, name=svc.name(n)) for e in es for n in (e.src, e.dst)}
        return dict(nodes=list(nodes.values()), edges=[s.edge_json(e) for e in es])


@app.get("/api/timeline")
def timeline(request: Request):
    s = session(request)
    with s.lock:
        nodes = {
            n: dict(id=n, name=svc.name(n)) for sn in s.timeline for e in sn["edges"] for n in (e["src"], e["dst"])
        }
        return dict(nodes=list(nodes.values()), snapshots=s.timeline)


@app.get("/api/report")
def report(request: Request):
    """Graph health report (Markdown): what the graph has learned not to trust."""
    s = session(request)
    with s.lock:
        a = s.audit()
        last = s.curve[-1] if s.curve else {}
        lines = [
            "# Graph health report",
            "",
            f"Generated by Physarum · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
            "",
            f"- Store: {STORE.name} · {BASE_EDGES:,} facts · verdicts learned from: {s.verdicts:,}",
            f"- Accuracy on never-asked questions: {last.get('transfer', 0):.0%} (frozen graph {last.get('static_transfer', 0):.0%})",
            f"- Facts examined by traffic: {a['examined']:,} · flagged as untrustworthy: {a['flagged']}"
            + (f" · precision {a['precision']:.0%} vs chance {a['base_rate']:.0%}" if a.get("precision") else ""),
            "",
            "## Facts to review (most suspicious first)",
            "",
            "| Fact | 👍 | 👎 | Suspicion | Known label |",
            "|---|---|---|---|---|",
        ]
        for i in a["items"]:
            lines.append(
                f"| {i['src_name']} — {i['rel_label']} → {i['dst_name']} | {i['acc']:.1f} | {i['rej']:.1f} | {i['suspicion']:.0%} | {i['label']} |"
            )
        lines += [
            "",
            "Suspicion = (👎+½)/(👍+👎+1) with plastic evidence. Flag rule: 👎 ≥ 1 and 👎 > 2×👍.",
            "Labels come from CoDEx human verification and the real 2020→2026 changes.",
        ]
        return PlainTextResponse(
            "\n".join(lines),
            media_type="text/markdown",
            headers={"Content-Disposition": "attachment; filename=graph-health-report.md"},
        )


# ---- playground: your own graph -----------------------------------------------------------------
class Load(BaseModel):
    text: str


class PlayAsk(BaseModel):
    subject: str
    relations: list[str]


class PlayVote(BaseModel):
    id: int
    correct: bool


class PlayFact(BaseModel):
    subject: str
    relation: str
    object: str


def playground(request: Request) -> pg.Playground:
    p = pg.get(request.headers.get("x-physarum-session") or "anonymous")
    if p is None:
        raise HTTPException(400, "load a graph first")
    return p


@app.get("/api/try/example")
def try_example():
    return dict(text=pg.EXAMPLE, note=pg.EXAMPLE_NOTE)


@app.post("/api/try/load")
def try_load(body: Load, request: Request):
    if len(body.text) > 600_000:
        raise HTTPException(413, "paste up to ~5,000 facts")
    try:
        p = pg.Playground.from_text(body.text)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    pg.put(request.headers.get("x-physarum-session") or "anonymous", p)
    traction.bump("graphs_built")
    traction.bump("facts_loaded", p.n_facts)
    return p.schema()


@app.post("/api/try/ask")
def try_ask(body: PlayAsk, request: Request):
    p = playground(request)
    with p.lock:
        try:
            return p.ask(body.subject, [r for r in body.relations if r])
        except KeyError as err:
            raise HTTPException(400, str(err).strip("'")) from err


@app.post("/api/try/feedback")
def try_feedback(body: PlayVote, request: Request):
    p = playground(request)
    with p.lock:
        try:
            res = dict(answer=p.feedback(body.id, body.correct), votes=p.votes)
            traction.bump("playground_votes")
            return res
        except KeyError as err:
            raise HTTPException(400, str(err).strip("'")) from err


@app.post("/api/try/add")
def try_add(body: PlayFact, request: Request):
    p = playground(request)
    with p.lock:
        try:
            p.add(body.subject.strip(), body.relation.strip(), body.object.strip())
        except ValueError as err:
            raise HTTPException(400, str(err)) from err
        return p.schema()


@app.get("/api/try/audit")
def try_audit(request: Request):
    p = playground(request)
    with p.lock:
        return dict(items=p.audit(), votes=p.votes)


# ---- early access -------------------------------------------------------------------------------
class Waitlist(BaseModel):
    email: str
    company: str = ""
    use_case: str = ""


@app.post("/api/waitlist")
def waitlist(body: Waitlist):
    email = body.email.strip()[:200]
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "please enter a valid email")
    traction.append(
        traction.WAITLIST, dict(email=email, company=body.company.strip()[:200], use_case=body.use_case.strip()[:500])
    )
    return dict(ok=True)


@app.get("/api/waitlist/count")
def waitlist_count():
    return dict(count=traction.summary()["waitlist"])


class Feedback(BaseModel):
    would_use: str
    quote: str = ""
    name: str = ""
    role: str = ""
    consent: bool = False


@app.post("/api/try/survey")
def try_survey(body: Feedback, request: Request):
    if body.would_use not in ("yes", "maybe", "no"):
        raise HTTPException(400, "would_use must be yes, maybe or no")
    p = pg.get(request.headers.get("x-physarum-session") or "anonymous")
    traction.add_feedback(
        body.would_use, body.quote.strip(), body.name.strip(), body.role.strip(), body.consent, p.n_facts if p else 0
    )
    return dict(ok=True)


@app.get("/api/traction")
def traction_summary():
    """Real usage of this deployment: counts only, no personal data."""
    return dict(**traction.summary(), testimonials=traction.testimonials())
