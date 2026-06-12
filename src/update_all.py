#!/usr/bin/env python3
"""update_all.py — full national pipeline for scheduled runs."""
import subprocess, sys
from pathlib import Path
HERE = Path(__file__).parent
for step in (["harvest.py"], ["process.py"], ["build_index.py"]):
    cmd = [sys.executable, str(HERE / step[0])]
    print(f"\n>>> {' '.join(cmd)}")
    if subprocess.run(cmd).returncode != 0:
        sys.exit(f"failed: {step}")
print("\nPipeline complete.")
