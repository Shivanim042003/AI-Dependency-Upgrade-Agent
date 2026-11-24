"""Acceptance checks for an execution backend. Run it before trusting any result produced on that backend.

    python scripts/validate_backend.py --backend docker --task toy-users

Checks: secrets not visible, timeout kills the process, network off during tests, writes outside the run dir
blocked, original base state green under v1, forced-upgrade start state fails, human reference passes,
empty patch fails, protected-file edits rejected. Exit code is non-zero if any check fails.
The `local` backend is expected to fail the isolation checks: that is exactly why it is not a security boundary.
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

from pmigrate.config import CACHE_DIR
from pmigrate.env import Env
from pmigrate.prepare import reference_source_diff
from pmigrate.sandbox import make_sandbox
from pmigrate.tasks import load_task
from pmigrate.verify import verify_patch
from pmigrate.workspace import prepare_workspace

ap = argparse.ArgumentParser()
ap.add_argument("--backend", default="docker")
ap.add_argument("--task", default="toy-users")
a = ap.parse_args()

results = []


def check(name, ok, detail="", isolation=False):
    results.append((name, ok, detail, isolation))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


os.environ["PMIGRATE_API_KEY"] = "canary-secret-compat"
os.environ["OPENAI_API_KEY"] = "canary-secret-openai"
runs = CACHE_DIR / "runs"
runs.mkdir(parents=True, exist_ok=True)
sb = make_sandbox(Path(tempfile.mkdtemp(prefix="validate-", dir=runs)), a.backend)
print(f"backend under test: {sb.name}\n")

for net in (False, True):
    r = sb.exec("env", cwd=".", timeout=20, network=net)
    check(f"no API credentials visible (network={net})", "canary-secret" not in r.stdout + r.stderr, isolation=True)

t0 = time.time()
r = sb.exec("sleep 60", cwd=".", timeout=3)
check("timeout terminates the command", r.timed_out and time.time() - t0 < 20, f"{time.time() - t0:.1f}s")

r = sb.exec("timeout 3 bash -c 'echo > /dev/tcp/1.1.1.1/53'", cwd=".", timeout=15, network=False)
check("network blocked during tests", r.returncode != 0, f"rc={r.returncode} isolated={r.isolated_network}", isolation=True)

r = sb.exec("touch /etc/pmigrate-escape-test && echo WROTE", cwd=".", timeout=15)
check("cannot write outside the run dir", "WROTE" not in r.stdout, isolation=True)
sb.exec("rm -f /etc/pmigrate-escape-test", cwd=".", timeout=5)
canary = Path("/tmp/pmigrate-host-canary"); canary.write_text("host file")
r = sb.exec("cat /tmp/pmigrate-host-canary", cwd=".", timeout=15)
check("host files outside mounts not readable", "host file" not in r.stdout, isolation=True)
canary.unlink(missing_ok=True)

task = load_task(a.task)
base_commit = prepare_workspace(task, sb.repo_dir)
v1 = Env(sb, task, "v1"); ok = v1.setup().ok
check("v1 environment builds in this backend", ok)
if ok:
    run = v1.run_tests(junit_name="v1.xml")
    check("original base is green under v1", run.green, str(run.counts()))
import shutil
shutil.rmtree(sb.run_dir / "venv", ignore_errors=True)       # fresh venv for the v2 state
v2 = Env(sb, task, "v2"); r2 = v2.setup()
check("v2 environment builds in this backend", r2.ok, "" if r2.ok else r2.tail(200))
if r2.ok:
    run = v2.run_tests(strict=task.policy == "native_v2", junit_name="v2.xml")
    check("forced-upgrade starting state fails", not run.green, str(run.counts()))

src, _ = reference_source_diff(task)
v = verify_patch(task, src, backend=a.backend)
check("human reference passes clean-room verification", v.success, f"violations={[x['code'] for x in v.violations]}")
check("empty patch fails", not verify_patch(task, "", backend=a.backend).success)
cheat = "diff --git a/tests/x.py b/tests/x.py\n--- a/tests/x.py\n+++ b/tests/x.py\n@@ -1 +1 @@\n+pass\n"
check("protected-file edit rejected", not verify_patch(task, cheat, backend=a.backend).success)
sb.cleanup()

bad = [r for r in results if not r[1] and (a.backend == "docker" or not r[3])]
print(f"\n{len(results) - len([r for r in results if not r[1]])}/{len(results)} checks passed on '{sb.name}'")
if sb.name == "local":
    print("local backend: isolation failures above are expected and are why it must not run untrusted code.")
sys.exit(1 if bad else 0)
