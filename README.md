# heldout: how much testing should a small repair agent do before it stops?

Code and data for the Kaggle writeup *When should a small repair agent stop testing?*
(Gemma 4 Developer Agent, Paper Track) by Julián Genuario. The writeup itself is `writeup.md`.

The repository seeds one-token bugs into two real Python projects (rich 14.3.4 and click 8.5.0),
shows the repairer one failing test per bug, holds the other failing tests out, and measures what
four stopping rules let through:

| rule | validation set |
|---|---|
| `min` | the visible failing test |
| `tfile` | every visible test in the same test file |
| `cov` | the visible failing test plus every visible test that executes the patched function |
| `full` | the whole visible suite |

Ground truth is the complete suite, held-out failing tests included, plus a manual review of
patches that pass everything without restoring the original code (`results/manual_review.json`).

Repairers: a deterministic single-token search (oracle line, or Ochiai/SBFL-ranked lines) and
Gemma 4 E2B (Google's QAT Q4_0 GGUF) served by llama.cpp on a 4-core CPU, up to 3 turns with test feedback.

All results were produced on one machine: Intel i7-6700T (4 cores), 8 GB RAM, no GPU, Windows 11, Python 3.12.

## Layout

```
heldout/                harness
  seed.py               seed bugs, keep the ones the suite catches
  coverage_map.py       per-test line coverage (coverage.py dynamic contexts)
  runner.py             pytest runner + per-test result plugin (pytest_record.py)
  evaluate.py           test partitions and the four stopping rules
  repair_search.py      oracle-line and SBFL search baselines
  repair_llm.py         Gemma loop through a llama.cpp server (OpenAI-compatible API)
  analyze.py            builds results/summary.md (every table in the writeup)
  review.py             lists patches that need a manual equivalence check
  portable.py           adds line/column to recorded edits (CRLF vs LF checkouts)
data/bugs_*.jsonl       the 120 bugs: file, function, edit, failing tests, visible test, diff
data/baseline_*.json    tests already failing on the clean checkout (excluded everywhere)
data/coverage_*.json    per-test coverage of the clean checkout
data/tests_*.json       all test node ids
data/seedlog_*.jsonl    every mutant tried, kept or not
results/search_*.jsonl  every search candidate with per-rule verdicts and costs
results/llm_*.jsonl     every Gemma attempt: reply, verdicts, tokens, timings, full conversation
results/manual_review.json  verdict + one-line reason for each reviewed patch
results/summary.md      generated tables
scripts/                setup, word count, path scrubbing
```

## Reproduce

```bash
bash scripts/setup.sh                       # clones rich and click at the pinned commits, builds .venv
PY=.venv/Scripts/python                     # .venv/bin/python on Linux/macOS

$PY -m heldout.analyze                      # rebuild every table from the raw results (about a minute)

# optional: re-run the experiments
$PY -m heldout.seed rich  --target 60 --max-candidates 240     # overwrites data/
$PY -m heldout.seed click --target 60 --max-candidates 240
$PY -m heldout.repair_search --mode oracle-line
$PY -m heldout.repair_search --mode sbfl --cap 40 --staged

# Gemma 4 E2B: llama.cpp build b11829, model google/gemma-4-E2B-it-qat-q4_0-gguf
llama-server -m gemma-4-E2B_q4_0-it.gguf -c 16384 -t 4 --port 8089 --jinja
$PY -m heldout.repair_llm --tag e2b
$PY -m heldout.repair_llm --tag e2b-t08 --retry-temperature 0.8 --copy-solved-from e2b --repo click
```

A second checkout lets two experiments run side by side: clone the projects again under
`work2/` and set `HELDOUT_WORK=work2`. `repair_llm --reverse` walks the bugs from the end, so two
workers can share one model server; `analyze` keeps the first record if they ever race on a bug.

## Notes

- Bugs are seeded, not mined: one-token edits are a narrow slice of real faults. See the writeup's limitations.
- The runs used a Windows checkout with CRLF line endings, so recorded byte offsets refer to CRLF
  files. Every edit also stores line and column, and `Edit.apply` relocates it on an LF checkout
  (checked for all 120 bugs).
- `PYTHONDONTWRITEBYTECODE=1` and a `__pycache__` wipe before every run are deliberate: a mutant with
  the same size, written within the same second as the previous version, could otherwise run stale bytecode.
- Machine-specific paths in recorded pytest output were replaced with `<repo>`, `<python>` and `<tmp>`.
- No data from the Kaggle competition is used or included.

## AI assistance

Built with Claude (Anthropic) as a coding agent; see the writeup's "AI assistance" section.

License: MIT (see LICENSE). Upstream excerpts in `data/` and `results/` keep their licenses (see NOTICE.md).
