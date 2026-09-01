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
import os
import sys

import pytest
import yaml

from proveit.checkers import CHECKERS, content_at
from proveit.grammar import CLAIM_TYPES, DEFAULT_STAGE, STAGED_TYPES
from proveit.parse import parse_claims
from proveit.runner import verify_text


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

    def test_command_cannot_make_a_cached_no_repo_fallback_go_stale(
            self, tmp_path):
        """Every claim observes repository topology at the time it runs."""
        landed = tmp_path / "landed.txt"
        landed.write_text("landed\n", encoding="utf-8")
        setup = tmp_path / "make_repo_then_unpushed_move.py"
        setup.write_text(
            "from pathlib import Path\n"
            "import subprocess\n"
            "work = Path.cwd()\n"
            "remote = work.parent / 'topology-remote.git'\n"
            "def git(*args):\n"
            "    subprocess.run(['git', '-C', str(work), *args], "
            "check=True, capture_output=True)\n"
            "subprocess.run(['git', 'init', '--bare', '-b', 'main', "
            "str(remote)], check=True, capture_output=True)\n"
            "git('init', '-b', 'main')\n"
            "git('config', 'user.email', 'tests@example.invalid')\n"
            "git('config', 'user.name', 'tests')\n"
            "git('config', 'commit.gpgsign', 'false')\n"
            "git('remote', 'add', 'origin', str(remote))\n"
            "git('add', 'landed.txt')\n"
            "git('commit', '-m', 'landed')\n"
            "git('push', '-u', 'origin', 'main')\n"
            "git('mv', 'landed.txt', 'moved.txt')\n"
            "git('commit', '-m', 'unpushed move')\n",
            encoding="utf-8",
        )
        command = f'"{sys.executable}" "{setup}"'
        claims = yaml.safe_dump([
            {"type": "path_exists", "path": str(landed)},
            {"type": "command_exits", "cmd": command,
             "cwd": str(tmp_path)},
            {"type": "path_moved", "src": str(landed),
             "dst": str(tmp_path / "moved.txt")},
        ], sort_keys=False)

        result = verify_text(claims)

        assert result.exit_code == 1
        assert [item.verdict.ok for item in result.results] == [True, True, False]
        evidence = result.results[-1].verdict.evidence
        assert "at pushed commit" in evidence
        assert "still present" in evidence


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


