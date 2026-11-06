<!-- Word count (python scripts/wordcount.py writeup.md; excludes references and this comment): 2969. Do not paste this line into Kaggle. -->

# When should a small repair agent stop testing?

*120 seeded bugs in rich and click, four stopping rules scored from one test run, and Gemma 4 E2B on a 4-core CPU*

## Abstract

An offline coding agent with a fixed time budget must decide when a patch is good enough to submit. The common rule is to stop once the reproduction test passes. I measured what that rule lets through. I seeded 120 one-token bugs into two real Python projects, rich and click, showed the repairer one failing test per bug, and held the other failing tests out as a hidden grader. I compared four stopping rules: the reproduction test alone, its test file, the tests that execute the patched function, and the whole visible suite.

- A search whose candidates always include the exact fix still submits a wrong patch for 14.2% of bugs under "reproduction only". Running the tests that cover the patched function cuts that to 2.6%, the same as the full suite, at a quarter of the cost.
- With Gemma 4 E2B on a CPU as the repairer, the coverage rule again matches the full suite (5.8% wrong against 10.9%).
- 9 of the 14 Gemma patches that pass every test without restoring the original code still change behaviour.
- On a retry, the model repeats its previous answer verbatim 78% of the time.

The harness, the bugs and every per-test result are public under MIT.

## 1. Introduction

The main track of this competition gives an agent 12 hours to patch about 120 Python repositories offline with a Gemma 4 model, graded by tests the agent never sees. A small model can afford only a few attempts per task, so when to stop is a real engineering decision. Stop too early and a patch that fixes the visible symptom breaks what the grader checks. Stop too late and the budget runs out.

Most scaffolds I have looked at settle this with a reproduction test: edit until it passes, maybe run some neighbouring tests. Program repair research has known for a decade that test-validated patches overfit (Qi et al., 2015; Smith et al., 2015). What I could not find were numbers for the trade-off an agent builder actually faces: how much more testing you need before the wrong-patch rate stops falling, and what each step costs in seconds on cheap hardware.

This writeup contributes:

1. **A resource:** 120 seeded bugs in rich 14.3.4 and click 8.5.0 with their failing tests, per-test coverage of the clean code, and a harness that scores any patch against four stopping rules from one suite run.
2. **Measurements with three repairers:** a single-token search with oracle localization, the same search with spectrum-based localization, and Gemma 4 E2B on llama.cpp with test feedback.
3. **A practical rule.** Run the tests that execute the function you changed. In all three main runs, this caught every wrong patch the full visible suite caught, at a quarter to a half of the cost. Running the reproduction test alone, or its test file, did not.

Everything ran on one old office machine: an Intel i7-6700T (4 cores, 2016), 8 GB of RAM, no GPU. That was a constraint, not a choice.

## 2. Setup

### 2.1 Projects and seeded bugs

| | rich | click |
|---|---|---|
| tag | v14.3.4 | 8.5.0 |
| tests | 1,003 | 2,016 |
| full suite run | ~7 s | ~10 s |

- **Why these two.** Both have fast, mature test suites. rich is one of the four repositories in the competition's public task set; click is not, and checks that the findings are not specific to one codebase. No competition data was used.
- **Excluded tests.** Ten rich tests already fail on the clean checkout (Pygments-dependent rendering and terminal-size tests) and are excluded everywhere.

Every bug is a single-token, byte-level edit:

| family | edits |
|---|---|
| comparison | `<`↔`<=`, `<`↔`>`, `==`↔`!=`, `is`↔`is not`, `in`↔`not in` |
| arithmetic | `+`↔`-` |
| boolean | `and`↔`or`, delete `not`, `True`↔`False` |
| constant | integer n → n±1 |

*Table 1. Operators. The repair search uses the same table plus "insert `not`" on `if`/`while` conditions, so its candidate space always contains the inverse of the seeded edit.*

