"""Deterministic search baseline: try single-token edits, cheapest check first.

  oracle-line : edits on the seeded line only (the repair space contains the
                exact inverse of the bug, so this is an upper bound for the search)
  sbfl        : edits on lines ranked by Ochiai suspiciousness, computed from the
                visible failing test and the visible passing tests, using the clean
                checkout's coverage. Capped at --cap candidates.

Each candidate is first checked against the visible failing test alone. Only if it
passes is the full suite run, so the per-policy verdicts come from one full run.
The search stops at the first candidate that passes the whole visible suite.

usage: python -m heldout.repair_search --mode oracle-line
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import time
from collections import defaultdict

from . import coverage_map
from .config import RESULTS, REPOS, repo_dir
from .evaluate import BugEnv, load_bugs
from .runner import collect_ids, failed_tests, run_pytest
from .mutate import edits_in_function, functions


def _func_at(tree, line):
    best = None
    for fn in functions(tree, 10 ** 6):
        if fn.lineno <= line <= fn.end_lineno and (best is None or fn.lineno > best.lineno):
            best = fn
    return best


def ochiai_lines(env: BugEnv, cmap):
    """Rank (file, line) by Ochiai with one failing test. Ties: fewer passing covers, then path, line."""
    tests = cmap["tests"]
    v = env.visible_test
    visible_pass = set(tests) - set(env.bug["failing_tests"]) - env.ignore
    scored = []
    for rel, lines in cmap["files"].items():
        for ln, ids in lines.items():
            names = {tests[i] for i in ids}
            if v not in names:
                continue
            ep = len(names & visible_pass)
            scored.append((1.0 / math.sqrt(1 + ep), rel, int(ln)))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    return scored


def candidate_edits(env: BugEnv, mode, cmap, cap):
    out = []
    if mode == "oracle-line":
        tree = ast.parse(env.buggy)
        fn = _func_at(tree, env.bug["edit"]["lineno"])
        for ed in edits_in_function(env.buggy, fn, include_not_insert=True, lines={env.bug["edit"]["lineno"]}):
            out.append((env.bug["file"], ed, (fn.lineno, fn.end_lineno)))
        return out
    trees = {}
    for score, rel, ln in ochiai_lines(env, cmap):
        if len(out) >= cap:
            break
        if rel not in trees:
            src = env.buggy if rel == env.bug["file"] else (repo_dir(env.repo) / rel).read_bytes()
            try:
                trees[rel] = (src, ast.parse(src))
            except SyntaxError:
                continue
        src, tree = trees[rel]
        fn = _func_at(tree, ln)
        if fn is None:
            continue
        for ed in edits_in_function(src, fn, include_not_insert=True, lines={ln}):
            out.append((rel, ed, (fn.lineno, fn.end_lineno)))
    return out[:cap]


def repair(bug, mode, cap, cmap, staged=False):
    env = BugEnv(bug, cmap)
    cands = candidate_edits(env, mode, cmap, cap)
    all_ids = set(collect_ids(env.repo)) - env.ignore
    tried = []
    t0 = time.time()
    for rel, ed, (f0, f1) in cands:
        p = repo_dir(env.repo) / rel
        base = env.buggy if rel == bug["file"] else p.read_bytes()
        patched = ed.apply(base)
        files = {env.path: env.buggy}
        files[p] = patched
        with env.state(files):
            ok, r = env.run_visible_test()
            rec = {"path": rel, "edit": ed.to_dict(), "min_wall": round(r["wall"], 3), "passes_min": ok}
            if ok and staged:
                # Cheaper but equivalent: run the tfile and cov sets first. If one of
                # them fails, the full and correct verdicts are already False.
                sets = env.policy_sets(all_ids, rel, range(f0, f1 + 1))
                sub = sorted((sets["tfile"] | sets["cov"]) - {env.visible_test})
                r2 = run_pytest(env.repo, selectors=sub, timeout=180) if sub else {"results": {}, "timed_out": False, "wall": 0}
                fails2 = failed_tests(r2["results"], env.ignore) | ({"<timeout>"} if r2["timed_out"] else set())
                if fails2:
                    res2 = r2["results"]
                    rec.update({"min": True, "tfile": not (fails2 & sets["tfile"]) and not r2["timed_out"],
                                "cov": not (fails2 & sets["cov"]) and not r2["timed_out"], "full": False, "correct": False,
                                "cost": {p: round(sum(res2[t]["duration"] for t in sets[p] if t in res2), 4)
                                         for p in ("tfile", "cov")},
                                "n_tests": {p: len(sets[p]) for p in sets}, "fails": sorted(fails2)[:50],
                                "n_fails": len(fails2), "full_skipped": True, "subset_wall": round(r2["wall"], 3),
                                "exact": False})
                    ok = False  # skip the full run below
            if ok:
                full = env.run_full()
                rec["full_wall"] = round(full["wall"], 3)
                rec.update(env.verdicts(full, rel, range(f0, f1 + 1)))
                rec["exact"] = rel == bug["file"] and env.is_exact(patched)
        tried.append(rec)
        if rec.get("full"):
            break
    return {"id": bug["id"], "repo": bug["repo"], "mode": mode, "n_candidates": len(cands),
            "tried": tried, "seconds": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["oracle-line", "sbfl"], required=True)
    ap.add_argument("--cap", type=int, default=40)
    ap.add_argument("--repo")
    ap.add_argument("--staged", action="store_true", help="run the tfile+cov tests before the full suite")
    a = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"search_{a.mode}.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    cmaps = {}
    for bug in load_bugs(a.repo):
        if bug["id"] in done:
            continue
        cmap = cmaps.setdefault(bug["repo"], coverage_map.load(bug["repo"]))
        res = repair(bug, a.mode, a.cap, cmap, a.staged)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(res) + "\n")
        acc = next((t for t in res["tried"] if t.get("min")), None)
        print(f"{bug['id']} {a.mode}: {len(res['tried'])}/{res['n_candidates']} tried, "
              f"first-min={'none' if not acc else ('exact' if acc['exact'] else ('correct' if acc['correct'] else 'WRONG'))} "
              f"{res['seconds']}s", flush=True)


if __name__ == "__main__":
    main()
