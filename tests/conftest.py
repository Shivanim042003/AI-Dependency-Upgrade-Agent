import os
import shutil

import pytest

# tests must not depend on the developer's shell (PMIGRATE_MODEL, PMIGRATE_MAX_TOKENS, API keys ...)
for _k in [k for k in os.environ if k.startswith("PMIGRATE_")]:
    del os.environ[_k]


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs uv + network (builds real venvs)")


@pytest.fixture
def task():
    from pmigrate.tasks import TaskSpec
    return TaskSpec(id="t", repo_url="x", base_sha="a", reference_sha="b", status="reproducible")


needs_uv = pytest.mark.skipif(not shutil.which("uv"), reason="uv not installed")
