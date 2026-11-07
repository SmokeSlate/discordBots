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
    counts = _run_git(repo, "rev-list", "--left-right", "--count", f"HEAD...{remote}/{branch}")
    if counts.returncode != 0:
        return {"ok": False, "reason": "ancestry_failed", "details": counts.stderr.strip()}
    try:
        ahead, behind = map(int, counts.stdout.split())
    except ValueError:
        return {"ok": False, "reason": "invalid_ancestry", "details": "Expected ahead/behind counts"}
    relationship = "diverged" if ahead and behind else "behind" if behind else "ahead" if ahead else "equal"
    return {
        "ok": True,
        "up_to_date": behind == 0,
        "can_fast_forward": behind > 0 and ahead == 0,
        "relationship": relationship,
        "ahead": ahead,
        "behind": behind,
        "local_sha": local_value,
        "remote_sha": remote_value,
    }


def apply_git_update(repo_dir: str, remote: str = "origin", branch: str = "main") -> Dict[str, Any]:
    """Apply a fast-forward only update from remote branch."""
    repo = Path(repo_dir)
    before = _run_git(repo, "rev-parse", "HEAD")
    pull = _run_git(repo, "pull", "--ff-only", remote, branch)
    after = _run_git(repo, "rev-parse", "HEAD")
    return {
        "ok": pull.returncode == 0 and before.returncode == 0 and after.returncode == 0,
        "changed": pull.returncode == 0 and before.returncode == 0 and after.returncode == 0 and before.stdout.strip() != after.stdout.strip(),
        "stdout": pull.stdout.strip(),
        "stderr": pull.stderr.strip(),
        "code": pull.returncode,
    }
