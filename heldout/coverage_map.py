"""Per-test line coverage of the clean checkout (coverage.py dynamic contexts)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .config import DATA, REPOS, repo_dir
from .runner import _env, clear_pycache


def build(repo: str, deselect: list[str]) -> dict:
    cwd = repo_dir(repo)
    pkg = REPOS[repo]["package"]
    cov_file = cwd / ".coverage.heldout"
    env = _env(repo)
    env["COVERAGE_FILE"] = str(cov_file)
    clear_pycache(repo)
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore",
            f"--cov={pkg.replace('/', os.sep)}", "--cov-context=test", "--cov-report="]
    for d in deselect:
        args += ["--deselect", d]
    args.append(REPOS[repo]["tests"])
    subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    # Read the coverage database directly. `coverage json --show-contexts` took more
    # than 10 minutes and 2 GB of RAM on click's 2,000 contexts.
    from coverage import CoverageData
    data = CoverageData(basename=str(cov_file))
    data.read()
    tests: list[str] = []
    index: dict[str, int] = {}
    files: dict[str, dict[str, list[int]]] = {}
    for fname in data.measured_files():
        try:
            rel = Path(fname).resolve().relative_to(cwd.resolve()).as_posix()
        except ValueError:
            continue
        lines = {}
        for line, ctxs in data.contexts_by_lineno(fname).items():
            ids = []
            for c in ctxs:
                if not c:
                    continue
                name = c.split("|")[0]
                if name not in index:
                    index[name] = len(tests)
                    tests.append(name)
                ids.append(index[name])
            if ids:
                lines[str(line)] = sorted(set(ids))
        files[rel] = lines
    out_json = cwd / "cov_heldout.json"
    for p in (cov_file, out_json):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    cmap = {"tests": tests, "files": files}
    DATA.mkdir(exist_ok=True)
    (DATA / f"coverage_{repo}.json").write_text(json.dumps(cmap), encoding="utf-8")
    return cmap


def load(repo: str) -> dict:
    return json.loads((DATA / f"coverage_{repo}.json").read_text(encoding="utf-8"))


def tests_covering(cmap: dict, relpath: str, lines) -> set[str]:
    f = cmap["files"].get(relpath, {})
    out = set()
    for ln in lines:
        for i in f.get(str(ln), []):
            out.add(cmap["tests"][i])
    return out