I mutated only functions of at most 60 lines, on lines some test executes. Functions were shuffled with a fixed seed and one random edit per function was run against the full suite; a mutant that broke at least one test became a bug, up to 60 per project.

| | rich | click |
|---|---|---|
| mutants tried | 83 | 77 |
| kept (broke ≥1 test) | 60 | 60 |
| survived | 23 | 13 |
| hang or collection error | 0 | 4 |
| median failing tests per bug | 4 | 9.5 |
| bugs with one failing test | 11 | 11 |

*Table 2. Seeding. Kept bugs: comparison 44, constant 29, boolean 19, `not` deleted 13, `True`/`False` 8, arithmetic 7.*

### 2.2 What the repairer sees

For each bug I drew one failing test at random (seeded by the bug id) as the **visible failing test**, playing the role of an issue's reproduction. The other failing tests are **hidden**, like a benchmark's held-out validation tests. Every test that passes on the buggy code is **visible**.

A patch is **correct** if the complete suite passes, hidden tests included, and **exact** if its AST equals the original's. **Wrong** means it passed the stopping rule but fails some test. "Correct" only means the project's tests cannot tell the difference. Section 3.4 checks by hand how often that is the same as right.

### 2.3 Four stopping rules

A repair loop accepts a candidate once its validation set passes:

- **min**: the visible failing test only.
- **tfile**: every visible test in the same test file.
- **cov**: the visible failing test plus every visible test that executes the patched function in the clean checkout (coverage.py dynamic contexts, collected once per project).
- **full**: the whole visible suite.

For each candidate the harness first runs the visible failing test (about 1 s). Only if it passes does it run the suite once, recording every test's outcome and duration; all four verdicts come from that run. Costs for `min` and `full` are measured wall-clock time; for `tfile` and `cov` they are pytest start-up plus the selected tests' durations. In the SBFL search, `tfile` and `cov` tests run before the suite, which changes no verdict; when that skips a full run, `full` is charged the project's median suite time.

### 2.4 Repairers

**Oracle-line search.**
- **What it does.** Tries every single-token edit on the seeded line, in source order, until one passes the visible suite.
- **Why it matters.** The exact inverse is always among the candidates, so any wrong patch it accepts is caused by the stopping rule alone.

**SBFL search.**
- **What it does.** Tries the same edits on lines ranked by Ochiai suspiciousness (Abreu et al., 2007), at most 40 candidates per bug.
- **Ranking.** The visible failing test against all visible passing tests, with clean-checkout coverage. The seeded line ranks first for 13 bugs and in the top 10 for 50; its median rank is 16 among about 725 executed lines.

**Gemma 4 E2B.**
- **Model and serving.** Google's QAT Q4_0 GGUF build of the instruction-tuned E2B model (3.3 GB), served by llama.cpp build b11829 on 4 threads, with thinking disabled and temperature 0.
- **What it gets.** The buggy function (oracle function-level localization), the visible test's source and the pytest output. It must return the whole corrected function.
- **Feedback.** After a failure it sees the output of the first failing visible test, for at most three turns. Prompts ran at about 38 tokens/s, generation at about 12.

**Gemma 4 E2B with sampled retries.**
- The same loop, but turns 2 and 3 sample at temperature 0.8 with a fixed seed. Run on the 60 click bugs only, for compute reasons.

## 3. Results

### 3.1 The stopping rule matters even when the fix is in reach

| rule | accepted | exact | other correct | wrong | wrong/accepted | median s per bug |
|---|---|---|---|---|---|---|
| min | 120 | 69 | 34 | 17 | 14.2% | 0.9 |
| tfile | 119 | 74 | 35 | 10 | 8.4% | 2.3 |
| cov | 117 | 79 | 35 | 3 | 2.6% | 2.5 |
| full | 117 | 79 | 35 | 3 | 2.6% | 10.1 |

*Table 3. Oracle-line search, 120 bugs.*

The search always has the exact fix available, yet under `min` it submits a wrong patch for 17 bugs, because some other edit on the same line makes the reproduction pass first. The other rules:

