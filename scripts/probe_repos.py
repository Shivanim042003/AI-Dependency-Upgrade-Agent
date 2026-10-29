"""Clone candidate repos and list likely v1->v2 upgrade commits (see `pmigrate discover`).

usage: python scripts/probe_repos.py https://github.com/org/repo [...]
"""
import json
import sys
import time

from pmigrate.prepare import discover_upgrade_commits
from pmigrate.tasks import TaskSpec
from pmigrate.workspace import upstream_clone

for url in sys.argv[1:]:
    tid = url.rstrip("/").split("/")[-1].lower()
    t0 = time.time()
    try:
        up = upstream_clone(TaskSpec(id=tid, repo_url=url, base_sha="HEAD", reference_sha="HEAD"))
        c = discover_upgrade_commits(up, limit=4)
        print(f"\n== {url}  ({time.time() - t0:.0f}s) {len(c)} candidate(s)")
        print(json.dumps(c, indent=1))
    except Exception as e:
        print(f"\n== {url}: ERROR {e}")
