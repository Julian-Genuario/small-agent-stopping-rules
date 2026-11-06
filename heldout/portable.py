"""Add line-relative columns to every recorded edit.

The experiments ran on a Windows checkout (CRLF line endings), so the byte offsets in
data/ and results/ refer to CRLF files. `col` (byte column inside the line) lets
Edit.apply relocate an edit on an LF checkout. Run once, on a clean checkout.
"""
import json

from .config import DATA, RESULTS
from .review import clean, path_of


def _starts(src: bytes):
    s = [0]
    s += [i + 1 for i, b in enumerate(src) if b == 10]
    return s


def add_col(edit, src):
    starts = _starts(src)
    edit["col"] = edit["start"] - starts[edit["lineno"] - 1]
    assert src[edit["start"]: edit["end"]].decode() == edit["old"], edit
    return edit


def main():
    cache = {}

    def src(repo, rel):
        return cache.setdefault((repo, rel), clean(repo, rel))

    for p in sorted(DATA.glob("bugs_*.jsonl")):
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        for b in rows:
            add_col(b["edit"], src(b["repo"], b["file"]))
        p.write_text("".join(json.dumps(b) + "\n" for b in rows), encoding="utf-8")
        print(p.name, len(rows))
    from .mutate import Edit
    bugs = {}
    for p in sorted(DATA.glob("bugs_*.jsonl")):
        for l in p.read_text(encoding="utf-8").splitlines():
            b = json.loads(l)
            bugs[b["id"]] = b
    for p in sorted(RESULTS.glob("search_*.jsonl")):
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        for r in rows:
            b = bugs[r["id"]]
            for t in r["tried"]:
                rel = path_of(t, b)
                base = src(b["repo"], rel)
                if rel == b["file"]:
                    base = Edit(**b["edit"]).apply(base)
                add_col(t["edit"], base)
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        print(p.name, len(rows))


if __name__ == "__main__":
    main()
