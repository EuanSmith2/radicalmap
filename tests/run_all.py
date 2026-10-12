#!/usr/bin/env python
"""Run every test file. No pytest dependency — each file is self-running."""
from __future__ import annotations
import subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = sorted(p for p in HERE.glob("test_*.py"))
failed = []
for f in FILES:
    print(f"\n{'=' * 64}\n{f.name}\n{'=' * 64}")
    r = subprocess.run([sys.executable, str(f)], cwd=HERE.parent)
    if r.returncode != 0:
        failed.append(f.name)
print(f"\n{'=' * 64}")
print(f"  {len(FILES) - len(failed)}/{len(FILES)} files passed")
if failed:
    sys.exit("  failed: " + ", ".join(failed))
