"""Run pytest inside a pinned checkout and collect per-test outcomes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import ROOT, SUITE_TIMEOUT_S, REPOS, repo_dir


def _env(repo: str | None = None):
    env = dict(os.environ)
    paths = [str(ROOT)]
    if repo:
        # import the library from the checkout under test, not from the editable install
        pkg_parent = (repo_dir(repo) / REPOS[repo]["package"]).parent
        paths.insert(0, str(pkg_parent))
    env["PYTHONPATH"] = os.pathsep.join(paths + [env.get("PYTHONPATH", "")])
    # Mutants are written in place, sometimes within the same second and with the
    # same file size as the previous version. Stale .pyc files would then be
    # reused, so bytecode caching is turned off and old caches are removed.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env["NO_COLOR"] = ""
    env.pop("FORCE_COLOR", None)
    env["TERM"] = "dumb"
    env["COLUMNS"] = "80"
    return env


def clear_pycache(repo: str) -> None:
    pkg = repo_dir(repo) / REPOS[repo]["package"]
    for d in pkg.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def to_selector(repo: str, nodeid: str) -> str:
    """Node ids are relative to pytest's rootdir, which is not always the repo root."""
    path = nodeid.split("::")[0]
    if (repo_dir(repo) / path).exists():
        return nodeid
    return f"{REPOS[repo]['tests']}/{nodeid}"


def run_pytest(repo: str, selectors: list[str] | None = None, deselect: list[str] | None = None,
               timeout: float = SUITE_TIMEOUT_S, capture_output: bool = False, extra: list[str] | None = None):
    """Run pytest. `selectors` are node ids; None means the whole test directory.

    Returns dict(results=..., wall=..., timed_out=..., output=...).
    """
    cwd = repo_dir(repo)
    tmpdir = Path(tempfile.mkdtemp(prefix="heldout_"))
    result_path = tmpdir / "res.json"
    args_path = tmpdir / "args.txt"
    # -p options must be on the real command line: pytest reads them before it
    # expands the @argsfile.
    early = ["-p", "no:cacheprovider", "-p", "heldout.pytest_record"]
    args = ["-q", "-o", "console_output_style=classic", "--tb=short", "-W", "ignore"]
    args += extra or []
    for d in deselect or []:
        args += ["--deselect", d]
    args += [to_selector(repo, s) for s in selectors] if selectors else [REPOS[repo]["tests"]]
    args_path.write_text("\n".join(args), encoding="utf-8")
    env = _env(repo)
    env["HELDOUT_RESULT"] = str(result_path)
    t0 = time.perf_counter()
    timed_out = False
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", *early, f"@{args_path}"], cwd=cwd, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        out = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
    wall = time.perf_counter() - t0
    results = {}
    if result_path.exists():
        try:
            results = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            results = {}
    shutil.rmtree(tmpdir, ignore_errors=True)
    return {"results": results, "wall": wall, "timed_out": timed_out, "output": out if capture_output else None}


def failed_tests(results: dict, ignore: set[str] = frozenset()) -> set[str]:
    return {k for k, v in results.items() if v["outcome"] == "failed" and k not in ignore}


def collect_ids(repo: str) -> list[str]:
    """All test node ids of the clean checkout (cached in data/tests_<repo>.json)."""
    import json as _json
    from .config import DATA
    cache = DATA / f"tests_{repo}.json"
    if cache.exists():
        return _json.loads(cache.read_text(encoding="utf-8"))
    r = run_pytest(repo, extra=["--collect-only"], capture_output=True)
    ids = sorted({line.strip() for line in (r["output"] or "").splitlines() if "::" in line and not line.startswith(" ")})
    cache.write_text(_json.dumps(ids), encoding="utf-8")
    return ids
