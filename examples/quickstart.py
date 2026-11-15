"""Bring your own graph: a tiny company graph where a fact goes stale, and the graph heals.

python examples/quickstart.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from physarum import Graph

g = Graph.from_csv(Path(__file__).with_name("company.csv"))

a = g.ask("Bob", "reports_to", "leads")
print(f"Which team is Bob in?     {a.answer:10} via {' → '.join(a.path)}  (confidence {a.confidence:.0%})")

# Bob moved to Dana's team. The pipeline appends the new fact; the old one stays.
g.add("Bob", "reports_to", "Dana")
a = g.ask("Bob", "reports_to", "leads")
print(f"After the reorg (no votes): {a.answer:10} unsure={a.unsure}  alternatives={a.alternatives}")

for i in range(1, 6):
    if a.answer == "Growth":
        break
    a = g.feedback(a, correct=False)  # a user says: that's outdated
    print(f"  👎 #{i}: now answers {a.answer:10} (confidence {a.confidence:.0%})")
a = g.feedback(a, correct=True)
print(f"Healed:                   {a.answer:10} via {' → '.join(a.path)}  (confidence {a.confidence:.0%})")
print(f"And where is Bob based?   {g.ask('Bob', 'reports_to', 'leads', 'based_in').answer}  (3-hop, learned for free)")
print("Facts the graph stopped trusting:", g.audit())
