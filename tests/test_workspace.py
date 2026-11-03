import pytest

from pmigrate.workspace import Workspace, WorkspaceError, init_fresh_repo


@pytest.fixture
def ws(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "a.py").write_text("from pydantic import validator\nx = 1\nx = 1\n")
    (repo / "tests" / "test_a.py").write_text("def test(): pass\n")
    base = init_fresh_repo(repo)
    return Workspace(repo, base)


def test_path_escape_blocked(ws):
    for bad in ["../x", "/etc/passwd", "src/../../x", ".git/config"]:
        with pytest.raises(WorkspaceError):
            ws.read_file(bad)


def test_symlink_escape_blocked(ws, tmp_path):
    (tmp_path / "secret.txt").write_text("s")
    (ws.root / "link").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(WorkspaceError):
        ws.read_file("link")


def test_protected_edit_denied_and_logged(ws):
    with pytest.raises(WorkspaceError):
        ws.write_file("tests/test_a.py", "def test(): assert False\n")
    with pytest.raises(WorkspaceError):
        ws.edit_file("tests/test_a.py", "pass", "assert 0")
    assert ws.denied_edits == ["tests/test_a.py", "tests/test_a.py"]
    assert ws.diff() == ""


def test_edit_requires_unique_match(ws):
    with pytest.raises(WorkspaceError, match="matches 2"):
        ws.edit_file("src/a.py", "x = 1", "x = 2")
    ws.edit_file("src/a.py", "from pydantic import validator", "from pydantic import field_validator")
    assert "field_validator" in ws.diff()


def test_snapshot_restore_roundtrip(ws):
    ws.edit_file("src/a.py", "from pydantic import validator", "from pydantic import field_validator")
    ws.snapshot()
    ws.write_file("src/b.py", "junk\n")
    ws.edit_file("src/a.py", "field_validator", "other")
    ws.restore_best()
    assert not (ws.root / "src" / "b.py").exists()
    assert "field_validator" in (ws.root / "src" / "a.py").read_text()
    assert "src/a.py" in ws.diff()      # diff is always against the ORIGINAL base


def test_search_and_list(ws):
    assert "src/a.py:1:" in ws.search(r"validator")
    assert "(no matches)" == ws.search("zzzz")
    assert "src/a.py" in ws.list_files()


def test_edit_tolerates_whitespace_differences(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "m.py").write_text("class A:\n    x = 1   \n    y = 2\n")
    w = Workspace(repo, init_fresh_repo(repo))
    # agent supplies different indentation and drops trailing spaces
    out = w.edit_file("src/m.py", "  x = 1\n  y = 2", "  x = 10\n  y = 20")
    assert "ignoring whitespace" in out
    assert (repo / "src" / "m.py").read_text() == "class A:\n    x = 10\n    y = 20\n"


def test_edit_whitespace_tolerance_refuses_ambiguity_and_content_changes(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "m.py").write_text("def f():\n    x = 1\ndef g():\n    x = 1\n")
    w = Workspace(repo, init_fresh_repo(repo))
    with pytest.raises(WorkspaceError, match="matches 2 places ignoring whitespace"):
        w.edit_file("src/m.py", "        x = 1", "        x = 2")
    with pytest.raises(WorkspaceError, match="not found"):
        w.edit_file("src/m.py", "    x = 99", "    x = 2")      # different content is never matched


def test_ambiguous_edit_reports_line_numbers(ws):
    with pytest.raises(WorkspaceError, match=r"lines 2, 3"):
        ws.edit_file("src/a.py", "x = 1", "x = 2")


def test_edit_whitespace_tolerance_skips_tab_indented_files(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "m.py").write_text("class A:\n\tx = 1\n")
    w = Workspace(repo, init_fresh_repo(repo))
    with pytest.raises(WorkspaceError, match="not found"):
        w.edit_file("src/m.py", "    x = 1", "    x = 2")
