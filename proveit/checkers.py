"""Run a claim against real state and report what was actually seen.

Every checker returns a Verdict carrying EVIDENCE, not a bare boolean.
"FAIL file_contains README.md" sends someone back to re-run by hand;
"file exists, 84 lines, text not present" does not.

M002 -- COMPLETE. All ten grammar types have evidence-carrying checkers.
Filesystem checkers resolve through `content_at` or the equivalent glob seam;
Git and process checkers fail loudly when reality cannot be judged.

RESOLUTION STAGE (DEC-002, locked by Stephen on 2026-08-31).
A filesystem claim resolves against PUSHED state by default: the content
of the path at the newest commit that is an ancestor of the branch's
remote tracking ref. The working tree is the opt-in stage, never the
default, because the headline failure this tool exists to catch -- a write
receipt returning ok for a commit that never reached the remote (tickets
H21, M51; incident 20.54) -- leaves the file on disk with exactly the
claimed content. A working-tree check returns green for it and launders
the claim.

Two degradations, and the difference between them is the whole point:

- NO ENCLOSING GIT REPO. There is no landing to prove, so the claim falls
  back to the working tree and every verdict SAYS SO in its evidence.
  Sanctioned explicitly by DEC-002.
- A REPO, BUT NOTHING TO COMPARE AGAINST (no remote, no upstream, no
  commits). There IS a landing to prove and it demonstrably has not
  happened. This does NOT fall back -- it is an unresolvable claim and
  fails loudly, naming `stage: worktree` as the way to ask the weaker
  question on purpose. DEC-002 does not cover this case; see SPEC.md.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

import yaml

from .grammar import ALLOWED_STAGES, DEFAULT_STAGE, STAGE_PUSHED, STAGE_WORKTREE
from .parse import Claim

GIT_TIMEOUT = 30
EVIDENCE_LIMIT = 500


@dataclass(frozen=True)
class Verdict:
    """The result of testing one claim against reality."""

    ok: bool
    evidence: str           # what was looked at and what was found
    detail: dict | None = None   # machine-readable form for --json

    @property
    def status(self) -> str:
        return "PASS" if self.ok else "FAIL"


# --------------------------------------------------------------------------
# resolution layer -- WHERE a path's reality is read from
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """What a path looked like at a given stage, and which stage that was.

    `requested` is what the claim asked for; `stage` is what was actually
    used. They differ only on the sanctioned no-repo fallback, and `where`
    always spells out which one happened so a verdict cannot hide it.

    `error` means the claim COULD NOT BE JUDGED -- it is never a pass.
    """

    requested: str
    stage: str
    where: str                      # evidence fragment, always rendered
    exists: bool
    kind: str | None = None         # file | dir | None (unknown)
    content: bytes | None = None    # only when content=True was asked for
    size: int | None = None         # bytes, for a file
    entries: int | None = None      # child count, for a directory
    error: str | None = None        # unresolvable -- fail loudly
    read_error: str | None = None   # it exists but could not be read

    @property
    def fell_back(self) -> bool:
        return self.requested != self.stage

    def describe(self) -> str:
        """Human description of what is actually at the path."""
        if self.kind == "dir":
            if self.entries is None:
                return "directory"
            return f"directory, {self.entries} entries"
        if self.kind == "file":
            if self.size is None:
                return "file"
            return f"file, {self.size} bytes"
        if self.read_error:
            return f"unreadable ({self.read_error})"
        return "missing"

    def as_detail(self) -> dict:
        return {
            "requested_stage": self.requested,
            "stage": self.stage,
            "exists": self.exists,
            "kind": self.kind,
        }


def _git(cwd, *args) -> tuple[int, bytes, str]:
    """Run git, capturing raw stdout. Never raises."""
    try:
        proc = subprocess.run(["git", "-C", str(cwd), *args],
                              capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return 1, b"", f"git could not be run: {exc}"
    return (proc.returncode, proc.stdout,
            proc.stderr.decode("utf-8", "replace").strip())


def _git_text(cwd, *args) -> tuple[int, str, str]:
    code, out, err = _git(cwd, *args)
    return code, out.decode("utf-8", "replace").strip(), err


def _nearest_existing_dir(path: Path) -> Path | None:
    """Deepest existing directory at or above `path`.

    A claim routinely names a path that does NOT exist -- that is half of
    what it is for -- so repo discovery has to start from something real.
    """
    start = path if path.is_dir() else path.parent
    for candidate in [start, *start.parents]:
        if candidate.is_dir():
            return candidate
    return None


@lru_cache(maxsize=None)
def _repo_root_of_dir(directory: str) -> str | None:
    """Repo containing `directory`, or None. Cached: TOPOLOGY ONLY.

    Deliberately does not cache anything a push can change -- a run that
    checks a path, pushes, and checks again must see the push.
    """
    code, out, _ = _git_text(directory, "rev-parse", "--show-toplevel")
    return out if code == 0 and out else None


def _repo_root(path: Path) -> Path | None:
    absolute = _lexical_absolute(path)
    start = (absolute if absolute.is_dir() and not absolute.is_symlink()
             else absolute.parent)
    for candidate in (start, *start.parents):
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        root = _repo_root_of_dir(str(candidate))
        if not root:
            continue
        root_path = Path(root)
        try:
            absolute.relative_to(root_path)
        except ValueError:
            continue
        return root_path
    return None


def _lexical_absolute(path: Path) -> Path:
    """Absolute path without following the final path or parent symlinks."""
    return Path(os.path.abspath(path))


def _why_nothing_landed(root: Path) -> str:
    """Say precisely why there is no pushed state to compare against."""
    code, _, _ = _git_text(root, "rev-parse", "--verify", "HEAD")
    if code != 0:
        return "the repo has no commits at all"
    code, remotes, _ = _git_text(root, "remote")
    if code == 0 and not remotes:
        return "the repo has no remote configured"
    _, branch, _ = _git_text(root, "rev-parse", "--abbrev-ref", "HEAD")
    return f"branch {branch or 'HEAD'} has no upstream tracking ref"


def _landed_commit(root: Path) -> tuple[str | None, str, bool]:
    """(sha, upstream, behind) -- the newest commit on THIS line that landed.

    `merge-base HEAD @{upstream}` rather than `@{upstream}` itself: the
    question is how much of this line of work reached the remote, not what
    else is sitting there.

    `behind` means the tracking ref carries commits this clone's HEAD does
    not. Content added only in those commits reads as NOT landed here, so
    the verdict has to say the clone is behind rather than quietly blaming
    the claim. An un-pulled clone lies; it should at least lie out loud.

    The tracking ref is a LOCAL CACHE. v1 makes no network calls, so a
    stale `origin/*` is the reader's `git fetch` to do.
    """
    code, upstream, _ = _git_text(
        root, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
        "@{upstream}")
    if code != 0 or not upstream:
        return None, _why_nothing_landed(root), False
    code, sha, _ = _git_text(root, "merge-base", "HEAD", upstream)
    if code != 0 or not sha:
        return None, f"HEAD shares no history with {upstream}", False
    _, tip, _ = _git_text(root, "rev-parse", upstream)
    return sha, upstream, bool(tip) and tip != sha


def _spec(sha: str, rel: str) -> str:
    return f"{sha}^{{tree}}" if rel in ("", ".") else f"{sha}:{rel}"


def _resolve_worktree(path: Path, requested: str, where: str,
                      want_content: bool) -> Resolution:
    def make(**kw):
        return Resolution(requested=requested, stage=STAGE_WORKTREE,
                          where=where, **kw)

    if not path.exists():
        return make(exists=False)
    if path.is_dir():
        try:
            entries = sum(1 for _ in path.iterdir())
        except OSError:
            entries = None
        return make(exists=True, kind="dir", entries=entries)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return make(exists=True, read_error=exc.strerror or str(exc))

    content = None
    if want_content:
        try:
            content = path.read_bytes()
        except OSError as exc:
            return make(exists=True, kind="file", size=size,
                        read_error=exc.strerror or str(exc))
    return make(exists=True, kind="file", size=size, content=content)


def _resolve_pushed(root: Path, sha: str, upstream: str, rel: str,
                    want_content: bool, behind: bool = False) -> Resolution:
    where = f"at pushed commit {sha[:7]} ({upstream}"
    where += "; this clone is BEHIND it -- run git fetch/pull)" if behind else ")"
    spec = _spec(sha, rel)

    def make(**kw):
        return Resolution(requested=STAGE_PUSHED, stage=STAGE_PUSHED,
                          where=where, **kw)

    code, kind, _ = _git_text(root, "cat-file", "-t", spec)
    if code != 0:
        return make(exists=False)

    if kind == "tree":
        code, listing, _ = _git_text(root, "ls-tree", "--name-only", spec)
        entries = len(listing.splitlines()) if code == 0 else None
        return make(exists=True, kind="dir", entries=entries)

    code, size_text, _ = _git_text(root, "cat-file", "-s", spec)
    size = int(size_text) if code == 0 and size_text.isdigit() else None

    content = None
    if want_content:
        code, raw, err = _git(root, "cat-file", "blob", spec)
        if code != 0:
            return make(exists=True, kind="file", size=size,
                        read_error=err or "git could not read the blob")
        content = raw
    return make(exists=True, kind="file", size=size, content=content)


def content_at(path, stage: str = DEFAULT_STAGE, *,
               content: bool = True) -> Resolution:
    """Resolve a path's reality at `stage`. THE seam DEC-002 asks for.

    Every filesystem checker reads through this and nowhere else, so the
    default stage is one line in one place rather than a decision each
    checker re-makes -- and one of them eventually re-makes it wrong.
    """
    if stage not in ALLOWED_STAGES:
        return Resolution(
            requested=stage, stage=stage, where="", exists=False,
            error=(f"unknown stage {stage!r} -- expected one of "
                   f"{', '.join(ALLOWED_STAGES)}"))

    p = Path(path)

    if stage == STAGE_WORKTREE:
        return _resolve_worktree(p, stage, "in the working tree", content)

    root = _repo_root(p)
    if root is None:
        return _resolve_worktree(
            p, stage,
            "in the working tree (no enclosing git repo -- fell back from "
            "pushed)", content)

    sha, upstream_or_reason, behind = _landed_commit(root)
    if sha is None:
        return Resolution(
            requested=stage, stage=stage, where="", exists=False,
            error=(f"cannot resolve pushed state -- {upstream_or_reason}. "
                   f"Nothing has landed, so a pass here would prove nothing; "
                   f"add `stage: worktree` to ask the weaker question on "
                   f"purpose."))

    try:
        rel = _lexical_absolute(p).relative_to(root).as_posix()
    except ValueError:
        return _resolve_worktree(
            p, stage,
            f"in the working tree (outside {root} -- fell back from pushed)",
            content)

    return _resolve_pushed(root, sha, upstream_or_reason, rel, content, behind)


# --------------------------------------------------------------------------
# checkers
# --------------------------------------------------------------------------


def _stage_of(claim: Claim) -> str:
    return claim.get("stage", DEFAULT_STAGE)


def _unresolvable(label: str, res: Resolution) -> Verdict:
    return Verdict(False, f"{label}: {res.error}",
                   {**res.as_detail(), "error": res.error})


def _missing_hint(path: Path, res: Resolution) -> str:
    """Why the absence is interesting, in the terms of the stage used."""
    if res.stage == STAGE_WORKTREE:
        return ("parent exists, path does not" if path.parent.exists()
                else "parent directory does not exist either")
    if path.exists():
        return ("present in the working tree but NOT at that commit "
                "-- the work never landed")
    return "absent from the working tree too"


def check_path_exists(claim: Claim) -> Verdict:
    path = Path(claim["path"])
    kind = claim.get("kind", "any")
    res = content_at(path, _stage_of(claim), content=False)
    if res.error:
        return _unresolvable(str(path), res)

    detail = {"path": str(path), **res.as_detail()}

    if not res.exists:
        return Verdict(False,
                       f"{path} not found {res.where} -- "
                       f"{_missing_hint(path, res)}", detail)

    actual = res.kind
    if kind != "any" and actual != kind:
        return Verdict(False,
                       f"{path} exists {res.where} but is a {actual}, "
                       f"claimed {kind}", detail)

    return Verdict(True, f"{path} ({res.describe()}) {res.where}", detail)


def check_path_absent(claim: Claim) -> Verdict:
    path = Path(claim["path"])
    res = content_at(path, _stage_of(claim), content=False)
    if res.error:
        return _unresolvable(str(path), res)

    detail = {"path": str(path), **res.as_detail()}
    if res.exists:
        return Verdict(False,
                       f"{path} is still present {res.where} "
                       f"({res.describe()})", detail)
    return Verdict(True, f"{path} absent {res.where}", detail)


def check_path_moved(claim: Claim) -> Verdict:
    stage = _stage_of(claim)
    src, dst = Path(claim["src"]), Path(claim["dst"])

    src_res = content_at(src, stage, content=False)
    if src_res.error:
        return _unresolvable(str(src), src_res)
    dst_res = content_at(dst, stage, content=False)
    if dst_res.error:
        return _unresolvable(str(dst), dst_res)

    src_root = _repo_root(src) if stage == STAGE_PUSHED else None
    dst_root = _repo_root(dst) if stage == STAGE_PUSHED else None
    same_observation = (src_res.stage == dst_res.stage
                        and src_res.where == dst_res.where
                        and src_root == dst_root)
    provenance = (f"source {src_res.where}; destination {dst_res.where}")
    detail = {"src": str(src), "dst": str(dst),
              "src_exists": src_res.exists, "dst_exists": dst_res.exists,
              "requested_stage": stage,
              "src_resolution": src_res.as_detail(),
              "dst_resolution": dst_res.as_detail(),
              "src_repo": str(src_root) if src_root else None,
              "dst_repo": str(dst_root) if dst_root else None}

    if stage == STAGE_PUSHED and not same_observation:
        return Verdict(
            False,
            f"cannot prove one pushed move across different observations -- "
            f"{provenance}; use stage: worktree to assert a local "
            "cross-boundary move on purpose",
            detail,
        )

    where = src_res.where

    if src_res.exists and dst_res.exists:
        return Verdict(False,
                       f"both ends present {where} -- {src} was COPIED, "
                       f"not moved", detail)
    if src_res.exists:
        return Verdict(False,
                       f"{src} still present {where}, {dst} not created",
                       detail)
    if not dst_res.exists:
        return Verdict(False,
                       f"{src} is gone but {dst} was never created {where} "
                       f"-- this is a deletion, not a move", detail)

    return Verdict(True, f"{src} -> {dst} ({dst_res.describe()}) {where}",
                   detail)


def check_file_contains(claim: Claim) -> Verdict:
    path = Path(claim["path"])
    needle = claim["text"]
    want = claim.get("count")
    res = content_at(path, _stage_of(claim), content=True)
    if res.error:
        return _unresolvable(str(path), res)

    detail = {"path": str(path), **res.as_detail()}

    if not res.exists:
        return Verdict(False,
                       f"{path} does not exist {res.where} -- "
                       f"{_missing_hint(path, res)}", detail)
    if res.kind == "dir":
        return Verdict(False, f"{path} is a directory, not a file", detail)
    if res.content is None:
        return Verdict(False,
                       f"{path} could not be read as UTF-8 text: "
                       f"{res.read_error}", {**detail, "readable": False})

    try:
        body = res.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        return Verdict(False,
                       f"{path} could not be read as UTF-8 text: {exc}",
                       {**detail, "readable": False})

    found = body.count(needle)
    lines = len(body.splitlines())
    line_count = f"{lines} {'line' if lines == 1 else 'lines'}"
    detail.update({"found": found, "lines": lines})

    if want is not None and found != want:
        return Verdict(False,
                       f"looked for {needle!r} {res.where} -- found {found} "
                       f"times, claimed {want} ({line_count})", detail)
    if found == 0:
        return Verdict(False,
                       f"looked for {needle!r} {res.where} -- file exists, "
                       f"{line_count}, text not present", detail)

    return Verdict(True,
                   f"{needle!r} found {found}x in {path} ({line_count}) "
                   f"{res.where}", detail)


def _text_at(path: Path, stage: str) -> tuple[Resolution, str | None]:
    """Read UTF-8 text through the DEC-002 resolution seam."""
    res = content_at(path, stage, content=True)
    if res.error or not res.exists or res.kind != "file" or res.content is None:
        return res, None
    try:
        return res, res.content.decode("utf-8")
    except UnicodeDecodeError:
        return res, None


def check_frontmatter_equals(claim: Claim) -> Verdict:
    path = Path(claim["path"])
    key, expected = claim["key"], claim["value"]
    res, body = _text_at(path, _stage_of(claim))
    detail = {"path": str(path), "key": key, "expected": expected,
              **res.as_detail()}
    if res.error:
        return _unresolvable(str(path), res)
    if not res.exists:
        return Verdict(False, f"{path} does not exist {res.where}", detail)
    if res.kind != "file":
        return Verdict(False, f"{path} is a directory, not a file", detail)
    if body is None:
        return Verdict(False, f"{path} is not readable UTF-8 text {res.where}",
                       detail)
    lines = body.splitlines()
    if not lines or lines[0].strip() != "---":
        return Verdict(False, f"{path} has no YAML frontmatter {res.where}",
                       detail)
    try:
        end = next(i for i, line in enumerate(lines[1:], 1)
                   if line.strip() == "---")
    except StopIteration:
        return Verdict(False,
                       f"{path} has unterminated YAML frontmatter {res.where}",
                       detail)
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        return Verdict(False,
                       f"{path} has invalid YAML frontmatter {res.where}: {exc}",
                       detail)
    if not isinstance(frontmatter, dict):
        return Verdict(False,
                       f"{path} frontmatter is not a mapping {res.where}", detail)
    if key not in frontmatter:
        return Verdict(False,
                       f"frontmatter key {key!r} missing in {path} {res.where}",
                       detail)
    actual = frontmatter[key]
    detail["actual"] = actual
    if actual != expected:
        return Verdict(False,
                       f"frontmatter {key!r} is {actual!r}, claimed "
                       f"{expected!r} in {path} {res.where}", detail)
    return Verdict(True,
                   f"frontmatter {key!r} equals {expected!r} in {path} "
                   f"{res.where}", detail)


def _worktree_glob(root: Path, pattern: str, requested: str,
                   where: str) -> tuple[list[str], str, str | None]:
    if (Path(pattern).is_absolute() or PurePosixPath(pattern).is_absolute()
            or PureWindowsPath(pattern).is_absolute()):
        return [], where, f"glob pattern must be relative, got {pattern!r}"
    try:
        matches = sorted(p.relative_to(root).as_posix()
                         for p in root.glob(pattern))
    except (OSError, ValueError, NotImplementedError) as exc:
        return [], where, str(exc)
    return matches, where, None


def _glob_at(root: Path, pattern: str, stage: str) -> tuple[list[str], str, str,
                                                            str | None]:
    """Resolve a glob at pushed or working-tree state."""
    if stage not in ALLOWED_STAGES:
        return [], stage, "", f"unknown stage {stage!r}"
    if stage == STAGE_WORKTREE:
        matches, where, error = _worktree_glob(
            root, pattern, stage, "in the working tree")
        return matches, stage, where, error

    repo = _repo_root(root)
    if repo is None:
        where = ("in the working tree (no enclosing git repo -- fell back "
                 "from pushed)")
        matches, _, error = _worktree_glob(root, pattern, stage, where)
        return matches, STAGE_WORKTREE, where, error
    sha, upstream_or_reason, behind = _landed_commit(repo)
    if sha is None:
        return [], stage, "", f"cannot resolve pushed state -- {upstream_or_reason}"
    try:
        relative_root = _lexical_absolute(root).relative_to(repo)
        prefix = "" if relative_root == Path(".") else relative_root.as_posix()
    except ValueError:
        return [], stage, "", f"glob root {root} is outside repo {repo}"
    code, listing, err = _git_text(
        repo, "ls-tree", "-r", "-t", "--name-only", sha)
    if code != 0:
        return [], stage, "", err or "git could not list pushed tree"
    matches = []
    prefix_with_slash = f"{prefix}/" if prefix else ""
    for item in listing.splitlines():
        if prefix_with_slash and not item.startswith(prefix_with_slash):
            continue
        rel = item[len(prefix_with_slash):]
        if rel and PurePosixPath(rel).full_match(pattern):
            matches.append(rel)
    where = f"at pushed commit {sha[:7]} ({upstream_or_reason}"
    where += "; this clone is BEHIND it -- run git fetch/pull)" if behind else ")"
    return sorted(set(matches)), stage, where, None


def check_glob_count(claim: Claim) -> Verdict:
    root = Path(claim.get("root", "."))
    pattern, expected = claim["pattern"], claim["count"]
    matches, actual_stage, where, error = _glob_at(
        root, pattern, _stage_of(claim))
    detail = {"root": str(root), "pattern": pattern, "count": len(matches),
              "matches": matches, "requested_stage": _stage_of(claim),
              "stage": actual_stage}
    if error:
        return Verdict(False, f"glob {pattern!r}: {error}", detail)
    if len(matches) != expected:
        return Verdict(False,
                       f"glob {pattern!r} under {root} matched {len(matches)} "
                       f"paths, claimed {expected} {where}: {matches}", detail)
    return Verdict(True,
                   f"glob {pattern!r} under {root} matched {expected} paths "
                   f"{where}: {matches}", detail)


def _clipped(value: str) -> str:
    value = value.strip()
    return value if len(value) <= EVIDENCE_LIMIT else value[:EVIDENCE_LIMIT] + "..."


def check_command_exits(claim: Claim) -> Verdict:
    cmd = claim["cmd"]
    expected = claim.get("code", 0)
    cwd = Path(claim.get("cwd", "."))
    timeout = claim.get("timeout", 60)
    detail = {"cmd": cmd, "cwd": str(cwd), "expected_code": expected}
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        detail["timeout"] = timeout
        return Verdict(False, f"command timed out after {timeout}s: {cmd}", detail)
    except OSError as exc:
        return Verdict(False, f"command could not run in {cwd}: {exc}", detail)
    detail.update({"actual_code": proc.returncode,
                   "stdout": _clipped(proc.stdout),
                   "stderr": _clipped(proc.stderr)})
    evidence = f"command exited {proc.returncode}, claimed {expected}: {cmd}"
    if proc.stdout.strip():
        evidence += f"; stdout={_clipped(proc.stdout)!r}"
    if proc.stderr.strip():
        evidence += f"; stderr={_clipped(proc.stderr)!r}"
    return Verdict(proc.returncode == expected, evidence, detail)


def _repo_or_failure(path: str) -> tuple[Path | None, Verdict | None]:
    repo = Path(path)
    code, root, err = _git_text(repo, "rev-parse", "--show-toplevel")
    if code != 0 or not root:
        return None, Verdict(False, f"{repo} is not a git repository: {err}",
                             {"repo": str(repo)})
    return Path(root), None


def check_git_head_is(claim: Claim) -> Verdict:
    root, failure = _repo_or_failure(claim["repo"])
    if failure:
        return failure
    assert root is not None
    code, head, err = _git_text(root, "rev-parse", "HEAD")
    if code != 0:
        return Verdict(False, f"cannot resolve HEAD in {root}: {err}")
    claimed = claim["sha"]
    detail = {"repo": str(root), "head": head, "claimed": claimed}
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", claimed):
        return Verdict(
            False,
            f"claimed commit {claimed!r} is not a hexadecimal object id "
            "or prefix; symbolic revisions are not fixed evidence",
            detail,
        )
    code, expected, err = _git_text(root, "rev-parse", "--verify",
                                    f"{claimed}^{{commit}}")
    if code != 0:
        return Verdict(False, f"claimed commit {claimed!r} does not resolve: {err}",
                       detail)
    detail["expected"] = expected
    return Verdict(head == expected,
                   f"HEAD is {head}, claimed {expected} in {root}", detail)


def check_git_clean(claim: Claim) -> Verdict:
    root, failure = _repo_or_failure(claim["repo"])
    if failure:
        return failure
    assert root is not None
    include_untracked = claim.get("untracked", False)
    flag = "--untracked-files=all" if include_untracked else "--untracked-files=no"
    code, output, err = _git_text(root, "status", "--porcelain", flag)
    if code != 0:
        return Verdict(False, f"git status failed in {root}: {err}")
    changes = output.splitlines() if output else []
    detail = {"repo": str(root), "untracked": include_untracked,
              "changes": changes}
    if changes:
        return Verdict(False, f"repo is dirty ({len(changes)} changes): {changes}",
                       detail)
    scope = "tracked and untracked files" if include_untracked else "tracked files"
    return Verdict(True, f"repo is clean for {scope}: {root}", detail)


def check_pushed_state(claim: Claim) -> Verdict:
    root, failure = _repo_or_failure(claim["repo"])
    if failure:
        return failure
    assert root is not None
    remote, named_ref = claim.get("remote", "origin"), claim.get("ref")
    remote_prefix = f"refs/remotes/{remote}/"
    if named_ref:
        if named_ref.startswith("refs/"):
            target = named_ref
        elif named_ref.startswith(f"{remote}/"):
            target = f"refs/remotes/{named_ref}"
        elif "/" in named_ref:
            return Verdict(
                False,
                f"ref {named_ref!r} does not belong to requested remote "
                f"{remote!r}",
                {"repo": str(root), "remote": remote, "ref": named_ref},
            )
        else:
            target = f"{remote_prefix}{named_ref}"
        if not target.startswith(remote_prefix):
            return Verdict(
                False,
                f"ref {named_ref!r} is not a remote-tracking ref for "
                f"requested remote {remote!r}",
                {"repo": str(root), "remote": remote, "ref": named_ref},
            )
    else:
        code, target, err = _git_text(
            root, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
            "@{upstream}")
        if code != 0 or not target:
            return Verdict(False, f"HEAD has no upstream tracking ref in {root}: {err}",
                           {"repo": str(root), "remote": remote})
        if target.startswith(f"{remote}/"):
            target = f"refs/remotes/{target}"
        if not target.startswith(remote_prefix):
            return Verdict(
                False,
                f"HEAD tracks {target!r}, not requested remote {remote!r}",
                {"repo": str(root), "remote": remote, "ref": target},
            )
    code, target_sha, err = _git_text(root, "rev-parse", "--verify", target)
    if code != 0:
        return Verdict(False, f"remote ref {target!r} does not resolve: {err}",
                       {"repo": str(root), "ref": target})
    _, head, _ = _git_text(root, "rev-parse", "HEAD")
    code, _, err = _git_text(root, "merge-base", "--is-ancestor", "HEAD", target)
    detail = {"repo": str(root), "head": head, "remote": remote, "ref": target,
              "ref_sha": target_sha}
    if code == 0:
        return Verdict(True, f"HEAD {head[:12]} is pushed to {target} "
                       f"({target_sha[:12]})", detail)
    if code == 1:
        return Verdict(False, f"HEAD {head[:12]} has not landed on {target} "
                       f"({target_sha[:12]})", detail)
    return Verdict(False, f"could not compare HEAD with {target}: {err}", detail)


# Wired checkers, by claim type. A type in the grammar with no entry here
# is NOT silently skipped -- the runner (M003) treats a missing checker as
# a hard error, same as an unknown type.
CHECKERS: dict[str, Callable[[Claim], Verdict]] = {
    "path_exists": check_path_exists,
    "path_absent": check_path_absent,
    "path_moved": check_path_moved,
    "file_contains": check_file_contains,
    "frontmatter_equals": check_frontmatter_equals,
    "glob_count": check_glob_count,
    "command_exits": check_command_exits,
    "git_head_is": check_git_head_is,
    "git_clean": check_git_clean,
    "git_pushed": check_pushed_state,
}
