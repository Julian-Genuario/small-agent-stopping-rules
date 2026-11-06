"""Gemma 4 repair loop through a local llama.cpp server (OpenAI-compatible API).

The model gets the buggy function (oracle function-level localization), the
visible failing test and its pytest output. It answers with a full replacement
function. Up to --turns attempts; after each failed attempt it sees the output of
the first failing visible test. The loop stops when the whole visible suite passes.

usage: python -m heldout.repair_llm --tag e2b-nothink --url http://127.0.0.1:8089
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import textwrap
import time
import urllib.error
import urllib.request

from . import coverage_map
from .config import RESULTS, repo_dir
from .evaluate import _ANSI, BugEnv, load_bugs

SYSTEM = (
    "You fix bugs in open-source Python libraries. You are given one function that contains a bug, "
    "a test that fails because of it, and the pytest output. Reply with the complete corrected function "
    "in a single ```python code block. Keep the name and signature. Change as little as possible. "
    "No explanation."
)
_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


def chat(url, messages, max_tokens, think, temperature=0.0, seed=None):
    body = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": bool(think)}}
    if seed is not None:
        body["seed"] = seed
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=3600))
    msg = r["choices"][0]["message"]
    return msg.get("content") or "", {"usage": r.get("usage"), "seconds": round(time.time() - t0, 1),
                                      "finish": r["choices"][0].get("finish_reason")}


def test_source(env: BugEnv, nodeid: str, limit=60):
    path, *rest = nodeid.split("::")
    root = repo_dir(env.repo)
    p = root / path
    if not p.exists():
        p = root / "tests" / path
    name = re.sub(r"\[.*\]$", "", rest[-1])
    src = p.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = src.splitlines()[node.lineno - 1 - len(node.decorator_list): node.end_lineno]
            return _cap("\n".join(lines[:limit]), MAX_TEST_CHARS, keep_end=False)
    return "(test source not found)"


MAX_OUTPUT_CHARS = 4000   # rich renders very wide lines; a line cap alone let one prompt reach 40k chars
MAX_TEST_CHARS = 3000


def _cap(text: str, n: int, keep_end: bool) -> str:
    if len(text) <= n:
        return text
    return ("[...]\n" + text[-n:]) if keep_end else (text[:n] + "\n[...]")


def trim_output(out: str, limit=60):
    lines = [l if len(l) <= 400 else l[:400] + " [...]" for l in out.splitlines() if l.strip()]
    return _cap("\n".join(lines[-limit:]), MAX_OUTPUT_CHARS, keep_end=True)


def func_source(src: bytes, start: int, end: int):
    lines = src.decode("utf-8").splitlines(True)
    chunk = "".join(lines[start - 1: end])
    indent = re.match(r"[ \t]*", lines[start - 1]).group(0)
    return textwrap.dedent(chunk), indent


def splice(src: bytes, start: int, end: int, new_func: str, indent: str) -> bytes:
    lines = src.decode("utf-8").splitlines(True)
    body = textwrap.indent(new_func.rstrip("\n") + "\n", indent, lambda l: l.strip() != "")
    return ("".join(lines[: start - 1]) + body + "".join(lines[end:])).encode("utf-8")


def extract(reply: str, name: str):
    m = _BLOCK.search(reply)
    if not m:
        return None, "no ```python code block found"
    code = textwrap.dedent(m.group(1))
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return None, f"SyntaxError: {e}"
    defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    target = next((d for d in defs if d.name == name), None)
    if target is None:
        return None, f"no function named {name} in the reply"
    lines = code.splitlines()
    # drop decorators the model may have repeated: the original ones stay in place
    return "\n".join(lines[target.lineno - 1: target.end_lineno]) + "\n", None


def repair(bug, args, cmap, prefix=None):
    env = BugEnv(bug, cmap)
    f0, f1 = bug["func_start"], bug["func_end"]
    fsrc, indent = func_source(env.buggy, f0, f1)
    with env.state({env.path: env.buggy}):
        out = env.run_test_output(env.visible_test)
    user = (f"File: {bug['file']}\n\nFunction with the bug:\n```python\n{fsrc}```\n\n"
            f"Failing test `{env.visible_test}`:\n```python\n{test_source(env, env.visible_test)}\n```\n\n"
            f"pytest output:\n```\n{trim_output(out)}\n```")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    attempts = []
    first_turn = 0
    if prefix is not None:
        # reuse turn 0 of an earlier greedy run: same prompt, same reply, same feedback
        # (the pytest summary line carries a timing, so the prompt is taken from the old run as-is)
        user = prefix["messages"][1]["content"]
        attempts = [dict(prefix["attempts"][0], reused_from=prefix["tag"])]
        messages = prefix["messages"][:4]
        first_turn = 1
    t0 = time.time()
    for turn in range(first_turn, args.turns):
        try:
            # turn 0 is always greedy; retries may sample (--retry-temperature)
            temp = 0.0 if turn == 0 else args.retry_temperature
            seed = None if turn == 0 else 1000 * turn + int(bug["id"].split("-")[1])
            reply, meta = chat(args.url, messages, args.max_tokens, args.think, temp, seed)
            meta["temperature"] = temp
        except urllib.error.HTTPError as e:   # e.g. the conversation outgrew the context window
            attempts.append({"turn": turn, "llm": None, "reply": "", "llm_error": f"HTTP {e.code}: {e.read()[:300]!r}"})
            break
        rec = {"turn": turn, "llm": meta, "reply": reply}
        new_func, err = extract(reply, bug["function"])
        if err:
            rec["apply_error"] = err
            feedback = f"Your reply could not be applied ({err}). Reply with the complete function in one ```python block."
        else:
            patched = env.buggy[:0] + splice(env.buggy, f0, f1, new_func, indent)
            try:
                ast.parse(patched)
                parse_ok = True
            except SyntaxError as e:
                parse_ok = False
                rec["apply_error"] = f"patched file does not parse: {e}"
                feedback = f"After inserting your function the file does not parse ({e}). Reply again."
            if parse_ok:
                rec["unchanged"] = patched == env.buggy
                with env.state({env.path: patched}):
                    ok, r = env.run_visible_test(capture=True)
                    rec["passes_min"] = ok
                    if not ok:
                        feedback = ("The test still fails after your change:\n```\n"
                                    f"{trim_output(_ANSI.sub('', r['output'] or ''))}\n```\nFix the function again.")
                    else:
                        full = env.run_full()
                        rec["full_wall"] = round(full["wall"], 3)
                        rec.update(env.verdicts(full, bug["file"], range(f0, f1 + 1)))
                        rec["exact"] = env.is_exact(patched)
                        rec["diff_lines"] = sum(1 for a, b in zip(env.buggy.splitlines(), patched.splitlines()) if a != b) \
                            + abs(len(env.buggy.splitlines()) - len(patched.splitlines()))
                        visible_fails = sorted(set(rec["fails"]) - env.hidden - {"<timeout>"})
                        if not rec["full"] and not visible_fails:
                            feedback = "The test suite timed out after your change (possible infinite loop). Fix the function again."
                        elif not rec["full"]:
                            first = visible_fails[0]
                            o = env.run_test_output(first)
                            feedback = (f"The original failing test now passes, but this other test fails:\n"
                                        f"`{first}`\n```python\n{test_source(env, first)}\n```\n```\n{trim_output(o)}\n```\n"
                                        "Fix the function so both pass.")
        attempts.append(rec)
        if rec.get("full"):
            break
        messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": feedback}]
    return {"id": bug["id"], "repo": bug["repo"], "tag": args.tag, "attempts": attempts,
            "prompt_chars": len(user), "messages": messages, "seconds": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8089")
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--retry-temperature", type=float, default=0.0)
    ap.add_argument("--copy-solved-from", help="tag whose turn-0 accepted (full) records are copied, not re-run")
    ap.add_argument("--repo")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--reverse", action="store_true", help="walk the bugs from the end (for a second worker)")
    a = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"llm_{a.tag}.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    bugs = [b for b in load_bugs(a.repo) if b["id"] not in done][: a.limit]
    cmaps = {}
    solved, base = {}, {}
    if a.copy_solved_from:
        for l in (RESULTS / f"llm_{a.copy_solved_from}.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            base[r["id"]] = r
            if r["attempts"] and r["attempts"][0].get("full"):
                solved[r["id"]] = r
    if a.reverse:
        bugs = bugs[::-1]

    def finished():
        if not out.exists():
            return set()
        return {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}

    for bug in bugs:
        if bug["id"] in finished():   # another worker may have taken it
            continue
        if bug["id"] in solved:
            res = dict(solved[bug["id"]], tag=a.tag, copied_from=a.copy_solved_from)
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(res) + "\n")
            continue
        cmap = cmaps.setdefault(bug["repo"], coverage_map.load(bug["repo"]))
        prev = base.get(bug["id"])
        prefix = prev if prev and len(prev.get("messages", [])) >= 4 else None
        res = repair(bug, a, cmap, prefix)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(res) + "\n")
        last = res["attempts"][-1]
        print(f"{bug['id']} {a.tag}: turns={len(res['attempts'])} min={last.get('passes_min')} "
              f"full={last.get('full')} correct={last.get('correct')} exact={last.get('exact')} {res['seconds']}s", flush=True)


if __name__ == "__main__":
    main()
