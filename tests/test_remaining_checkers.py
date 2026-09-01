"""M002 completion: frontmatter, glob, process, and Git checkers."""

import subprocess
import sys

import pytest

from proveit.checkers import CHECKERS, _repo_root_of_dir
from proveit.parse import parse_claims


def git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def run(text):
    claims, errors = parse_claims(text)
    assert errors == [], errors
    claim = claims[0]
    return CHECKERS[claim.type](claim)


@pytest.fixture(autouse=True)
def clear_repo_cache():
    _repo_root_of_dir.cache_clear()
    yield
    _repo_root_of_dir.cache_clear()


@pytest.fixture
def landed_repo(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-b", "main")
    git(work, "config", "user.email", "tests@example.invalid")
    git(work, "config", "user.name", "tests")
    git(work, "config", "commit.gpgsign", "false")
    git(work, "remote", "add", "origin", str(remote))
    (work / "note.md").write_text(
        "---\nstatus: approved\ncount: 2\n---\nbody\n", encoding="utf-8")
    (work / "other.md").write_text("other\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-m", "landed")
    git(work, "push", "-u", "origin", "main")
    monkeypatch.chdir(work)
    return work


class TestFrontmatterEquals:
    def test_reads_pushed_frontmatter_not_uncommitted_edit(self, landed_repo):
        (landed_repo / "note.md").write_text(
            "---\nstatus: draft\ncount: 9\n---\nbody\n", encoding="utf-8")
        verdict = run("- type: frontmatter_equals\n  path: note.md\n"
                      "  key: status\n  value: approved\n")
        assert verdict.ok, verdict.evidence
        assert verdict.detail["stage"] == "pushed"

    def test_reports_actual_value(self, landed_repo):
        verdict = run("- type: frontmatter_equals\n  path: note.md\n"
                      "  key: count\n  value: 3\n")
        assert not verdict.ok
        assert verdict.detail["actual"] == 2


class TestGlobCount:
    def test_untracked_match_does_not_satisfy_pushed_default(self, landed_repo):
        (landed_repo / "ghost.md").write_text("not landed\n", encoding="utf-8")
        verdict = run("- type: glob_count\n  pattern: '*.md'\n  count: 2\n")
        assert verdict.ok, verdict.evidence
        assert verdict.detail["stage"] == "pushed"

    def test_worktree_stage_sees_untracked_match(self, landed_repo):
        (landed_repo / "ghost.md").write_text("not landed\n", encoding="utf-8")
        verdict = run("- type: glob_count\n  pattern: '*.md'\n  count: 3\n"
                      "  stage: worktree\n")
        assert verdict.ok, verdict.evidence

    def test_pushed_glob_respects_nested_root(self, landed_repo):
        nested = landed_repo / "docs"
        nested.mkdir()
        (nested / "inside.md").write_text("inside\n", encoding="utf-8")
        git(landed_repo, "add", "-A")
        git(landed_repo, "commit", "-m", "nested")
        git(landed_repo, "push")
        verdict = run("- type: glob_count\n  root: docs\n"
                      "  pattern: '*.md'\n  count: 1\n")
        assert verdict.ok, verdict.evidence
        assert verdict.detail["matches"] == ["inside.md"]

    def test_recursive_pattern_includes_root_and_nested_files(self, landed_repo):
        nested = landed_repo / "docs"
        nested.mkdir()
        (nested / "inside.md").write_text("inside\n", encoding="utf-8")
        git(landed_repo, "add", "-A")
        git(landed_repo, "commit", "-m", "nested")
        git(landed_repo, "push")
        verdict = run("- type: glob_count\n  pattern: '**/*.md'\n  count: 3\n")
        assert verdict.ok, verdict.evidence


class TestCommandExits:
    def test_expected_nonzero_exit_passes_with_evidence(self, tmp_path):
        cmd = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
        verdict = run("- type: command_exits\n"
                      f"  cmd: {cmd!r}\n  code: 3\n  cwd: {str(tmp_path)!r}\n")
        assert verdict.ok
        assert verdict.detail["actual_code"] == 3

    def test_wrong_exit_fails(self, tmp_path):
        cmd = f'"{sys.executable}" -c "import sys; sys.exit(4)"'
        verdict = run("- type: command_exits\n"
                      f"  cmd: {cmd!r}\n  code: 0\n  cwd: {str(tmp_path)!r}\n")
        assert not verdict.ok and verdict.detail["actual_code"] == 4


class TestGitCheckers:
    def test_head_is_accepts_short_sha(self, landed_repo):
        sha = git(landed_repo, "rev-parse", "--short", "HEAD")
        verdict = run("- type: git_head_is\n  repo: .\n"
                      f"  sha: {sha}\n")
        assert verdict.ok, verdict.evidence

    def test_clean_ignores_untracked_unless_requested(self, landed_repo):
        (landed_repo / "untracked.txt").write_text("x", encoding="utf-8")
        assert run("- type: git_clean\n  repo: .\n").ok
        verdict = run("- type: git_clean\n  repo: .\n  untracked: true\n")
        assert not verdict.ok and verdict.detail["changes"]

    def test_clean_fails_on_tracked_change(self, landed_repo):
        (landed_repo / "note.md").write_text("changed\n", encoding="utf-8")
        assert not run("- type: git_clean\n  repo: .\n").ok

    def test_pushed_tracks_actual_ancestry(self, landed_repo):
        assert run("- type: git_pushed\n  repo: .\n").ok
        (landed_repo / "new.txt").write_text("local\n", encoding="utf-8")
        git(landed_repo, "add", "-A")
        git(landed_repo, "commit", "-m", "not pushed")
        verdict = run("- type: git_pushed\n  repo: .\n")
        assert not verdict.ok and "has not landed" in verdict.evidence
        git(landed_repo, "push")
        assert run("- type: git_pushed\n  repo: .\n").ok
