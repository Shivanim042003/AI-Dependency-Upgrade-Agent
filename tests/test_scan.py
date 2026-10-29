from pmigrate.scan import format_inventory, scan_usages

SRC = '''
from pydantic import BaseModel, validator
from pydantic.generics import GenericModel

class M(BaseModel):
    name: str
    nick: Optional[str]
    class Config:
        orm_mode = True

    @validator("name", pre=True)
    def v(cls, x): return x

def f(m): return M.parse_obj(m).dict()
'''


def test_scan_finds_v1_patterns_and_skips_tests(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "m.py").write_text(SRC)
    (tmp_path / "tests" / "test_m.py").write_text("M.parse_obj({})\n")
    inv = scan_usages(tmp_path)
    for rule in ["validators", "config_class", "config_keys", "model_methods", "optional_no_default",
                 "generic_models", "validator_kwargs", "moved_modules"]:
        assert rule in inv, rule
    assert all("tests/" not in e for d in inv.values() if "examples" in d for e in d["examples"])
    assert inv["_meta"]["protected_file_hits_skipped"] >= 1
    assert "Optional[X] without a default is now REQUIRED" in format_inventory(inv)
