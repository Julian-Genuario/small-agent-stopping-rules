"""Tiny pytest plugin: writes {nodeid: {"outcome", "duration"}} to $HELDOUT_RESULT.

Loaded with `-p heldout.pytest_record`. A test counts as failed if any phase
(setup, call, teardown) failed.
"""
import json
import os

_results = {}


def pytest_runtest_logreport(report):
    rec = _results.setdefault(report.nodeid, {"outcome": "passed", "duration": 0.0})
    rec["duration"] += float(getattr(report, "duration", 0.0) or 0.0)
    if report.failed:
        rec["outcome"] = "failed"
    elif report.skipped and report.when in ("setup", "call") and rec["outcome"] != "failed":
        rec["outcome"] = "skipped"


def pytest_collectreport(report):
    if report.failed:
        _results[report.nodeid or "<collection>"] = {"outcome": "failed", "duration": 0.0}


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("HELDOUT_RESULT")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_results, fh)
