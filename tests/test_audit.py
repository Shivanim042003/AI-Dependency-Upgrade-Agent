from pmigrate.audit import audit_patch


def d(path, removed=(), added=()):
    return "\n".join([f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", "@@ -1 +1 @@",
                      *[f"-{x}" for x in removed], *[f"+{x}" for x in added]]) + "\n"


def test_mechanical_rename_is_not_flagged():
    a = audit_patch(d("tests/t.py", ["    assert m.dict() == {'a': 1}"], ["    assert m.model_dump() == {'a': 1}"]))[0]
    assert a.mechanical == 1 and a.flags == []


def test_removed_test_and_assertions_flagged():
    a = audit_patch(d("tests/t.py", ["def test_old():", "    assert x == 1", "    assert y == 2"], ["def test_new():", "    assert x == 1"]))[0]
    assert a.tests_removed == ["test_old"] and a.asserts_removed == 2 and a.asserts_added == 1
    assert any("REMOVED TESTS" in f for f in a.flags) and any("NET ASSERTIONS" in f for f in a.flags)


def test_exception_type_and_literal_changes_flagged():
    a = audit_patch(d("tests/t.py", ["with pytest.raises(ValueError):", "    assert str(e) == 'old msg'"],
                      ["with pytest.raises(Exception):", "    assert str(e) == 'new msg'"]))[0]
    assert any("EXCEPTION TYPE CHANGED" in f for f in a.flags) and any("EXPECTED VALUES CHANGED" in f for f in a.flags)


def test_skip_added_flagged():
    a = audit_patch(d("tests/t.py", [], ["@pytest.mark.skip"]))[0]
    assert any("SKIP" in f for f in a.flags)