- **tfile** helps less than I expected. Tests for a function often live far from the reproduction. For one rich bug in `text.py` the visible test was in `test_traceback.py`, and the wrong patch broke `test_text.py` and `test_table.py`.
- **cov** leaves 3, the same three the full suite misses. Only the hidden tests themselves can catch those.

`cov` costs a quarter of `full`: its median validation set is 22 tests instead of 1,539, and its wall time is mostly pytest start-up. Bugs with a single failing test are not safe either. The 22 such bugs have no hidden tests at all, yet `min` accepted 3 wrong patches there, each breaking a visible test `min` never ran.

### 3.2 With realistic localization, wrong patches move elsewhere

| rule | accepted | exact | other correct | wrong | wrong/accepted | median s per bug |
|---|---|---|---|---|---|---|
| min | 93 | 48 | 23 | 22 | 23.7% | 6.0 |
| tfile | 89 | 51 | 23 | 15 | 16.9% | 7.5 |
| cov | 88 | 53 | 23 | 12 | 13.6% | 7.4 |
| full | 88 | 53 | 23 | 12 | 13.6% | 15.8 |

*Table 4. SBFL search, 40 candidates per bug.*

Once the search has to find the line itself, two things happen:

- **More bugs go unfixed.** 27 get no plausible patch at all under `min`. For every one of them, the seeded line never made it into the 40-candidate window.
- **The wrong patches change character.** Under `full`, 10 of the 12 wrong patches edit a different line and leave the bug in place, while compensating for it well enough to pass every visible test. The hidden tests, which exercise the bug directly, are what catch them. Under `min` the split is 14 elsewhere and 8 on the seeded line.

`cov` still matches `full` exactly, at half the time per bug; most candidates fail the one-second reproduction check first.

### 3.3 Gemma 4 E2B

| rule | accepted | exact | other correct | wrong | wrong/accepted |
|---|---|---|---|---|---|
| min | 55 | 35 | 14 | 6 | 10.9% |
| tfile | 53 | 35 | 14 | 4 | 7.5% |
| cov | 52 | 35 | 14 | 3 | 5.8% |
| full | 52 | 35 | 14 | 3 | 5.8% |

*Table 5. Gemma 4 E2B, greedy, up to 3 turns, 120 bugs.*

**Stopping rules.** The model accepted fewer patches than the search did, but the pattern of wrong patches is the same. Of the 6 wrong patches under `min`, `cov` catches 3 and `full` catches no more. The 3 that survive both fail only hidden tests.

**The model's failures.** The bigger story is how the model fails:

- **Handing the bug back.** 143 of the 262 applicable replies are AST-identical to the buggy code.
- **Retries repeat themselves.** 112 of 143 retry turns repeat the previous code block character for character, even though the prompt now contains new failure output.
- **When patches land.** Of the 52 accepted patches, 45 came on the first turn and 7 on the second. None came on the third.

**Sampled retries.** Sampling retries at T=0.8 was meant to break the repetition. On the 60 click bugs it barely moved anything: verbatim repeats fell from 56/72 retry turns to 44/72, and patches accepted under `full` went from 26 to 27 (wrong ones from 2 to 1). Two of those came on the third turn, where greedy never succeeded.

A bug took a median of 81 s; the run took 4.3 hours.

### 3.4 "Passes every test" is not "correct"

I read every accepted patch that passes the complete suite without being AST-identical to the original. That is 75 distinct patches.

| source | patches | equivalent | changes behaviour |
|---|---|---|---|
| oracle-line search | 35 | 28 | 7 |
| SBFL search | 23 | 16 | 7 |
| Gemma 4 E2B greedy | 14 | 5 | 9 |
| Gemma 4 E2B, sampled retries | 3 | 2 | 1 |

*Table 6. Diff-by-diff review. Verdicts and one-line reasons are in `results/manual_review.json`.*

