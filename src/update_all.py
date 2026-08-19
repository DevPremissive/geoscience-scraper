#!/usr/bin/env python3
"""update_all.py — full national pipeline for scheduled runs.

**Guarded, because importing this module used to run it.** The pipeline was
written as top-level statements, so `import update_all` — from an import check,
a linter, a docs tool, or `python -c "import update_all"` — launched a full
national harvest as a side effect of being read. That happened on 2026-08-18
while building C4.2: the loop fired, `harvest.py` started fetching Ontario, and
CKAN discovery took an HTTP 429 before it was killed. Nothing was lost (the
staged-download design from audit I1 left one `.part` file and no ledger rows),
but nothing should have started either.

A module that does work when imported has no safe way to be inspected.
"""
import subprocess, sys
from pathlib import Path

HERE = Path(__file__).parent

STEPS = (["harvest.py"], ["process.py"], ["build_index.py"], ["coverage.py"])


def main() -> None:
    for step in STEPS:
        cmd = [sys.executable, str(HERE / step[0])]
        print(f"\n>>> {' '.join(cmd)}")
        if subprocess.run(cmd).returncode != 0:
            sys.exit(f"failed: {step}")
    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
