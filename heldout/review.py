"""Dump every accepted patch that passes the whole suite but is not AST-identical to the
original code, so a person can judge whether it is semantically equivalent.

Writes results/review_candidates.md. Verdicts are in results/manual_review.json as
{"<source>:<bug id>#<index>": {"verdict": "equivalent" | "not_equivalent", "note": "..."}}.
"""
from __future__ import annotations

import difflib
import subprocess

from .analyze import jl, pol
from .config import RESULTS, repo_dir
from .evaluate import POLICIES, load_bugs
from .mutate import Edit
from .repair_llm import extract, func_source, splice


def clean(repo, rel) -> bytes:
    """Read the pinned file from git, not the working tree (an experiment may be running).

    Offsets in the data were taken on the working tree, so the working tree's line
    endings are restored (git on Windows checks out CRLF by default)."""
    data = subprocess.run(["git", "-C", str(repo_dir(repo)), "show", f"HEAD:{rel}"], capture_output=True,
                          check=True).stdout
    crlf = b"\r\n" in (repo_dir(repo) / rel).read_bytes()[:4000]
    if crlf and b"\r\n" not in data:
        data = data.replace(b"\n", b"\r\n")
    return data


def diff(path, a: bytes, b: bytes):
    a, b = a.replace(b"\r\n", b"\n"), b.replace(b"\r\n", b"\n")
    return "".join(difflib.unified_diff(a.decode().splitlines(True), b.decode().splitlines(True),
                                        f"original/{path}", f"patched/{path}", n=1))


def path_of(t, b):
    v = t.get("path", t.get("file"))
    return v if isinstance(v, str) else b["file"]


def picks(items):
    """Index of the item each policy would accept; each distinct index once."""
    return sorted({next((i for i, t in enumerate(items) if pol(t, p)), None) for p in POLICIES} - {None})


def needs_review(rec):
    return rec.get("correct") and not rec.get("exact")


def search_patch(b, t):
    rel = path_of(t, b)
    orig = clean(b["repo"], rel)
    if rel == b["file"]:
        return diff(rel, orig, Edit(**t["edit"]).apply(Edit(**b["edit"]).apply(orig)))
    bug_file = clean(b["repo"], b["file"])
    return (diff(b["file"], bug_file, Edit(**b["edit"]).apply(bug_file))
            + "\n(bug left in place; patch elsewhere)\n" + diff(rel, orig, Edit(**t["edit"]).apply(orig)))


def llm_patch(b, a):
    orig = clean(b["repo"], b["file"])
    buggy = Edit(**b["edit"]).apply(orig)
    _, indent = func_source(buggy, b["func_start"], b["func_end"])
    new_func, _ = extract(a["reply"], b["function"])
    return diff(b["file"], orig, splice(buggy, b["func_start"], b["func_end"], new_func, indent))


def candidates():
    bugs = {b["id"]: b for b in load_bugs()}
    for mode in ("oracle-line", "sbfl"):
        for r in jl(RESULTS / f"search_{mode}.jsonl"):
            for i in picks(r["tried"]):
                if needs_review(r["tried"][i]):
                    yield f"search-{mode}:{r['id']}#{i}", search_patch(bugs[r["id"]], r["tried"][i])
    for path in sorted(RESULTS.glob("llm_*.jsonl")):
        for r in jl(path):
            for i in picks(r["attempts"]):
                a = r["attempts"][i]
                # turn-0 answers reused or copied from the greedy run are reviewed there
                if needs_review(a) and not a.get("reused_from") and not r.get("copied_from"):
                    yield f"{path.stem}:{r['id']}#{i}", llm_patch(bugs[r["id"]], a)


def main():
    out = ["# Patches that pass every test but differ from the original code", ""]
    n = 0
    for key, d in candidates():
        out += [f"## {key}", "```diff", d, "```", ""]
        n += 1
    (RESULTS / "review_candidates.md").write_text("\n".join(out), encoding="utf-8")
    print(f"{n} patches to review")


if __name__ == "__main__":
    main()
