"""Pinned upstream projects used to seed bugs.

Both projects are cloned into ./work by scripts/setup.sh. The commits are pinned so
that the seeded bugs (data/bugs.jsonl) apply cleanly.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# A second checkout (HELDOUT_WORK=work2) lets two experiments run side by side.
WORK = ROOT / os.environ.get("HELDOUT_WORK", "work")
DATA = ROOT / "data"
RESULTS = ROOT / "results"

REPOS = {
    "rich": {
        "url": "https://github.com/Textualize/rich.git",
        "tag": "v14.3.4",
        "commit": "ee8378c3bbbd7c75abc2f55c6c19e83b218ae81d",
        "package": "rich",          # directory with the library source
        "tests": "tests",
        "license": "MIT",
    },
    "click": {
        "url": "https://github.com/pallets/click.git",
        "tag": "8.5.0",
        "commit": "8b19813f2bfca99f1018a587a8cf54fc959f2e5d",
        "package": "src/click",
        "tests": "tests",
        "license": "BSD-3-Clause",
    },
}

# Only functions up to this many lines are mutated. Keeps prompts for a 2B model
# small enough to run on a CPU, and is applied to every condition alike.
MAX_FUNC_LINES = 60

# A full-suite run that takes longer than this is treated as a hang.
SUITE_TIMEOUT_S = 180


def repo_dir(name: str) -> Path:
    return WORK / name
