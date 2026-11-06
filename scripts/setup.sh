#!/usr/bin/env bash
# Clone the two pinned projects and create the virtualenv. Run from the repo root.
set -euo pipefail
python -m venv .venv
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python
"$PY" -m pip install -q -r requirements.txt
mkdir -p work
[ -d work/rich ] || git clone -q https://github.com/Textualize/rich.git work/rich
[ -d work/click ] || git clone -q https://github.com/pallets/click.git work/click
git -C work/rich checkout -q ee8378c3bbbd7c75abc2f55c6c19e83b218ae81d
git -C work/click checkout -q 8b19813f2bfca99f1018a587a8cf54fc959f2e5d
"$PY" -m pip install -q -e work/rich -e work/click
echo "ok: $("$PY" -c 'import rich, click; print(rich.__file__, click.__file__)')"
