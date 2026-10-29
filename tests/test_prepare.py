"""Task-preparation helpers that do not need venvs (git only)."""
import subprocess
from pathlib import Path

from pmigrate.policy import check_patch
from pmigrate.prepare import DEPRECATION_ASSERT, reference_source_diff, strip_deprecation_assertions
from pmigrate.tasks import TaskSpec
from pmigrate.workspace import apply_patch, prepare_workspace


def sh(repo: Path, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "up"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    sh(repo, "init", "-q", "-b", "main")
    (repo / "src" / "m.py").write_text("from pydantic import BaseModel\n\n\ndef make():\n    return 1\n")
    (repo / "tests" / "test_a.py").write_text("from src.m import make\n\n\ndef test_a():\n    x = make()\n    assert x == 1\n")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "base")
    base = sh(repo, "rev-parse", "HEAD")
    (repo / "src" / "m.py").write_text("from pydantic.v1 import BaseModel\n\n\ndef make():\n    return 1\n")
    (repo / "tests" / "test_a.py").write_text(
        "import pytest\nfrom src.m import make\n\n\ndef test_a():\n    with pytest.deprecated_call():\n"
        "        x = make()\n    assert x == 1\n")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "ref")
    return TaskSpec(id="prep-t", repo_url=str(repo), base_sha=base, reference_sha=sh(repo, "rev-parse", "HEAD"))


def test_reference_diff_is_split_by_protected_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("pmigrate.workspace.CACHE_DIR", tmp_path / "cache")
    t = make_repo(tmp_path)
    src, tst = reference_source_diff(t)
    assert "src/m.py" in src and "tests/" not in src
    assert "tests/test_a.py" in tst and "src/" not in tst


def test_reference_that_imports_pydantic_v1_violates_policy(tmp_path, monkeypatch):
    monkeypatch.setattr("pmigrate.workspace.CACHE_DIR", tmp_path / "cache")
    t = make_repo(tmp_path)
    src, _ = reference_source_diff(t)
    assert {v.code for v in check_patch(src, t)} == {"PYDANTIC_V1_SHIM"}


def test_deprecation_assertions_are_detected_and_unwrapped(tmp_path, monkeypatch):
    monkeypatch.setattr("pmigrate.workspace.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("pmigrate.verify.CACHE_DIR", tmp_path / "cache")
    t = make_repo(tmp_path)
    _, tst = reference_source_diff(t)
    assert DEPRECATION_ASSERT.search(tst)
    fixed = strip_deprecation_assertions(t, tst)
    assert "deprecated_call" not in fixed
    t.test_adaptation_patch = fixed
    ws = tmp_path / "ws"
    prepare_workspace(t, ws)
    body = (ws / "tests" / "test_a.py").read_text()
    assert "    x = make()\n    assert x == 1" in body and "deprecated_call" not in body
