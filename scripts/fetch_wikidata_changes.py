"""Regenerate data/real_changes.json from LIVE Wikidata (run on a machine with internet).

For every country that has a head of state (P35) in CoDEx, fetch the current holder (statement
without an end date, preferred rank first) and record the ones that differ from the CoDEx snapshot.

    python scripts/fetch_wikidata_changes.py
"""

import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from physarum.data import load_codex  # noqa: E402

kg = load_codex()
old = defaultdict(set)
for s, d, r, t in kg.edges:
    if r == "P35" and t:
        old[kg.qids[s]].add(kg.qids[d])

values = " ".join(f"wd:{q}" for q in old)
query = f"""
SELECT ?country ?holder ?holderLabel ?start WHERE {{
  VALUES ?country {{ {values} }}
  ?country p:P35 ?st . ?st ps:P35 ?holder .
  FILTER NOT EXISTS {{ ?st pq:P582 ?end }}
  OPTIONAL {{ ?st pq:P580 ?start }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}"""
url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query)
req = urllib.request.Request(url, headers={"User-Agent": "physarum-hackathon/1.0"})
rows = json.loads(urllib.request.urlopen(req, timeout=60).read())["results"]["bindings"]

current = defaultdict(list)
for r in rows:
    c = r["country"]["value"].rsplit("/", 1)[1]
    current[c].append(
        (r["holder"]["value"].rsplit("/", 1)[1], r["holderLabel"]["value"], r.get("start", {}).get("value", "")[:10])
    )

groups = defaultdict(lambda: dict(countries=[], date=""))
for c, olds in old.items():
    now = current.get(c, [])
    if not now or {q for q, _, _ in now} & olds:
        continue  # unchanged or no current holder recorded
    q, name, start = sorted(now, key=lambda x: x[2])[-1]
    key = (tuple(sorted(olds)), q)
    g = groups[key]
    g["countries"].append(c)
    g["date"] = max(g["date"], start)
    g["name"] = name
    g["old"] = sorted(olds)[0]

changes = [
    dict(
        event=f"{g['name']} becomes head of state",
        date=g["date"],
        old=g["old"],
        new=dict(qid=q, name=g["name"]),
        countries=g["countries"],
        source=f"https://www.wikidata.org/wiki/{q}",
    )
    for (olds, q), g in groups.items()
]
out = dict(
    description=f"Live Wikidata P35 changes vs CoDEx, fetched {date.today()}",
    relation="P35",
    as_of=str(date.today()),
    changes=changes,
)
(ROOT / "data" / "real_changes.json").write_text(json.dumps(out, indent=1))
print(f"{len(changes)} change groups, {sum(len(c['countries']) for c in changes)} countries -> data/real_changes.json")
