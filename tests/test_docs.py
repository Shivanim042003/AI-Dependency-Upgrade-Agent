from pmigrate.docs import MigrationDocs, split_sections, tokenize

MIGRATION = """# Migration guide

## Changes to pydantic.BaseModel

### Methods

`parse_obj` is deprecated, use `model_validate` instead.
`.dict()` becomes `model_dump()`.

## Changes to validators

`@validator` is deprecated; use `@field_validator` with `@classmethod`.

## BaseSettings has moved

`BaseSettings` now lives in the `pydantic-settings` package.
"""
HISTORY = """## v2.11.0 (2025-03-01)

* Future feature that must be filtered out when targeting 2.10.

## v2.5.0 (2024-09-01)

### Fixes

* Fix `validator` deprecation warning text.

## v1.10.0 (2022-01-01)

* Ancient v1 change that must be filtered out.
"""


def make_docs(tmp_path, target="2.10.0"):
    d = tmp_path / "v2.10.0"
    d.mkdir()
    (d / "migration.md").write_text(MIGRATION)
    (d / "changelog.md").write_text(HISTORY)
    return MigrationDocs("v2.10.0", docs_dir=tmp_path, target_version=target)


def test_tokenize_splits_snake_case():
    assert {"parse_obj", "parse", "obj"} <= set(tokenize("parse_obj"))


def test_sections_have_heading_paths_and_lines():
    secs = split_sections(MIGRATION, "migration", "v2.10.0")
    paths = [s.heading for s in secs]
    assert any(p.endswith("Changes to pydantic.BaseModel > Methods") for p in paths)
    assert all(s.line >= 1 and "#L" in s.url for s in secs)


def test_retrieval_finds_right_section_with_provenance(tmp_path):
    d = make_docs(tmp_path)
    h = d.search("parse_obj replacement", k=1)[0]
    assert "Methods" in h.heading and h.source == "migration" and h.tag == "v2.10.0"
    assert h.url.startswith("https://github.com/pydantic/pydantic/blob/v2.10.0/docs/migration.md#L")
    assert d.search("BaseSettings moved", k=1)[0].heading.endswith("BaseSettings has moved")


def test_changelog_is_version_filtered(tmp_path):
    d = make_docs(tmp_path)
    rel = {s.release for s in d.sections if s.source == "changelog"}
    assert rel == {"2.5.0"}      # 2.11.0 is newer than target, 1.10.0 predates v2
