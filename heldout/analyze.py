"""Turn results/*.jsonl into the tables used in the writeup (results/summary.md)."""
from __future__ import annotations

import json
import statistics as st
from collections import Counter, defaultdict

from .config import DATA, RESULTS
from .evaluate import POLICIES, load_bugs
from .repair_llm import _BLOCK


def _norm(rec):
    """Early records named the test-file policy "file" (colliding with the patch path)."""
    if isinstance(rec.get("file"), bool):
        rec["tfile"] = rec.pop("file")
    for k in ("cost", "n_tests"):
        if isinstance(rec.get(k), dict) and "file" in rec[k]:
            rec[k]["tfile"] = rec[k].pop("file")
    return rec


def jl(path):
    if not path.exists():
        return []
    rows, seen = [], set()
    for l in path.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        if "id" in r:
            if r["id"] in seen:     # two workers raced on the same bug: keep the first record
                continue
            seen.add(r["id"])
        rows.append(r)
    for r in rows:
        for t in r.get("tried", []) + r.get("attempts", []):
            _norm(t)
    return rows


def pol(rec, p):
    """Policy verdict of a record. Early records stored the test-file policy as "file",
    which collided with the patch path key; a bool there is the verdict."""
    if p == "tfile":
        v = rec.get("tfile", rec.get("file"))
        return v if isinstance(v, bool) else None
    return rec.get(p)


def outcome(rec):
    if rec is None:
        return "none"
    if rec.get("exact"):
        return "exact"
    return "correct" if rec.get("correct") else "wrong"