class TestPushedSymlinksAreLexical:
    @pytest.fixture
    def untracked_link(self, repo):
        link = repo / "untracked-link.md"
        try:
            os.symlink("landed.txt", link)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable on this host: {exc}")
        return link

    def test_untracked_link_cannot_satisfy_path_exists(self, untracked_link):
        verdict = run("- type: path_exists\n  path: untracked-link.md\n")
        assert not verdict.ok
        assert "NOT at that commit" in verdict.evidence

    def test_untracked_link_is_absent_at_pushed_state(self, untracked_link):
        assert run("- type: path_absent\n  path: untracked-link.md\n").ok

    def test_untracked_link_cannot_be_the_destination_of_a_move(
            self, untracked_link):
        verdict = run("- type: path_moved\n  src: old.md\n"
                      "  dst: untracked-link.md\n")
        assert not verdict.ok and "deletion, not a move" in verdict.evidence

    def test_untracked_link_cannot_supply_file_content(self, untracked_link):
        verdict = run("- type: file_contains\n  path: untracked-link.md\n"
                      "  text: landed content\n")
        assert not verdict.ok and "NOT at that commit" in verdict.evidence

    def test_untracked_link_cannot_supply_frontmatter(self, repo):
        target = repo / "frontmatter.md"
        target.write_text("---\nstatus: landed\n---\n", encoding="utf-8")
        git(repo, "add", "frontmatter.md")
        git(repo, "commit", "-m", "frontmatter")
        git(repo, "push")
        link = repo / "untracked-frontmatter.md"
        try:
            os.symlink("frontmatter.md", link)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable on this host: {exc}")
        verdict = run("- type: frontmatter_equals\n"
                      "  path: untracked-frontmatter.md\n"
                      "  key: status\n  value: landed\n")
        assert not verdict.ok and "does not exist at pushed commit" in verdict.evidence

    def test_pushed_glob_ignores_untracked_link(self, untracked_link):
        verdict = run("- type: glob_count\n"
                      "  pattern: 'untracked-link*'\n  count: 0\n")
        assert verdict.ok, verdict.evidence

    def test_changed_tracked_link_reads_the_pushed_link_blob(self, repo):
        (repo / "other.txt").write_text("other\n", encoding="utf-8")
        link = repo / "tracked-link.txt"
        try:
            os.symlink("landed.txt", link)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable on this host: {exc}")
        git(repo, "add", "other.txt", "tracked-link.txt")
        git(repo, "commit", "-m", "tracked symlink")
        git(repo, "push")
        link.unlink()
        os.symlink("other.txt", link)

        landed = run("- type: file_contains\n  path: tracked-link.txt\n"
                     "  text: landed.txt\n")
        changed = run("- type: file_contains\n  path: tracked-link.txt\n"
                      "  text: other.txt\n")
        assert landed.ok, landed.evidence
        assert not changed.ok, changed.evidence

    @pytest.fixture
    def intermediate_link(self, repo, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text(
            "unlanded secret\n", encoding="utf-8")
        (outside / "frontmatter.md").write_text(
            "---\nstatus: unlanded\n---\n", encoding="utf-8")
        link = repo / "escape-dir"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"directory symlinks unavailable on this host: {exc}")
        return outside

    def test_intermediate_link_cannot_satisfy_path_exists(
            self, intermediate_link):
        verdict = run("- type: path_exists\n"
                      "  path: escape-dir/secret.txt\n")
        assert not verdict.ok and verdict.detail["stage"] == "pushed"

    def test_intermediate_link_is_absent_at_pushed_state(
            self, intermediate_link):
        assert run("- type: path_absent\n"
                   "  path: escape-dir/secret.txt\n").ok

    def test_intermediate_link_cannot_be_move_destination(
            self, intermediate_link):
        verdict = run("- type: path_moved\n  src: old.md\n"
                      "  dst: escape-dir/secret.txt\n")
        assert not verdict.ok and "deletion, not a move" in verdict.evidence

    def test_intermediate_link_cannot_supply_file_content(
            self, intermediate_link):
        verdict = run("- type: file_contains\n"
                      "  path: escape-dir/secret.txt\n"
                      "  text: unlanded secret\n")
        assert not verdict.ok and verdict.detail["stage"] == "pushed"

    def test_intermediate_link_cannot_supply_frontmatter(
            self, intermediate_link):
        verdict = run("- type: frontmatter_equals\n"
                      "  path: escape-dir/frontmatter.md\n"
                      "  key: status\n  value: unlanded\n")
        assert not verdict.ok and verdict.detail["stage"] == "pushed"

    def test_pushed_glob_does_not_follow_intermediate_link(
            self, intermediate_link):
        verdict = run("- type: glob_count\n  root: escape-dir\n"
                      "  pattern: '*'\n  count: 0\n")
        assert verdict.ok, verdict.evidence

    def test_move_refuses_mixed_pushed_and_worktree_observations(
            self, repo, intermediate_link):
        outside = intermediate_link / "secret.txt"
        verdict = run("- type: path_moved\n  src: old.md\n"
                      f"  dst: {str(outside)!r}\n")
        assert not verdict.ok
        assert "different observations" in verdict.evidence
        assert "source at pushed commit" in verdict.evidence
        assert "destination in the working tree" in verdict.evidence
        assert verdict.detail["src_resolution"]["stage"] == "pushed"
        assert verdict.detail["dst_resolution"]["stage"] == "worktree"


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

    def test_frontmatter_stage_is_accepted_now_that_checker_honours_it(self):
        """Every filesystem checker now honours the declared stage."""
        claims, errors = parse_claims(
            "- type: frontmatter_equals\n  path: a.md\n  key: k\n"
            "  value: v\n  stage: worktree\n")
        assert errors == []
        assert claims[0]["stage"] == "worktree"
        assert "frontmatter_equals" in STAGED_TYPES
        assert "glob_count" in STAGED_TYPES
