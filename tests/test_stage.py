"""M002 / DEC-002 -- claims resolve against PUSHED state, not the worktree.

These tests exist because tests/test_checkers.py CANNOT catch a broken
retrofit. Every fixture there is built in a bare `tmp_path` with no git
repository at all, so the whole suite exercises only the no-repo fallback
and stays green against pure working-tree semantics.

So this module builds a real repo with a real bare remote and drives the
actual failure the tool was built for: work that is committed locally and
never pushed. On the working tree that claim passes -- the file is right
there with exactly the claimed bytes. It must FAIL here.
"""

import subprocess

import pytest

from proveit.checkers import CHECKERS, _repo_root_of_dir, content_at
from proveit.grammar import CLAIM_TYPES, DEFAULT_STAGE, STAGED_TYPES
from proveit.parse import parse_claims


def git(cwd, *args) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


def run(text):
    claims, errors = parse_claims(text)
    assert errors == [], errors
    claim = claims[0]
    return CHECKERS[claim.type](claim)


@pytest.fixture(autouse=True)
def _no_stale_topology():
    """Repo discovery is cached; a fresh tmp_path per test must not inherit."""
    _repo_root_of_dir.cache_clear()
    yield
    _repo_root_of_dir.cache_clear()


def _init(work, remote=None):
    work.mkdir(exist_ok=True)
    git(work, "init", "-b", "main")
    git(work, "config", "user.email", "drill@example.invalid")
    git(work, "config", "user.name", "drill")
    git(work, "config", "commit.gpgsign", "false")
    if remote is not None:
        git(work, "remote", "add", "origin", str(remote))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real repo whose `main` tracks a real bare remote, with one commit
    already landed: `landed.txt` containing 'landed content'."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    _init(work, remote)
    (work / "landed.txt").write_text("landed content\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-m", "landed")
    git(work, "push", "-u", "origin", "main")
    monkeypatch.chdir(work)
    return work


