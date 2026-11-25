"""Secret-bearing local files (OpenTofu state/vars, .env variants) must be git-ignored."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

MUST_IGNORE = [
    "deploy/tofu/terraform.tfstate",
    "deploy/tofu/terraform.tfstate.backup",
    "deploy/tofu/terraform.tfvars",
    "deploy/tofu/.terraform/x",
    ".env",
    ".env.local",
]
MUST_NOT_IGNORE = [".env.example", "deploy/tofu/terraform.tfvars.example"]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def in_git_tree() -> None:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    res = _git("rev-parse", "--is-inside-work-tree")
    if res.returncode != 0 or res.stdout.strip() != "true":
        pytest.skip("not inside a git work tree")


@pytest.mark.parametrize("path", MUST_IGNORE)
def test_secret_files_are_ignored(in_git_tree: None, path: str) -> None:
    # --no-index: judge by the ignore rules even if a file were ever tracked.
    res = _git("check-ignore", "-q", "--no-index", path)
    assert res.returncode == 0, f"{path} is not git-ignored"


@pytest.mark.parametrize("path", MUST_NOT_IGNORE)
def test_examples_are_not_ignored(in_git_tree: None, path: str) -> None:
    res = _git("check-ignore", "-q", "--no-index", path)
    assert res.returncode == 1, f"{path} is git-ignored but must be committed"