**The search.** Its equivalent patches are mostly double negations such as `not (x is not None)`. The rest are genuinely different and simply untested:

- `len(args) != 2` instead of `== 1`;
- treating only exit code 1 as failure;
- `not (a and b)` instead of `a and not b`.

**Gemma.** Its passing patches are larger rewrites and more often wrong. A few examples:

- In click's `human_readable_name`, both branches now return `self.name.upper()`, so `metavar` is ignored. It passes the whole suite.
- Two of its "fixes" add behaviour nobody asked for: rich's progress bar now returns `None` when `total == 0`, and a `try/except` swallows `FileNotFoundError`.

Counting these hidden regressions, the wrong rate under `full` becomes 8.5% for the oracle search, 21.6% for SBFL and 23.1% for Gemma. No stopping rule built from the existing tests can see them.

## 4. Things that went wrong on the way

- **Stale bytecode.** A `<` → `>` mutant keeps the file size, so two writes in the same second can reuse the old `.pyc`. I disable bytecode writing and wipe `__pycache__` before every run.
- **pytest reads `-p` before expanding `@argsfile`.** I passed my result-recording plugin inside an args file, to stay under the Windows command-line limit, and it silently did not load. The first runs reported zero tests.
- **A key collision in my own records.** The patch path and the test-file rule's verdict were both stored under `file`, so one overwrote the other. The `tfile` row briefly looked worse than `min`, which is impossible by construction; that is what gave it away.
- **`coverage json --show-contexts`** took over ten minutes and 2 GB of RAM on click. Reading the database with `CoverageData.contexts_by_lineno` gave the same map in seconds.
- **Prompt size.** rich's tests print very wide output. One prompt overflowed the 8k context, and after I raised it to 16k another, at 40,000 characters, still did. I capped the test output at 4,000 characters and restarted the Gemma run from zero, so every reported run uses the same prompt rules.
- **Concurrency.** To finish in a day I ran two clients against one llama.cpp server. Of six greedy first turns generated twice, one came back different: batched greedy decoding is not bit-reproducible.

## 5. Limitations

- **Seeded bugs are not real bugs.** Mutants are coupled to real faults (Just et al., 2014), but real issues need multi-line, multi-file fixes, where more plausible wrong patches exist. I would expect `min` to look worse there, not better.
- **Sample size.** 120 bugs from two projects is a pilot. A difference of one or two bugs between rows is noise.
- **Coverage.** `cov` uses coverage of the clean code, not of the patched code. A patch that adds new calls could reach code no selected test covers.
- **Timing.** Experiments shared the machine; trust the ratios between rules, not the seconds.
- **Gemma was given the buggy function, and only E2B was tested.** This isolates the stopping question but is not an end-to-end agent; larger variants do not fit next to the tests in 8 GB.
- **Review.** The verdicts were assigned by Claude, reading each diff; they were not independently re-checked by a second person. Every verdict and its one-line reason is published so others can audit and disagree.

## 6. Related work

**Overfitting in test-based repair.** Qi et al. (2015) showed most patches from early generate-and-validate systems were plausible but incorrect, many simply deleting untested behaviour. Smith et al. (2015) named this overfitting and tied it to test-suite quality; Xiong et al. (2018) separated correct from overfitting patches with generated tests. My setup is a controlled version of their question: a candidate space containing the answer, and validation sets that differ only in which tests they include.

**Search-based repair and localization.** GenProg (Le Goues et al., 2012) set the generate-and-validate loop around spectrum-based localization. Ochiai (Abreu et al., 2007) remains a strong default ranking.

**LLM agents on repositories.** SWE-bench (Jimenez et al., 2024) made held-out tests the standard grade for agent patches. SWE-agent (Yang et al., 2024) and Agentless (Xia et al., 2024) use reproduction and regression tests to accept or rank patches. Wang, Pradel and Liu (2025) found that SWE-bench's validation accepts many patches that behave differently from the developers' fix, the gap Section 3.4 measures on a small scale.

