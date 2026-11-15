"""Fill in your GitHub user and live URL everywhere, in one go.

python scripts/set_links.py --github yourname --url https://physarum.up.railway.app
"""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "README.md",
    "pyproject.toml",
    "server.json",
    "mcp_server.py",
    "static/index.html",
    "docs/DISTRIBUTION.md",
    "docs/LAUNCH.md",
]

ap = argparse.ArgumentParser()
ap.add_argument("--github", required=True, help="your GitHub username")
ap.add_argument("--repo", default="physarum")
ap.add_argument("--url", required=True, help="live URL, e.g. https://physarum.up.railway.app")
a = ap.parse_args()
url = a.url.rstrip("/")
host = url.split("://", 1)[-1]
pairs = [
    ("YOUR_GITHUB/physarum", f"{a.github}/{a.repo}"),
    ("YOUR_GITHUB", a.github),
    ("https://YOUR-DEPLOYMENT", url),
    ("YOUR-DEPLOYMENT", host),
    ("https://<your-deployment>", url),
    ("https://your-deployment", url),
    ("<LIVE>", url),
    ("<REPO>", f"https://github.com/{a.github}/{a.repo}"),
    ('href="https://github.com/" id="ghLink"', f'href="https://github.com/{a.github}/{a.repo}" id="ghLink"'),
]
for f in FILES:
    p = ROOT / f
    s = p.read_text()
    t = s
    for old, new in pairs:
        t = t.replace(old, new)
    if t != s:
        p.write_text(t)
        print("updated", f)