def seeding_table():
    rows = ["| repo | tests | baseline-failing (excluded) | candidates | killed | survived | hang/other | kept bugs | median failing tests per bug | bugs with exactly 1 failing test |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for repo in ("rich", "click"):
        log = jl(DATA / f"seedlog_{repo}.jsonl")
        if not log:
            continue
        base = json.loads((DATA / f"baseline_{repo}.json").read_text(encoding="utf-8"))
        c = Counter(x["status"] for x in log)
        bugs = load_bugs(repo)
        nf = [len(b["failing_tests"]) for b in bugs]
        rows.append(f"| {repo} | {base['n_tests']} | {len(base['baseline_failures'])} | {len(log)} | {c['killed']} | "
                    f"{c['survived']} | {c['hang'] + c['collection_error']} | {len(bugs)} | {st.median(nf)} | "
                    f"{sum(1 for n in nf if n == 1)} |")
    ops = Counter(b["edit"]["op"] for b in load_bugs())
    rows.append("")
    rows.append("Operators in kept bugs: " + ", ".join(f"{k} {v}" for k, v in ops.most_common()))
    return "\n".join(rows)


def search_rows(results):
    """For each policy: outcome counts and estimated wall seconds until acceptance."""
    per = {p: Counter() for p in POLICIES}
    secs = {p: [] for p in POLICIES}
    # with --staged, a candidate that fails the tfile/cov sets never gets a full run;
    # the full rule would still have paid for one, so charge the repo's median full run
    walls = defaultdict(list)
    for r in results:
        for t in r["tried"]:
            if "full_wall" in t:
                walls[r["repo"]].append(t["full_wall"])
    med_full = {k: st.median(v) for k, v in walls.items()}
    for r in results:
        tried = r["tried"]
        startup = st.median([t["min_wall"] for t in tried]) if tried else 0
        for p in POLICIES:
            acc, cost = None, 0.0
            for t in tried:
                cost += t["min_wall"]
                if t.get("passes_min") and ("full_wall" in t or t.get("full_skipped")):
                    if p == "full":
                        cost += t.get("full_wall", med_full.get(r["repo"], 0.0))
                    elif p in ("tfile", "cov"):
                        cost += startup + t["cost"][p]
                if pol(t, p):
                    acc = t
                    break
            per[p][outcome(acc)] += 1
            secs[p].append(cost)
    return per, secs


def llm_rows(results):
    per = {p: Counter() for p in POLICIES}
    turns = {p: [] for p in POLICIES}
    for r in results:
        for p in POLICIES:
            acc = None
            for a in r["attempts"]:
                if pol(a, p):
                    acc = a
                    break
            per[p][outcome(acc)] += 1
            if acc is not None:
                turns[p].append(acc["turn"] + 1)
    return per, turns


def fmt_policy_table(per, n, extra=None, extra_name=""):
    head = "| policy | accepted | exact fix | other correct | wrong (passes policy, fails hidden tests) | wrong / accepted |" + (f" {extra_name} |" if extra else "")
    rows = [head, "|---|---|---|---|---|---|" + ("---|" if extra else "")]
    for p in POLICIES:
        c = per[p]
        acc = c["exact"] + c["correct"] + c["wrong"]
        share = f"{100 * c['wrong'] / acc:.1f}%" if acc else "-"
        row = f"| {p} | {acc}/{n} | {c['exact']} | {c['correct']} | {c['wrong']} | {share} |"
        if extra:
            row += f" {extra[p]} |"
        rows.append(row)
    return "\n".join(rows)


def by_failing_count(results, kind):
    """Wrong-accept rate under `min`, split by how many tests the bug breaks."""
    bugs = {b["id"]: b for b in load_bugs()}
    groups = defaultdict(Counter)
    for r in results:
        n = len(bugs[r["id"]]["failing_tests"])
        g = "1" if n == 1 else ("2-5" if n <= 5 else ">5")
        items = r["tried"] if kind == "search" else r["attempts"]
        acc = next((t for t in items if pol(t, "min")), None)
        groups[g][outcome(acc)] += 1
    rows = ["| failing tests in bug | bugs | accepted under min | wrong |", "|---|---|---|---|"]
    for g in ("1", "2-5", ">5"):
        c = groups[g]
        acc = c["exact"] + c["correct"] + c["wrong"]
        rows.append(f"| {g} | {sum(c.values())} | {acc} | {c['wrong']} |")
    return "\n".join(rows)


def noop_attempts(results):
    """Attempts whose patched file is AST-identical to the buggy file (the model handed
    the bug back, modulo formatting)."""
    from .mutate import Edit, same_ast
    from .repair_llm import extract, func_source, splice
    from .review import clean
    bugs = {b["id"]: b for b in load_bugs()}
    n = total = 0
    for r in results:
        b = bugs[r["id"]]
        buggy = Edit(**b["edit"]).apply(clean(b["repo"], b["file"]))
        _, indent = func_source(buggy, b["func_start"], b["func_end"])
        for a in r["attempts"]:
            new_func, err = extract(a.get("reply") or "", b["function"])
            if err:
                continue
            total += 1
            n += 1 if same_ast(splice(buggy, b["func_start"], b["func_end"], new_func, indent), buggy) else 0
    return n, total


def llm_stats(results):
    toks_in, toks_out, secs, applied_err, unchanged = [], [], [], 0, 0
    attempts, repeats, later = 0, 0, 0
    for r in results:
        secs.append(r["seconds"])
        prev = None
        for a in r["attempts"]:
            m = _BLOCK.search(a["reply"] or "")
            code = m.group(1).strip() if m else None
            if a["turn"] > 0:
                later += 1
                repeats += 1 if code is not None and code == prev else 0
            prev = code
            attempts += 1
            u = (a["llm"] or {}).get("usage") or {}
            toks_in.append(u.get("prompt_tokens", 0))
            toks_out.append(u.get("completion_tokens", 0))
            applied_err += 1 if a.get("apply_error") else 0
            unchanged += 1 if a.get("unchanged") else 0
    if not results:
        return ""
    return (f"bugs {len(results)}, attempts {attempts}, replies that could not be applied {applied_err}, "
            f"median prompt tokens {st.median(toks_in):.0f}, "
            f"median completion tokens {st.median(toks_out):.0f}, median seconds per bug {st.median(secs):.0f}, "
            f"total hours {sum(secs) / 3600:.2f}, applied replies AST-identical to the buggy code {'/'.join(map(str, noop_attempts(results)))}, retry turns that repeated the previous code block verbatim {repeats}/{later}")


def main():
    out = ["# Results summary (generated by `python -m heldout.analyze`)", "", "## Seeded bugs", "", seeding_table(), ""]
    for mode in ("oracle-line", "sbfl"):
        res = jl(RESULTS / f"search_{mode}.jsonl")
        if not res:
            continue
        per, secs = search_rows(res)
        med = {p: f"{st.median(secs[p]):.1f} / {sum(secs[p]) / 60:.1f}" for p in POLICIES}
        out += [f"## Search baseline: {mode} ({len(res)} bugs)", "",
                fmt_policy_table(per, len(res), med, "median s per bug / total min"), "",
                "Wrong-accept under `min` by number of failing tests:", "", by_failing_count(res, "search"), ""]
    ranks = sbfl_ranks()
    rk = [x["rank"] for x in ranks if x["rank"]]
    out += ["## Ochiai rank of the seeded line", "",
            f"bugs {len(ranks)}, seeded line not covered by the visible test {sum(1 for x in ranks if not x['rank'])}, "
            + ", ".join(f"top-{k} {sum(1 for x in rk if x <= k)}" for k in (1, 5, 10, 20))
            + f", median rank {st.median(rk)}, median lines covered by the visible test "
            f"{st.median([x['covered_lines'] for x in ranks])}", ""]
    for path in sorted(RESULTS.glob("llm_*.jsonl")):
        res = jl(path)
        per, turns = llm_rows(res)
        mt = {p: (f"{st.mean(turns[p]):.2f}" if turns[p] else "-") for p in POLICIES}
        out += [f"## Gemma 4: {path.stem} ({len(res)} bugs)", "", fmt_policy_table(per, len(res), mt, "mean turns to accept"),
                "", llm_stats(res), "", "Wrong-accept under `min` by number of failing tests:", "",
                by_failing_count(res, "llm"), ""]
    out += ["## Manual review of patches that pass every test but differ from the original", "",
            manual_review_table(), ""]
    text = "\n".join(out)
    (RESULTS / "summary.md").write_text(text, encoding="utf-8")
    print(text)



def sbfl_ranks():
    """Rank of the seeded line in the Ochiai ordering (1 = most suspicious), with the
    number of lines tied at the same score."""
    from . import coverage_map
    from .evaluate import BugEnv
    from .repair_search import ochiai_lines
    out = []
    cmaps = {}
    for b in load_bugs():
        cmap = cmaps.setdefault(b["repo"], coverage_map.load(b["repo"]))
        env = BugEnv.__new__(BugEnv)
        env.bug, env.repo, env.visible_test = b, b["repo"], b["visible_test"]
        from .evaluate import baseline_failures
        env.ignore = baseline_failures(b["repo"])
        ranked = ochiai_lines(env, cmap)
        key = (b["file"], b["edit"]["lineno"])
        pos = next((i + 1 for i, (_, f, l) in enumerate(ranked) if (f, l) == key), None)
        ties = sum(1 for s, _, _ in ranked if pos and s == ranked[pos - 1][0])
        out.append({"id": b["id"], "rank": pos, "ties": ties, "covered_lines": len(ranked)})
    return out



def manual_review_table():
    """Suite-adequate, non-identical patches (any policy) and the hand verdicts."""
    from .review import candidates
    path = RESULTS / "manual_review.json"
    verdicts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    per = defaultdict(Counter)
    for key, _ in candidates():
        src = key.split(":")[0]
        per[src][verdicts.get(key, {}).get("verdict", "unreviewed")] += 1
    rows = ["| source | patches | equivalent | not equivalent | unreviewed |", "|---|---|---|---|---|"]
    for src, c in per.items():
        rows.append(f"| {src} | {sum(c.values())} | {c['equivalent']} | {c['not_equivalent']} | {c['unreviewed']} |")
    return "\n".join(rows)


if __name__ == "__main__":
    main()
