"""Real usage and demand signals, counted honestly and kept on disk.

Counts only (no tracking cookies, no IPs). Feedback quotes are shown publicly only when the person ticked
"OK to quote me". Point PHYSARUM_DATA_DIR at a persistent volume in production.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DIR = Path(os.environ.get("PHYSARUM_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
COUNTS = DIR / "traction.json"
FEEDBACK = DIR / "feedback.jsonl"
WAITLIST = DIR / "waitlist.jsonl"
KEYS = ("visitors", "demo_verdicts", "graphs_built", "facts_loaded", "playground_votes")
_lock = threading.Lock()


def _load():
    try:
        return {k: int(v) for k, v in json.loads(COUNTS.read_text()).items()}
    except (OSError, ValueError):
        return {}


_counts = {k: 0 for k in KEYS} | _load()
_dirty_since = 0.0


def bump(key: str, n: int = 1):
    global _dirty_since
    with _lock:
        _counts[key] = _counts.get(key, 0) + n
        if time.time() - _dirty_since > 5:  # write at most every 5 s
            _dirty_since = time.time()
            DIR.mkdir(parents=True, exist_ok=True)
            COUNTS.write_text(json.dumps(_counts))


def _rows(path: Path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def append(path: Path, row: dict):
    with _lock:
        DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}) + "\n")


def add_feedback(would_use: str, quote: str, name: str, role: str, consent: bool, facts: int):
    append(
        FEEDBACK,
        dict(
            would_use=would_use, quote=quote[:280], name=name[:80], role=role[:80], consent=bool(consent), facts=facts
        ),
    )


def summary():
    fb = _rows(FEEDBACK)
    votes = {k: sum(r.get("would_use") == k for r in fb) for k in ("yes", "maybe", "no")}
    with _lock:
        counts = dict(_counts)
    return dict(
        **counts,
        waitlist=len(_rows(WAITLIST)),
        feedback=len(fb),
        would_use=votes,
        since=_since(),
    )


def testimonials(limit: int = 6):
    rows = [r for r in _rows(FEEDBACK) if r.get("consent") and r.get("quote", "").strip()]
    return [dict(quote=r["quote"], name=r.get("name") or "Anonymous", role=r.get("role", "")) for r in rows[-limit:]][
        ::-1
    ]


def _since():
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(COUNTS.stat().st_ctime))
    except OSError:
        return time.strftime("%Y-%m-%d", time.gmtime())
