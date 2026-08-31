"""Run a claim against real state and report what was actually seen.

Every checker returns a Verdict carrying EVIDENCE, not a bare boolean.
"FAIL file_contains README.md" sends someone back to re-run by hand;
"file exists, 84 lines, text not present" does not.

M002 -- IN PROGRESS. Filesystem checkers are done; git and process
checkers are not written yet. See CHECKERS below for what is wired.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .parse import Claim


@dataclass(frozen=True)
class Verdict:
    """The result of testing one claim against reality."""

    ok: bool
    evidence: str           # what was looked at and what was found
    detail: dict | None = None   # machine-readable form for --json

    @property
    def status(self) -> str:
        return "PASS" if self.ok else "FAIL"


def _describe(path: Path) -> str:
    """Human description of what is actually at a path."""
    try:
        stat = path.stat()
    except OSError as exc:
        return f"unreadable ({exc.strerror})"
    if path.is_dir():
        try:
            n = sum(1 for _ in path.iterdir())
        except OSError:
            return "directory"
        return f"directory, {n} entries"
    return f"file, {stat.st_size} bytes"


def check_path_exists(claim: Claim) -> Verdict:
    path = Path(claim["path"])
    kind = claim.get("kind", "any")

    if not path.exists():
        parent = path.parent
        hint = ("parent directory does not exist either"
                if not parent.exists() else "parent exists, path does not")
        return Verdict(False, f"{path} not found -- {hint}",
                       {"path": str(path), "exists": False})

    actual = "dir" if path.is_dir() else "file"
    if kind != "any" and actual != kind:
        return Verdict(False,
                       f"{path} exists but is a {actual}, claimed {kind}",
                       {"path": str(path), "exists": True, "kind": actual})

    return Verdict(True, f"{path} ({_describe(path)})",
                   {"path": str(path), "exists": True, "kind": actual})


def check_path_absent(claim: Claim) -> Verdict:
    path = Path(claim["path"])
    if path.exists():
        return Verdict(False, f"{path} is still present ({_describe(path)})",
                       {"path": str(path), "exists": True})
    return Verdict(True, f"{path} absent",
                   {"path": str(path), "exists": False})


def check_path_moved(claim: Claim) -> Verdict:
    src, dst = Path(claim["src"]), Path(claim["dst"])
    src_there, dst_there = src.exists(), dst.exists()

    if src_there and dst_there:
        return Verdict(False,
                       f"both ends present -- {src} was COPIED, not moved",
                       {"src_exists": True, "dst_exists": True})
    if src_there:
        return Verdict(False, f"{src} still present, {dst} not created",
                       {"src_exists": True, "dst_exists": False})
    if not dst_there:
        return Verdict(False,
                       f"{src} is gone but {dst} was never created "
                       f"-- this is a deletion, not a move",
                       {"src_exists": False, "dst_exists": False})

    return Verdict(True, f"{src} -> {dst} ({_describe(dst)})",
                   {"src_exists": False, "dst_exists": True})


def check_file_contains(claim: Claim) -> Verdict:
    path = Path(claim["path"])
    needle = claim["text"]
    want = claim.get("count")

    if not path.exists():
        return Verdict(False, f"{path} does not exist",
                       {"path": str(path), "exists": False})
    if path.is_dir():
        return Verdict(False, f"{path} is a directory, not a file",
                       {"path": str(path), "exists": True})

    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Verdict(False, f"{path} could not be read as UTF-8 text: {exc}",
                       {"path": str(path), "readable": False})

    found = body.count(needle)
    lines = body.count("\n") + 1
    detail = {"path": str(path), "found": found, "lines": lines}

    if want is not None and found != want:
        return Verdict(False,
                       f"looked for {needle!r} -- found {found} times, "
                       f"claimed {want} ({lines} lines)", detail)
    if found == 0:
        return Verdict(False,
                       f"looked for {needle!r} -- file exists, {lines} lines, "
                       f"text not present", detail)

    return Verdict(True, f"{needle!r} found {found}x in {path} ({lines} lines)",
                   detail)


# Wired checkers, by claim type. A type in the grammar with no entry here
# is NOT silently skipped -- the runner (M003) treats a missing checker as
# a hard error, same as an unknown type.
CHECKERS: dict[str, Callable[[Claim], Verdict]] = {
    "path_exists": check_path_exists,
    "path_absent": check_path_absent,
    "path_moved": check_path_moved,
    "file_contains": check_file_contains,
}

# NOT YET IMPLEMENTED -- frontmatter_equals, glob_count, command_exits,
# git_head_is, git_clean, git_pushed.