**Mutants as faults.** Just et al. (2014) found real faults are often coupled to simple mutants, the usual justification for mutation-based benchmarks. Papadakis et al. (2019) survey the area.

## 7. What I would tell an agent builder

1. **Do not stop on the reproduction alone.** On this data it let through two to six times as many wrong patches as the coverage rule, which costs one or two seconds more per check.
2. **Run the tests that execute the function you changed.** Collect per-test coverage once per repository before the agent starts. That rule matched the full suite in all three main runs, and missed one extra patch only in the 60-bug sampled-retry ablation.
3. **Localization still dominates.** With realistic localization even the full suite let 13.6% wrong patches through, against 2.6% when the line was given. No test-selection rule fixes an edit in the wrong place.
4. **Detect repeated answers.** If a retry returns the same code, stop paying for retries and move on. Sampling helps only a little.

## Reproducibility

Code, bugs, coverage maps and every candidate and model attempt with per-test outcomes are at https://github.com/Julian-Genuario/small-agent-stopping-rules (MIT; upstream excerpts keep their MIT and BSD-3-Clause licences). `scripts/setup.sh` clones both projects at the pinned commits, and `python -m heldout.analyze` rebuilds every table here from the raw results. Re-running everything takes about 10 machine-hours on the hardware above.

## AI assistance

I built this project with Claude (Anthropic) as a coding agent. It proposed the research question within the brief I gave it, wrote most of the harness and analysis code, ran the experiments on my machine, assigned the review verdicts in Section 3.4 and drafted this text. I set the goal and constraints and decided what to submit; the review verdicts in Section 3.4 were not independently re-checked by a human. Every number here comes from the released scripts and raw results.

## References

- Abreu, R., Zoeteweij, P., van Gemund, A. J. C. (2007). On the accuracy of spectrum-based fault localization. TAICPART-MUTATION.
- Gemma Team, Google DeepMind (2026). Gemma 4 E2B QAT Q4_0 GGUF. https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf
- ggml-org. llama.cpp, build b11829. https://github.com/ggml-org/llama.cpp
- Jimenez, C. E., Yang, J., Wettig, A., Yao, S., Pei, K., Press, O., Narasimhan, K. (2024). SWE-bench: Can language models resolve real-world GitHub issues? ICLR.
- Just, R., Jalali, D., Inozemtseva, L., Ernst, M. D., Holmes, R., Fraser, G. (2014). Are mutants a valid substitute for real faults in software testing? FSE.
- Le Goues, C., Nguyen, T., Forrest, S., Weimer, W. (2012). GenProg: A generic method for automatic software repair. IEEE TSE 38(1).
- Papadakis, M., Kintis, M., Zhang, J., Jia, Y., Le Traon, Y., Harman, M. (2019). Mutation testing advances: an analysis and survey. Advances in Computers 112.
- Qi, Z., Long, F., Achour, S., Rinard, M. (2015). An analysis of patch plausibility and correctness for generate-and-validate patch generation systems. ISSTA.
- Smith, E. K., Barr, E. T., Le Goues, C., Brun, Y. (2015). Is the cure worse than the disease? Overfitting in automated program repair. ESEC/FSE.
- Wang, Y., Pradel, M., Liu, Z. (2025). Are "solved issues" in SWE-bench really solved correctly? An empirical study. arXiv:2503.15223.
- Xia, C. S., Deng, Y., Dunn, S., Zhang, L. (2024). Agentless: Demystifying LLM-based software engineering agents. arXiv:2407.01489.
- Xiong, Y., Liu, X., Zeng, M., Zhang, L., Huang, G. (2018). Identifying patch correctness in test-based program repair. ICSE.
- Yang, J., Jimenez, C. E., Wettig, A., Lieret, K., Yao, S., Narasimhan, K., Press, O. (2024). SWE-agent: Agent-computer interfaces enable automated software engineering. NeurIPS.
