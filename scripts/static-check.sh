#!/bin/sh
# Run syntax and contract checks without importing project modules or writing bytecode.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"

PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
import ast
from pathlib import Path

for path in (Path("main.py"), Path("test_magick.py")):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("Python syntax parsing passed")
PY

for script in entrypoint.sh cloud/hfs/export_space_bundle.sh cloud/hfs/smoke-test.sh \
    scripts/validate-hfs-contract.sh scripts/static-check.sh; do
    sh -n "$script"
done
printf '%s\n' "Shell syntax parsing passed"

./scripts/validate-hfs-contract.sh
