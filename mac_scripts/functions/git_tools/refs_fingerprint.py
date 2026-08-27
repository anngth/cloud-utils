"""Pure helpers for comparing the meaningful refs of a remote repository."""

import re


def parse_ls_remote_fingerprint(stdout: str | None) -> tuple[str, ...]:
    """Return sorted ``sha ref`` entries for heads and tags only."""

    stdout = "" if stdout is None else str(stdout)
    lines: list[str] = []
    for line in re.split(r"\r?\n", stdout):
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        sha, ref = parts[:2]
        if ref.startswith(("refs/heads/", "refs/tags/")):
            lines.append(f"{sha} {ref}")
    return tuple(sorted(lines))


def fingerprints_equal(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return left == right
