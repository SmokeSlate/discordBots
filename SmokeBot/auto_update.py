"""Helpers for automatic bot updates using git."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict


def _run_git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        check=False,
        capture_output=True,
        text=True,
    )


def get_git_update_status(repo_dir: str, remote: str = "origin", branch: str = "main") -> Dict[str, Any]:
    """Return whether the repo is behind the configured remote branch."""
    repo = Path(repo_dir)
    is_git = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    if is_git.returncode != 0:
        return {"ok": False, "reason": "not_git_repo", "details": is_git.stderr.strip()}

    fetch = _run_git(repo, "fetch", remote, branch)
    if fetch.returncode != 0:
        return {"ok": False, "reason": "fetch_failed", "details": fetch.stderr.strip()}

    local_sha = _run_git(repo, "rev-parse", "HEAD")
    remote_sha = _run_git(repo, "rev-parse", f"{remote}/{branch}")

    if local_sha.returncode != 0 or remote_sha.returncode != 0:
        return {"ok": False, "reason": "rev_parse_failed", "details": (local_sha.stderr + remote_sha.stderr).strip()}

    local_value = local_sha.stdout.strip()
    remote_value = remote_sha.stdout.strip()
    return {
        "ok": True,
        "up_to_date": local_value == remote_value,
        "local_sha": local_value,
        "remote_sha": remote_value,
    }


def apply_git_update(repo_dir: str, remote: str = "origin", branch: str = "main") -> Dict[str, Any]:
    """Apply a fast-forward only update from remote branch."""
    repo = Path(repo_dir)
    pull = _run_git(repo, "pull", "--ff-only", remote, branch)
    return {
        "ok": pull.returncode == 0,
        "stdout": pull.stdout.strip(),
        "stderr": pull.stderr.strip(),
        "code": pull.returncode,
    }
