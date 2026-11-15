"""Command line: try Physarum on a CSV of facts in ten seconds.

physarum ask facts.csv Bob reports_to leads        # one question
physarum chat facts.csv                            # ask, vote y/n, watch it heal, then see the audit
physarum example > facts.csv                       # a small company graph to start from
"""

from __future__ import annotations

import argparse
import sys

from .playground import EXAMPLE
from .sdk import Graph


def _show(a):
    if a.answer is None:
        print("  no path found")
        return
    flag = "  (unsure)" if a.unsure else ""
    print(f"  → {a.answer}   {a.confidence:.0%}{flag}")
    print("    via " + " → ".join(a.path))
    if a.alternatives:
        print("    other: " + ", ".join(f"{n} {c:.0%}" for n, c in a.alternatives[:3]))


def chat(g: Graph):
    print("Type: <subject> <relation> [<relation> ...]   ·  'audit'  ·  'quit'")
    while True:
        try:
            line = input("? ").strip()
        except EOFError:
            break
        if line in ("quit", "exit", "q"):
            break
        if line == "audit":
            for s, r, o, rej, acc in g.audit() or []:
                print(f"  ✗ {s} {r} {o}   👎 {rej}  👍 {acc}")
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            a = g.ask(parts[0], *parts[1:])
        except KeyError as e:
            print(f"  {e}")
            continue
        _show(a)
        if a.answer is None:
            continue
        v = input("  right? [y/n/enter to skip] ").strip().lower()
        if v in ("y", "n"):
            b = g.feedback(a, v == "y")
            if b.answer != a.answer:
                print(f"  healed: {a.answer} → {b.answer}")
            _show(b)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="physarum", description="A knowledge graph that heals itself from 👍/👎")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ask", help="answer one multi-hop question")
    p.add_argument("facts")
    p.add_argument("subject")
    p.add_argument("relations", nargs="+")
    c = sub.add_parser("chat", help="interactive: ask, vote, heal, audit")
    c.add_argument("facts")
    sub.add_parser("example", help="print an example facts CSV")
    a = ap.parse_args(argv)
    if a.cmd == "example":
        sys.stdout.write(EXAMPLE)
    elif a.cmd == "ask":
        _show(Graph.from_csv(a.facts).ask(a.subject, *a.relations))
    else:
        chat(Graph.from_csv(a.facts))


if __name__ == "__main__":
    main()
