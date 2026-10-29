"""Sandboxed command execution.

Two interchangeable backends with the same interface:

* LocalSandbox  - subprocess with a scrubbed environment, process-group kill on timeout,
                  CPU/file rlimits and (when the kernel allows it) `unshare -n` for the
                  no-network test phase. Good for development; NOT a security boundary.
* DockerSandbox - disposable container per command: no network during tests, dropped
                  capabilities, memory/CPU/pids limits, only the run directory mounted,
                  no Docker socket, no host environment variables or secrets.

Every attempt gets its own run directory:

    run_dir/
      repo/    the workspace the agent edits (single fresh commit, no upstream history)
      venv/    the Python environment (created inside the sandbox)
      home/    scratch HOME
"""
from __future__ import annotations

import os
import resource
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import CACHE_DIR

MAX_OUT = 400_000  # bytes kept per stream

# Variables forwarded only to phases that are allowed network access (dependency install).
_PROXY_VARS = [
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "UV_NATIVE_TLS", "UV_SYSTEM_CERTS", "GIT_SSL_CAINFO", "PIP_CERT",
]


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    isolated_network: Optional[bool] = None  # None = not applicable / unknown

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def tail(self, n: int = 3000) -> str:
        s = (self.stdout + ("\n" + self.stderr if self.stderr.strip() else "")).strip()
        return s[-n:]


def _trunc(b: bytes) -> str:
    if len(b) > MAX_OUT:
        b = b[:MAX_OUT // 2] + b"\n...[truncated]...\n" + b[-MAX_OUT // 2:]
    return b.decode("utf-8", errors="replace")


_UNSHARE_OK: Optional[bool] = None


def can_unshare_net() -> bool:
    global _UNSHARE_OK
    if _UNSHARE_OK is None:
        if not shutil.which("unshare"):
            _UNSHARE_OK = False
        else:
            try:
                r = subprocess.run(["unshare", "-n", "true"], capture_output=True, timeout=5)
                _UNSHARE_OK = r.returncode == 0
            except Exception:
                _UNSHARE_OK = False
    return _UNSHARE_OK


class Sandbox:
    name = "base"

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "home").mkdir(exist_ok=True)

    # path of run_dir as seen by commands executed in the sandbox
    @property
    def work_root(self) -> str:
        raise NotImplementedError

    @property
    def repo_dir(self) -> Path:          # host-side path
        return self.run_dir / "repo"

    def exec(self, cmd: str, *, cwd: str = "repo", timeout: int = 300,
             network: bool = False, env: Optional[dict] = None) -> RunResult:
        raise NotImplementedError

    def cleanup(self) -> None:
        shutil.rmtree(self.run_dir, ignore_errors=True)


class LocalSandbox(Sandbox):
    name = "local"

    @property
    def work_root(self) -> str:
        return str(self.run_dir)

    def _env(self, network: bool, extra: Optional[dict]) -> dict:
        shared = CACHE_DIR
        env = {
            "PATH": f"{self.run_dir}/venv/bin:" + os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(self.run_dir / "home"),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "UV_CACHE_DIR": str(shared / "uv-cache"),
            "UV_PYTHON_INSTALL_DIR": str(shared / "uv-python"),
            "UV_LINK_MODE": "copy",
            "VIRTUAL_ENV": f"{self.run_dir}/venv",
            "CI": "1",
        }
        if network:
            for k in _PROXY_VARS:
                if k in os.environ:
                    env[k] = os.environ[k]
        if extra:
            env.update(extra)
        return env

    def exec(self, cmd, *, cwd="repo", timeout=300, network=False, env=None) -> RunResult:
        workdir = self.run_dir / cwd
        workdir.mkdir(parents=True, exist_ok=True)
        argv = ["bash", "-c", cmd]
        isolated = None
        if not network:
            if can_unshare_net():
                argv = ["unshare", "-n", "bash", "-c", cmd]
                isolated = True
            else:
                isolated = False

        def _limits():
            os.setsid()
            resource.setrlimit(resource.RLIMIT_CPU, (timeout + 30, timeout + 30))
            resource.setrlimit(resource.RLIMIT_FSIZE, (2 << 30, 2 << 30))

        t0 = time.time()
        p = subprocess.Popen(argv, cwd=workdir, env=self._env(network, env),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             preexec_fn=_limits)
        timed_out = False
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = p.communicate()
        return RunResult(p.returncode if p.returncode is not None else -9,
                         _trunc(out), _trunc(err), timed_out, time.time() - t0, isolated)


class DockerSandbox(Sandbox):
    """One disposable container per command. Requires a running Docker daemon.

    Hardening applied to every container: --cap-drop ALL, no-new-privileges, read-only
    root filesystem (tmpfs /tmp), pids/memory/cpu limits, non-root user, no Docker socket,
    only `run_dir` and the shared uv cache mounted, empty environment (no host secrets),
    and `--network none` for every phase except dependency installation.
    """
    name = "docker"
    IMAGE = os.environ.get("PMIGRATE_IMAGE", "pmigrate-runner:latest")

    def __init__(self, run_dir: Path, memory: str = "2g", cpus: str = "2", pids: int = 512):
        super().__init__(run_dir)
        self.memory, self.cpus, self.pids = memory, cpus, pids

    @property
    def work_root(self) -> str:
        return "/work"

    def exec(self, cmd, *, cwd="repo", timeout=300, network=False, env=None) -> RunResult:
        (CACHE_DIR / "uv-cache").mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "uv-python").mkdir(parents=True, exist_ok=True)
        e = {
            "HOME": "/work/home", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0", "UV_CACHE_DIR": "/uvcache", "UV_LINK_MODE": "copy",
            "UV_PYTHON_INSTALL_DIR": "/uvpython", "VIRTUAL_ENV": "/work/venv",
            "PATH": "/work/venv/bin:/usr/local/bin:/usr/bin:/bin", "CI": "1",
        }
        if network:
            for k in _PROXY_VARS:
                if k in os.environ:
                    e[k] = os.environ[k]
        if env:
            e.update(env)
        name = f"pmigrate-{os.getpid()}-{int(time.time() * 1000)}"
        argv = [
            "docker", "run", "--rm", "--name", name,
            "--network", "bridge" if network else "none",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids), "--memory", self.memory, "--cpus", self.cpus,
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=512m",
            "-v", f"{self.run_dir}:/work",
            "-v", f"{CACHE_DIR / 'uv-cache'}:/uvcache",
            "-v", f"{CACHE_DIR / 'uv-python'}:/uvpython",
            "-w", f"/work/{cwd}",
        ]
        for k, v in e.items():
            argv += ["-e", f"{k}={v}"]
        argv += [self.IMAGE, "bash", "-c", cmd]
        t0 = time.time()
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        timed_out = False
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(["docker", "kill", name], capture_output=True)
            p.kill()
            out, err = p.communicate()
        return RunResult(p.returncode if p.returncode is not None else -9,
                         _trunc(out), _trunc(err), timed_out, time.time() - t0,
                         isolated_network=not network)


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def make_sandbox(run_dir: Path, backend: str = "auto") -> Sandbox:
    if backend == "auto":
        backend = os.environ.get("PMIGRATE_BACKEND") or ("docker" if docker_available() else "local")
    if backend == "docker":
        return DockerSandbox(run_dir)
    if backend == "local":
        return LocalSandbox(run_dir)
    raise ValueError(f"unknown backend {backend}")


def q(s: str) -> str:
    return shlex.quote(s)
