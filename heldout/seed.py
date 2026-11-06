"""Seed one-token bugs into pinned projects and keep the ones the test suite catches.

usage: python -m heldout.seed rich --target 60 --max-candidates 220
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import random
import time

from . import coverage_map
from .config import DATA, MAX_FUNC_LINES, REPOS, repo_dir
from .mutate import edits_in_function, functions
from .runner import clear_pycache, failed_tests, run_pytest

SEED = 2026


def baseline(repo: str):
    """Tests that already fail (or flake) on the clean checkout. Two runs, union."""
    clear_pycache(repo)
    bad, walls, n = set(), [], 0
    for _ in range(2):
        r = run_pytest(repo)
        bad |= failed_tests(r["results"])
        walls.append(r["wall"])
        n = len(r["results"])
    info = {"repo": repo, "baseline_failures": sorted(bad), "n_tests": n, "suite_wall_s": walls}
    DATA.mkdir(exist_ok=True)
    (DATA / f"baseline_{repo}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def unified(path: str, a: bytes, b: bytes) -> str:
    return "".join(difflib.unified_diff(a.decode().splitlines(True), b.decode().splitlines(True),
                                        f"a/{path}", f"b/{path}", n=2))


def candidates(repo: str, cmap: dict, rng: random.Random):
    root = repo_dir(repo)
    pkg = REPOS[repo]["package"]
    out = []
    for path in sorted((root / pkg).rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if "/_vendor" in rel or rel.endswith("__main__.py"):
            continue
        covered = {int(k) for k in cmap["files"].get(rel, {})}
        if not covered:
            continue
        src = path.read_bytes()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for fn in functions(tree, MAX_FUNC_LINES):
            eds = edits_in_function(src, fn, include_not_insert=False, lines=covered)
            if eds:
                out.append((rel, fn.name, fn.lineno, fn.end_lineno, eds))
    rng.shuffle(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--target", type=int, default=60)
    ap.add_argument("--max-candidates", type=int, default=220)
    a = ap.parse_args()
    repo = a.repo
    rng = random.Random(f"{SEED}-{repo}")
    t0 = time.time()
    base = baseline(repo)
    ignore = set(base["baseline_failures"])
    print(f"[{repo}] baseline: {base['n_tests']} tests, {len(ignore)} failing, walls {base['suite_wall_s']}", flush=True)
    cmap = coverage_map.build(repo, sorted(ignore))
    print(f"[{repo}] coverage map: {len(cmap['tests'])} test contexts, {len(cmap['files'])} files", flush=True)
    cands = candidates(repo, cmap, rng)
    print(f"[{repo}] {len(cands)} candidate functions", flush=True)
    root = repo_dir(repo)
    bugs, log = [], []
    for k, (rel, fname, l0, l1, eds) in enumerate(cands[: a.max_candidates]):
        if len(bugs) >= a.target:
            break
        ed = rng.choice(eds)
        path = root / rel
        orig = path.read_bytes()
        mutated = ed.apply(orig)
        try:
            path.write_bytes(mutated)
            clear_pycache(repo)
            r = run_pytest(repo, deselect=sorted(ignore))
        finally:
            path.write_bytes(orig)
        fails = failed_tests(r["results"], ignore)
        status = "hang" if r["timed_out"] else ("killed" if fails else "survived")
        if "<collection>" in fails or any("::" not in f for f in fails):
            status = "collection_error"
        log.append({"k": k, "file": rel, "function": fname, "edit": ed.to_dict(), "status": status,
                    "n_fail": len(fails), "wall": r["wall"]})
        if status == "killed":
            fails_sorted = sorted(fails)
            bug_id = f"{repo}-{len(bugs):03d}"
            visible = random.Random(bug_id).choice(fails_sorted)
            bugs.append({
                "id": bug_id, "repo": repo, "file": rel, "function": fname,
                "func_start": l0, "func_end": l1, "edit": ed.to_dict(),
                "failing_tests": fails_sorted, "visible_test": visible,
                "diff": unified(rel, orig, mutated),
            })
        print(f"[{repo}] cand {k}: {rel}:{ed.lineno} {ed.op} {ed.old!r}->{ed.new!r} {status} "
              f"fails={len(fails)} {r['wall']:.1f}s  bugs={len(bugs)}", flush=True)
    clear_pycache(repo)
    (DATA / f"bugs_{repo}.jsonl").write_text("".join(json.dumps(b) + "\n" for b in bugs), encoding="utf-8")
    (DATA / f"seedlog_{repo}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in log), encoding="utf-8")
    print(f"[{repo}] done: {len(bugs)} bugs from {len(log)} candidates in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