def commit_only(work, name, body):
    """Write and COMMIT a file without pushing -- the H21/M51 shape."""
    (work / name).write_text(body, encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-m", f"add {name}")


class TestTheLyingReceipt:
    """The regression this whole decision exists for."""

    def test_committed_but_not_pushed_fails_by_default(self, repo):
        commit_only(repo, "ghost.py", "def check_git_pushed():\n    pass\n")

        # The bait: on disk, the claim is true in every detail.
        assert (repo / "ghost.py").read_text() == \
            "def check_git_pushed():\n    pass\n"

        v = run("- type: file_contains\n  path: ghost.py\n"
                "  text: 'def check_git_pushed'\n")
        assert not v.ok, f"a working-tree read leaked through: {v.evidence}"
        assert v.detail["stage"] == "pushed"
        assert "never landed" in v.evidence

    def test_the_same_claim_passes_once_it_actually_lands(self, repo):
        commit_only(repo, "ghost.py", "def check_git_pushed():\n    pass\n")
        claim = ("- type: file_contains\n  path: ghost.py\n"
                 "  text: 'def check_git_pushed'\n")
        assert not run(claim).ok

        git(repo, "push")

        v = run(claim)
        assert v.ok, v.evidence
        assert "at pushed commit" in v.evidence

    def test_worktree_is_the_opt_in_escape_hatch(self, repo):
        commit_only(repo, "ghost.py", "def check_git_pushed():\n    pass\n")
        v = run("- type: file_contains\n  path: ghost.py\n"
                "  text: 'def check_git_pushed'\n  stage: worktree\n")
        assert v.ok
        assert v.detail["stage"] == "worktree"
        assert "in the working tree" in v.evidence

    def test_an_uncommitted_edit_cannot_satisfy_a_claim(self, repo):
        """Content really comes from the object store, not from disk."""
        (repo / "landed.txt").write_text("secretly rewritten\n",
                                         encoding="utf-8")
        v = run("- type: file_contains\n  path: landed.txt\n"
                "  text: secretly rewritten\n")
        assert not v.ok and "text not present" in v.evidence

        # ...and the landed content is still what gets read.
        assert run("- type: file_contains\n  path: landed.txt\n"
                   "  text: landed content\n").ok

    def test_evidence_names_the_commit_it_read(self, repo):
        landed = git(repo, "rev-parse", "HEAD")
        v = run("- type: file_contains\n  path: landed.txt\n"
                "  text: landed content\n")
        assert v.ok
        assert landed[:7] in v.evidence and "origin/main" in v.evidence


class TestOtherCheckersHonourTheStage:
    def test_path_exists_needs_the_file_to_have_landed(self, repo):
        commit_only(repo, "ghost.txt", "x\n")
        v = run("- type: path_exists\n  path: ghost.txt\n")
        assert not v.ok
        assert "present in the working tree but NOT at that commit" in v.evidence
        assert run("- type: path_exists\n  path: ghost.txt\n"
                   "  stage: worktree\n").ok

    def test_path_exists_passes_on_landed_state(self, repo):
        v = run("- type: path_exists\n  path: landed.txt\n  kind: file\n")
        assert v.ok and "file, 15 bytes" in v.evidence

    def test_path_absent_needs_the_deletion_to_land(self, repo):
        (repo / "landed.txt").unlink()
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "delete")

        v = run("- type: path_absent\n  path: landed.txt\n")
        assert not v.ok, "a deletion that never left the box read as gone"
        assert "still present" in v.evidence

        git(repo, "push")
        assert run("- type: path_absent\n  path: landed.txt\n").ok

    def test_path_moved_needs_the_move_to_land(self, repo):
        git(repo, "mv", "landed.txt", "moved.txt")
        git(repo, "commit", "-m", "move")

        claim = "- type: path_moved\n  src: landed.txt\n  dst: moved.txt\n"
        v = run(claim)
        assert not v.ok
        assert "still present" in v.evidence

        git(repo, "push")
        assert run(claim).ok

    def test_a_directory_resolves_at_the_pushed_stage(self, repo):
        (repo / "pkg").mkdir()
        (repo / "pkg" / "a.txt").write_text("a\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "pkg")
        git(repo, "push")

        v = run("- type: path_exists\n  path: pkg\n  kind: dir\n")
        assert v.ok and "directory, 1 entries" in v.evidence


class TestBehindClone:
    """merge-base semantics, pinned deliberately.

    Resolution is `merge-base HEAD @{upstream}` -- the newest commit of
    THIS line of work that landed -- not the tracking ref's tip. A clone
    that is behind therefore reads content pushed by someone else as NOT
    landed. That is a false alarm rather than a false pass, which is the
    safe direction, but it must be VISIBLE or it reads as prove-it lying.
    """

    def _push_from_elsewhere(self, tmp_path, work, name, body):
        other = tmp_path / "other"
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"),
                        str(other)], check=True, capture_output=True)
        git(other, "config", "user.email", "other@example.invalid")
        git(other, "config", "user.name", "other")
        git(other, "config", "commit.gpgsign", "false")
        (other / name).write_text(body, encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-m", f"other adds {name}")
        git(other, "push")
        git(work, "fetch", "origin")

    def test_a_behind_clone_says_so(self, repo, tmp_path):
        self._push_from_elsewhere(tmp_path, repo, "theirs.txt", "theirs\n")

        v = run("- type: file_contains\n  path: landed.txt\n"
                "  text: landed content\n")
        assert v.ok
        assert "this clone is BEHIND it" in v.evidence

    def test_content_only_on_the_remote_reads_as_not_landed(self, repo,
                                                            tmp_path):
        self._push_from_elsewhere(tmp_path, repo, "theirs.txt", "theirs\n")
        v = run("- type: path_exists\n  path: theirs.txt\n")
        assert not v.ok
        assert "this clone is BEHIND it" in v.evidence

    def test_an_up_to_date_clone_carries_no_warning(self, repo):
        v = run("- type: path_exists\n  path: landed.txt\n")
        assert v.ok and "BEHIND" not in v.evidence


class TestDegradation:
    def test_no_enclosing_repo_falls_back_and_says_so(self, tmp_path,
                                                      monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
        v = run("- type: path_exists\n  path: a.txt\n")
        assert v.ok
        assert v.detail["requested_stage"] == "pushed"
        assert v.detail["stage"] == "worktree"
        assert "no enclosing git repo" in v.evidence

    def test_a_repo_with_no_remote_refuses_to_guess(self, tmp_path,
                                                    monkeypatch):
        """prove-it's own situation (R-001). A repo means there IS a landing
        to prove, and it demonstrably has not happened -- so this must not
        quietly degrade to the working tree the way the no-repo case does."""
        work = tmp_path / "solo"
        _init(work)
        (work / "a.txt").write_text("hi", encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-m", "one")
        monkeypatch.chdir(work)

        v = run("- type: file_contains\n  path: a.txt\n  text: hi\n")
        assert not v.ok
        assert "no remote configured" in v.evidence
        assert "stage: worktree" in v.evidence

    def test_a_branch_with_no_upstream_refuses_to_guess(self, repo):
        git(repo, "checkout", "-b", "sidebranch")
        v = run("- type: file_contains\n  path: landed.txt\n"
                "  text: landed content\n")
        assert not v.ok
        assert "no upstream tracking ref" in v.evidence

    def test_an_empty_repo_refuses_to_guess(self, tmp_path, monkeypatch):
        work = tmp_path / "empty"
        _init(work)
        (work / "a.txt").write_text("hi", encoding="utf-8")
        monkeypatch.chdir(work)
        v = run("- type: path_exists\n  path: a.txt\n")
        assert not v.ok and "no commits at all" in v.evidence

    def test_unknown_stage_is_a_loud_failure(self, repo):
        res = content_at("landed.txt", "banana")
        assert res.error and "unknown stage 'banana'" in res.error
        assert "pushed" in res.error and "worktree" in res.error


class TestGrammarAgreement:
    def test_the_default_stage_is_pushed(self):
        assert DEFAULT_STAGE == "pushed"
        for name in STAGED_TYPES:
            assert CLAIM_TYPES[name].defaults["stage"] == "pushed"

    def test_every_staged_type_has_a_checker(self):
        assert set(STAGED_TYPES) <= set(CHECKERS)

    def test_every_wired_fs_checker_declares_a_stage(self):
        """Anti-drift: an fs checker added without `stage` reads the
        working tree with no way for a claim to say otherwise."""
        wired_fs = {n for n in CHECKERS if CLAIM_TYPES[n].domain == "fs"}
        assert wired_fs == set(STAGED_TYPES)

    def test_worktree_stage_is_rejected_on_types_that_cannot_honour_it(self):
        """frontmatter_equals has no checker yet, so it must NOT accept
        `stage` -- an accepted-then-ignored field is the silent pass."""
        _, errors = parse_claims(
            "- type: frontmatter_equals\n  path: a.md\n  key: k\n"
            "  value: v\n  stage: worktree\n")
        assert any("unknown field 'stage'" in e.message for e in errors)
