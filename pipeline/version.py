"""Which code produced a row or a file: the git commit, so a failure points at an exact version (ADR-010).

In the container the image is built with BASEDNEWS_COMMIT set to the commit it was built from; locally the commit
comes from git, marked "-dirty" when tracked files have uncommitted changes.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def commit() -> str:
    if os.environ.get("BASEDNEWS_COMMIT"):
        return os.environ["BASEDNEWS_COMMIT"]
    try:
        git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, timeout=5).stdout.strip()
        sha = git("rev-parse", "--short=12", "HEAD")
        return f"{sha}-dirty" if sha and git("status", "--porcelain", "--untracked-files=no") else (sha or "unknown")
    except (OSError, subprocess.SubprocessError):
        return "unknown"
