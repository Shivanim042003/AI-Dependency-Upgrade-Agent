"""A tiny git repo that undergoes a real Pydantic v1 -> v2 migration.

Used by the test-suite, by `pmigrate init-toy`, and as the offline demo task, so the whole pipeline
(prepare -> agent -> verify -> eval) can be exercised without depending on third-party repositories.
This is a *smoke-test fixture*, not part of the benchmark's reported results.
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

PYPROJECT_V1 = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "toyapp"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = ["pydantic>=1.10,<2"]

[tool.setuptools.packages.find]
where = ["src"]
"""
PYPROJECT_V2 = PYPROJECT_V1.replace('"pydantic>=1.10,<2"', '"pydantic>=2.5", "pydantic-settings>=2"')

MODELS_V1 = '''\
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field, root_validator, validator
from pydantic.generics import GenericModel

T = TypeVar("T")


class Address(BaseModel):
    city: str
    zip: str = Field(..., regex=r"^\\d{5}$")


class User(BaseModel):
    id: int
    name: str
    email: str
    nickname: Optional[str]
    tags: List[str] = Field(default_factory=list, max_items=5)
    address: Optional[Address] = None

    class Config:
        orm_mode = True
        anystr_strip_whitespace = True

    @validator("email")
    def email_ok(cls, v):
        if "@" not in v:
            raise ValueError("bad email")
        return v.lower()

    @root_validator
    def name_not_email(cls, values):
        if values.get("name") == values.get("email"):
            raise ValueError("name must differ from email")
        return values


class Page(GenericModel, Generic[T]):
    items: List[T]
    total: int
'''
MODELS_V2 = '''\
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")


class Address(BaseModel):
    city: str
    zip: str = Field(..., pattern=r"^\\d{5}$")


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: int
    name: str
    email: str
    nickname: Optional[str] = None
    tags: List[str] = Field(default_factory=list, max_length=5)
    address: Optional[Address] = None

    @field_validator("email")
    @classmethod
    def email_ok(cls, v):
        if "@" not in v:
            raise ValueError("bad email")
        return v.lower()

    @model_validator(mode="after")
    def name_not_email(self):
        if self.name == self.email:
            raise ValueError("name must differ from email")
        return self


class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
'''
SERVICE_V1 = '''\
import json

from .models import Page, User


def create_user(payload: dict) -> dict:
    user = User.parse_obj(payload)
    return user.dict(exclude_none=True)


def user_json(payload: dict) -> str:
    return User.parse_obj(payload).json()


def user_schema() -> dict:
    return User.schema()


def from_object(obj) -> User:
    return User.from_orm(obj)


def clone_with(user: User, **changes) -> User:
    return user.copy(update=changes)


def page_of(users: list, total: int) -> dict:
    return Page[User](items=users, total=total).dict()


def load(raw: str) -> User:
    return User.parse_raw(raw)
'''
SERVICE_V2 = '''\
import json

from .models import Page, User


def create_user(payload: dict) -> dict:
    user = User.model_validate(payload)
    return user.model_dump(exclude_none=True)


def user_json(payload: dict) -> str:
    return User.model_validate(payload).model_dump_json()


def user_schema() -> dict:
    return User.model_json_schema()


def from_object(obj) -> User:
    return User.model_validate(obj)


def clone_with(user: User, **changes) -> User:
    return user.model_copy(update=changes)


def page_of(users: list, total: int) -> dict:
    return Page[User](items=users, total=total).model_dump()


def load(raw: str) -> User:
    return User.model_validate_json(raw)
'''
SETTINGS_V1 = '''\
from pydantic import BaseSettings


class Settings(BaseSettings):
    app_name: str = "toy"
    debug: bool = False

    class Config:
        env_prefix = "TOY_"
'''
SETTINGS_V2 = '''\
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TOY_")

    app_name: str = "toy"
    debug: bool = False
'''
TESTS = '''\
import json
from types import SimpleNamespace

import pytest

from toyapp import service
from toyapp.settings import Settings


def test_create_user_minimal():
    out = service.create_user({"id": 1, "name": "Ann", "email": "ANN@x.io"})
    assert out == {"id": 1, "name": "Ann", "email": "ann@x.io", "tags": []}


def test_strip_whitespace():
    out = service.create_user({"id": 2, "name": "  Bob ", "email": "b@x.io"})
    assert out["name"] == "Bob"


def test_bad_email_rejected():
    with pytest.raises(Exception):
        service.create_user({"id": 3, "name": "Cy", "email": "nope"})


def test_name_equals_email_rejected():
    with pytest.raises(Exception):
        service.create_user({"id": 4, "name": "a@b.io", "email": "a@b.io"})


def test_zip_pattern():
    ok = service.create_user({"id": 5, "name": "D", "email": "d@x.io", "address": {"city": "X", "zip": "12345"}})
    assert ok["address"]["zip"] == "12345"
    with pytest.raises(Exception):
        service.create_user({"id": 6, "name": "E", "email": "e@x.io", "address": {"city": "X", "zip": "1"}})


def test_tag_limit():
    with pytest.raises(Exception):
        service.create_user({"id": 7, "name": "F", "email": "f@x.io", "tags": list("abcdef")})


def test_json_roundtrip():
    raw = service.user_json({"id": 8, "name": "G", "email": "g@x.io", "nickname": "gg"})
    assert json.loads(raw)["nickname"] == "gg"
    assert service.load(raw).id == 8


def test_schema_has_properties():
    schema = service.user_schema()
    assert "email" in schema["properties"]


def test_from_object():
    obj = SimpleNamespace(id=9, name="H", email="h@x.io", nickname=None, tags=[], address=None)
    assert service.from_object(obj).email == "h@x.io"


def test_copy_update():
    u = service.from_object(SimpleNamespace(id=10, name="I", email="i@x.io", nickname=None, tags=[], address=None))
    assert service.clone_with(u, name="J").name == "J"


def test_generic_page():
    u = service.from_object(SimpleNamespace(id=11, name="K", email="k@x.io", nickname=None, tags=[], address=None))
    page = service.page_of([u], 1)
    assert page["total"] == 1 and page["items"][0]["id"] == 11


def test_settings_env(monkeypatch):
    monkeypatch.setenv("TOY_APP_NAME", "custom")
    assert Settings().app_name == "custom"
'''


