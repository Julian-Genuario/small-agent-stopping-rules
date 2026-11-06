# Third-party material

`data/bugs_*.jsonl` contains unified diffs with short excerpts of source code, and `results/*.jsonl` contains pytest output, test source and model replies that quote code, from:

- **rich** (https://github.com/Textualize/rich), tag v14.3.4, commit ee8378c3. Copyright (c) 2020 Will McGugan. MIT License.
- **click** (https://github.com/pallets/click), tag 8.5.0, commit 8b19813f. Copyright 2014 Pallets. BSD-3-Clause License.

`data/coverage_*.json` and `data/baseline_*.json` list test names and line numbers of those projects.
The projects themselves are not included; `scripts/setup.sh` clones them at the pinned commits.
The excerpts keep their original licenses. Everything else in this repository is MIT (see LICENSE).

No data from the Kaggle "Gemma 4 Developer Agent" competition is included or was used.

The Gemma 4 weights (google/gemma-4-E2B-it-qat-q4_0-gguf, Apache-2.0) and llama.cpp (MIT) are downloaded by the user and not redistributed here.
