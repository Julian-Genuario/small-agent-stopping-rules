"""Replace machine-specific absolute paths in recorded pytest output with placeholders.

usage: python scripts/scrub_paths.py
"""
import pathlib
import re

SEP = r"(?:\\\\|\\|/)+"   # one or more separators: escaped or plain backslashes, or slashes
PATTERNS = [
    (re.compile(r"[A-Za-z]:" + SEP + "Users" + SEP + r"[^\\/\"']+" + SEP + "hackathons" + SEP + "kaggle-gemma4-paper"), "<repo>"),
    (re.compile(r"[A-Za-z]:" + SEP + "Users" + SEP + r"[^\\/\"']+" + SEP + "AppData" + SEP + "Local" + SEP + "Programs"
                + SEP + "Python" + SEP + r"Python3\d+"), "<python>"),
    (re.compile(r"[A-Za-z]:" + SEP + "Users" + SEP + r"[^\\/\"']+" + SEP + "AppData" + SEP + "Local" + SEP + "Temp"), "<tmp>"),
]

root = pathlib.Path(__file__).resolve().parent.parent
for p in sorted(list((root / "results").glob("*.jsonl")) + list((root / "data").glob("*.json*"))):
    text = p.read_text(encoding="utf-8")
    new = text
    for rx, rep in PATTERNS:
        new = rx.sub(rep, new)
    if new != text:
        p.write_text(new, encoding="utf-8")
        print("scrubbed", p.name)
