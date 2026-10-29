"""Static inventory of Pydantic v1-style usages. Powers the optional planning step.

This is intentionally a coarse, regex-level scan: its job is to hand the agent a *complete list
of places to look* before it starts editing file-by-file. Method-call rules are candidates only
(`.dict(`/`.copy(`/`.json(` also exist on non-Pydantic objects).
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from .env import is_protected
from .workspace import SKIP_DIRS

RULES = [
    ("validators", r"@\s*(validator|root_validator)\b|import\s+.*\b(validator|root_validator)\b",
     "-> @field_validator(..., mode='before'|'after') / @model_validator(mode=...); validators become classmethods"),
    ("config_class", r"^\s*class\s+Config\b", "-> model_config = ConfigDict(...) (BaseSettings: SettingsConfigDict)"),
    ("config_keys",
     r"\b(orm_mode|allow_population_by_field_name|allow_mutation|anystr_strip_whitespace|anystr_lower|"
     r"schema_extra|validate_all|keep_untouched|json_encoders|min_anystr_length|max_anystr_length|"
     r"smart_union|copy_on_model_validation|underscore_attrs_are_private)\b",
     "-> renamed ConfigDict keys (from_attributes, populate_by_name, str_strip_whitespace, json_schema_extra, ...)"),
    ("field_args", r"\b(regex|min_items|max_items|unique_items|const)\s*=",
     "-> Field(pattern=, min_length=, max_length=); const -> Literal"),
    ("moved_modules",
     r"from\s+pydantic\.(generics|fields|error_wrappers|utils|typing|class_validators|main|json|env_settings|"
     r"datetime_parse|parse|tools|schema|color)\b|from\s+pydantic\s+import\s+.*"
     r"\b(BaseSettings|GenericModel|parse_obj_as|parse_file_as|schema_of|ModelField|BaseConfig|Extra|"
     r"validate_arguments|PyObject)\b",
     "-> BaseSettings in pydantic_settings; GenericModel -> BaseModel+Generic; parse_obj_as -> TypeAdapter; ..."),
    ("model_methods",
     r"\.(dict|json|parse_obj|parse_raw|parse_file|from_orm|copy|construct|schema|schema_json|"
     r"update_forward_refs)\(|__fields__|__fields_set__",
     "-> model_dump / model_dump_json / model_validate / model_validate_json / model_copy / "
     "model_construct / model_json_schema / model_rebuild / model_fields (candidates: check receiver type)"),
    ("optional_no_default", r"^\s{2,}\w+\s*:\s*(Optional\[.*\]|.*\|\s*None|Union\[.*None\])\s*(#.*)?$",
     "-> BEHAVIOUR CHANGE: Optional[X] without a default is now REQUIRED in v2; add `= None` to keep v1 semantics"),
    ("root_models", r"\b__root__\b", "-> RootModel[T]"),
    ("validator_kwargs", r"\b(each_item|always)\s*=\s*True|\bpre\s*=\s*True",
     "-> mode='before'; each_item -> validate inner type; always -> Field(validate_default=True)"),
    ("generic_models", r"\bGenericModel\b", "-> class M(BaseModel, Generic[T])"),
]
_COMPILED = [(rid, re.compile(rx), hint) for rid, rx, hint in RULES]


def scan_usages(repo: Path, max_examples: int = 8, include_tests: bool = False) -> dict:
    """Returns {rule_id: {"hint", "count", "files", "examples": [...]}} for non-test sources."""
    repo = Path(repo)
    found: dict = defaultdict(lambda: {"count": 0, "files": set(), "examples": []})
    test_hits = 0
    for dp, dns, fns in os.walk(repo):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            if not f.endswith(".py"):
                continue
            fp = Path(dp) / f
            rel = fp.relative_to(repo).as_posix()
            protected = is_protected(rel)
            try:
                lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, ln in enumerate(lines, 1):
                for rid, rx, _ in _COMPILED:
                    if rx.search(ln):
                        if protected and not include_tests:
                            test_hits += 1
                            continue
                        d = found[rid]
                        d["count"] += 1
                        d["files"].add(rel)
                        if len(d["examples"]) < max_examples:
                            d["examples"].append(f"{rel}:{i}: {ln.strip()[:110]}")
    out = {}
    for rid, _, hint in _COMPILED:
        if rid in found:
            d = found[rid]
            out[rid] = {"hint": hint, "count": d["count"], "files": sorted(d["files"]), "examples": d["examples"]}
    out["_meta"] = {"protected_file_hits_skipped": test_hits}
    return out


def format_inventory(inv: dict) -> str:
    lines = ["Static scan of v1-style Pydantic usages in NON-test source files (coarse; verify each):"]
    for rid, d in inv.items():
        if rid.startswith("_"):
            continue
        lines.append(f"\n## {rid}: {d['count']} hits in {len(d['files'])} file(s)\n   {d['hint']}")
        for e in d["examples"]:
            lines.append(f"   {e}")
        if d["count"] > len(d["examples"]):
            lines.append(f"   ... and {d['count'] - len(d['examples'])} more (use `search` for the full list)")
    if len(lines) == 1:
        lines.append("(no obvious v1 usages found; failures may come from behaviour changes)")
    return "\n".join(lines)