def w(root: Path, rel: str, content: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def g(root: Path, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root,
                          capture_output=True, text=True, check=True).stdout.strip()


def build(path: Path) -> dict:
    path.mkdir(parents=True, exist_ok=True)
    g(path, "init", "-q", "-b", "main")
    w(path, "pyproject.toml", PYPROJECT_V1)
    w(path, ".gitignore", "*.egg-info/\n__pycache__/\n")
    w(path, "src/toyapp/__init__.py", "")
    w(path, "src/toyapp/models.py", MODELS_V1)
    w(path, "src/toyapp/service.py", SERVICE_V1)
    w(path, "src/toyapp/settings.py", SETTINGS_V1)
    w(path, "tests/test_service.py", TESTS)
    g(path, "add", "-A"); g(path, "commit", "-q", "-m", "toyapp on pydantic v1")
    base = g(path, "rev-parse", "HEAD")
    w(path, "pyproject.toml", PYPROJECT_V2)
    w(path, "src/toyapp/models.py", MODELS_V2)
    w(path, "src/toyapp/service.py", SERVICE_V2)
    w(path, "src/toyapp/settings.py", SETTINGS_V2)
    g(path, "add", "-A"); g(path, "commit", "-q", "-m", "Migrate to pydantic v2")
    ref = g(path, "rev-parse", "HEAD")
    return {"path": str(path), "base_sha": base, "reference_sha": ref}


if __name__ == "__main__":
    print(json.dumps(build(Path(sys.argv[1]))))


def scripted_solution():
    """Tool calls of a correct migration, for replay mode and tests (no LLM needed)."""
    from .llm import tool_call
    return [
        tool_call("run_tests"),
        tool_call("search", pattern=r"pydantic"),
        tool_call("write_file", path="src/toyapp/models.py", content=MODELS_V2),
        tool_call("write_file", path="src/toyapp/service.py", content=SERVICE_V2),
        tool_call("write_file", path="src/toyapp/settings.py", content=SETTINGS_V2),
        tool_call("write_file", path="pyproject.toml", content=PYPROJECT_V2),
        tool_call("run_tests"),
        tool_call("finish", status="done", summary="Migrated models, service and settings to the native Pydantic v2 API."),
    ]


def init_toy_task(tasks_dir=None, backend="auto", log=print):
    """Build the fixture repo in the cache and register it as task `toy-users`."""
    from .config import CACHE_DIR, TASKS_DIR
    from .prepare import prepare_task
    from .tasks import TaskSpec
    import shutil
    dest = CACHE_DIR / "fixtures" / "toyrepo"
    if dest.exists():
        shutil.rmtree(dest)
    info = build(dest)
    t = TaskSpec(id="toy-users", repo_url=info["path"], base_sha=info["base_sha"], reference_sha=info["reference_sha"],
                 python_version="3.11", pydantic_before="1.10.13", pydantic_after="2.10.0",
                 extra_deps_after=["pydantic-settings==2.6.1"], test_cmd="pytest -q", split="dev", synthetic=True,
                 notes="synthetic smoke-test fixture; not part of the reported benchmark")
    kw = {"tasks_dir": tasks_dir} if tasks_dir else {}
    return prepare_task(t, backend=backend, log=log, **kw)
