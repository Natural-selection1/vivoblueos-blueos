#!/usr/bin/env python3
import argparse
import re
import subprocess
from pathlib import Path

PATH_MAP = {
    "apps_shell": "apps/shell",
    "apps_example": "apps/example",
    "book": "book",
    "build": "build",
    "external": "external",
    "kernel": "kernel",
    "libc": "libc",
    "librs": "librs",
}


def main():
    parser = argparse.ArgumentParser(
        description="Fetch a subtree branch and import it into the blueos repository.",
        allow_abbrev=False,
    )
    parser.add_argument("--owner", required=True, help="GitHub user or organization")
    parser.add_argument("--repo", required=True, help="subtree repository name")
    parser.add_argument("--source-branch", required=True, help="source branch")
    parser.add_argument("--target-branch", required=True, help="local branch to update")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9-]+", args.owner):
        parser.error(f"invalid GitHub owner: {args.owner}")
    if args.repo not in PATH_MAP:
        parser.error(f"unsupported repository: {args.repo}")

    repo_root = Path(__file__).resolve().parent.parent.parent
    for ref in (
        ("--branch", args.source_branch),
        (f"refs/heads/{args.target_branch}",),
    ):
        result = subprocess.run(
            ["git", "check-ref-format", *ref],
            check=False,
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode:
            parser.error(f"invalid branch name: {ref[-1]}")

    commands = (
        [
            "git",
            "fetch",
            f"https://github.com/{args.owner}/{args.repo}.git",
            args.source_branch,
        ],
        [
            "josh-filter",
            f":prefix={PATH_MAP[args.repo]}",
            "FETCH_HEAD",
            "--update",
            f"refs/heads/{args.target_branch}",
        ],
        ["git", "branch"],
    )
    for command in commands:
        result = subprocess.run(command, check=False, cwd=repo_root)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
