"""Shared evaluation: apply a candidate patch, run tests, derive every validation policy.

Validation policies (what a repair loop runs before it accepts a patch):
  min   - only the visible failing test (the "reproduction")
  tfile - every visible test in the same test file as the reproduction
  cov   - the reproduction plus every visible test that covers the patched
          function in the clean checkout
  full  - the whole visible suite
Ground truth ("correct") is the whole suite, including the failing tests that were
held out of the visible suite.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager

from . import coverage_map
from .config import DATA, repo_dir
from .mutate import Edit, same_ast
from .runner import clear_pycache, failed_tests, run_pytest

POLICIES = ("min", "tfile", "cov", "full")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def load_bugs(repo=None):
    bugs = []
    for p in sorted(DATA.glob("bugs_*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                b = json.loads(line)
                if repo is None or b["repo"] == repo:
                    bugs.append(b)
    return bugs


def baseline_failures(repo):
    return set(json.loads((DATA / f"baseline_{repo}.json").read_text(encoding="utf-8"))["baseline_failures"])


class BugEnv:
    """Holds the clean file, the buggy file and the test partitions for one bug."""

    def __init__(self, bug, cmap=None):
        self.bug = bug
        self.repo = bug["repo"]
        self.path = repo_dir(self.repo) / bug["file"]
        self.orig = self.path.read_bytes()
        self.buggy = Edit(**bug["edit"]).apply(self.orig)
        self.ignore = baseline_failures(self.repo)
        self.cmap = cmap or coverage_map.load(self.repo)
        self.visible_test = bug["visible_test"]
        self.hidden = set(bug["failing_tests"]) - {self.visible_test}

    @contextmanager
    def state(self, files: dict):
        """Temporarily write {path: bytes}; always restores the clean checkout."""
        saved = {p: p.read_bytes() for p in files}
        try:
            for p, data in files.items():
                p.write_bytes(data)
            clear_pycache(self.repo)
            yield
        finally:
            for p, data in saved.items():
                p.write_bytes(data)
            # restore the clean file of the bug too, in case it was not in `files`
            if self.path.read_bytes() != self.orig:
                self.path.write_bytes(self.orig)

    def run_visible_test(self, capture=False):
        r = run_pytest(self.repo, selectors=[self.visible_test], capture_output=capture, timeout=120)
        rec = r["results"].get(self.visible_test)
        ok = (not r["timed_out"]) and rec is not None and rec["outcome"] == "passed"
        return ok, r

    def run_test_output(self, nodeid):
        r = run_pytest(self.repo, selectors=[nodeid], capture_output=True, timeout=120)
        return _ANSI.sub("", r["output"] or "")

    def run_full(self):
        return run_pytest(self.repo, deselect=sorted(self.ignore))

    def policy_sets(self, all_tests: set[str], patched_file_rel: str, func_lines: range):
        visible = all_tests - self.hidden
        vfile = self.visible_test.split("::")[0]
        cov = coverage_map.tests_covering(self.cmap, patched_file_rel, func_lines) & visible
        return {
            "min": {self.visible_test},
            "tfile": {t for t in visible if t.split("::")[0] == vfile} | {self.visible_test},
            "cov": cov | {self.visible_test},
            "full": visible,
        }

    def verdicts(self, full_run, patched_file_rel, func_lines):
        res = full_run["results"]
        tests = set(res) - self.ignore
        fails = failed_tests(res, self.ignore)
        if full_run["timed_out"]:
            fails = fails | {"<timeout>"}
        sets = self.policy_sets(tests, patched_file_rel, func_lines)
        out = {p: not (fails & sets[p]) and not full_run["timed_out"] for p in POLICIES}
        out["correct"] = not fails
        # estimated cost of each policy in test-seconds (sum of per-test durations)
        out["cost"] = {p: round(sum(res[t]["duration"] for t in sets[p] if t in res), 4) for p in POLICIES}
        out["n_tests"] = {p: len(sets[p]) for p in POLICIES}
        out["fails"] = sorted(fails)[:50]
        out["n_fails"] = len(fails)
        return out

    def is_exact(self, patched_bytes):
        return same_ast(patched_bytes, self.orig)
