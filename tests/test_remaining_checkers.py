"""M002 completion: frontmatter, glob, process, and Git checkers."""

import os
import shutil
import subprocess
import sys

import pytest
import yaml

from proveit.checkers import CHECKERS, command_policy
from proveit.parse import parse_claims
from proveit.runner import RunResult, verify_text


def git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def run(text, allowed_commands=()):
    claims, errors = parse_claims(text)
    assert errors == [], errors
    claim = claims[0]
    with command_policy(allowed_commands):
        return CHECKERS[claim.type](claim)


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

    def test_recursive_actual_value_returns_failed_verdict(self, landed_repo):
        (landed_repo / "recursive.md").write_text(
            "---\nloop: &loop [*loop]\n---\nbody\n", encoding="utf-8")
        git(landed_repo, "add", "recursive.md")
        git(landed_repo, "commit", "-m", "recursive frontmatter")
        git(landed_repo, "push")

        verdict = run("- type: frontmatter_equals\n  path: recursive.md\n"
                      "  key: loop\n  value: []\n")

        assert not verdict.ok
        assert "recursive YAML alias" in verdict.evidence
        assert "actual" not in verdict.detail
        assert "actual_error" in verdict.detail

    @pytest.mark.parametrize("body,key,expected", [
        ("flag: true", "flag", "1"),
        ("count: 1", "count", "1.0"),
        ("nested: [true, {count: 1}]", "nested", "[1, {count: 1.0}]"),
    ])
    def test_yaml_equality_preserves_scalar_types(
            self, landed_repo, body, key, expected):
        (landed_repo / "typed.md").write_text(
            f"---\n{body}\n---\nbody\n", encoding="utf-8")
        git(landed_repo, "add", "typed.md")
        git(landed_repo, "commit", "-m", "typed frontmatter")
        git(landed_repo, "push")

        verdict = run("- type: frontmatter_equals\n  path: typed.md\n"
                      f"  key: {key}\n  value: {expected}\n")

        assert not verdict.ok
        assert "claimed" in verdict.evidence


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

    def test_absolute_pattern_returns_evidence_instead_of_raising(self,
                                                                  landed_repo):
        verdict = run("- type: glob_count\n  root: .\n"
                      "  pattern: /tmp/*\n  count: 0\n"
                      "  stage: worktree\n")
        assert not verdict.ok
        assert "must be relative" in verdict.evidence

    def test_enumeration_error_is_not_proven_zero(
            self, landed_repo, monkeypatch):
        locked = landed_repo / "locked"
        locked.mkdir()
        (locked / "present.md").write_text("present\n", encoding="utf-8")
        original = os.scandir

        def deny(path):
            if os.path.abspath(path) == str(locked):
                raise PermissionError("enumeration denied")
            return original(path)

        monkeypatch.setattr(os, "scandir", deny)
        verdict = run("- type: glob_count\n  root: locked\n"
                      "  pattern: '*'\n  count: 0\n  stage: worktree\n")

        assert not verdict.ok
        assert "cannot enumerate glob root" in verdict.evidence


class TestCommandExits:
    def test_expected_nonzero_exit_passes_with_evidence(self, tmp_path):
        cmd = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
        verdict = run("- type: command_exits\n"
                      f"  cmd: {cmd!r}\n  code: 3\n  cwd: {str(tmp_path)!r}\n",
                      [sys.executable])
        assert verdict.ok
        assert verdict.detail["actual_code"] == 3

    def test_wrong_exit_fails(self, tmp_path):
        cmd = f'"{sys.executable}" -c "import sys; sys.exit(4)"'
        verdict = run("- type: command_exits\n"
                      f"  cmd: {cmd!r}\n  code: 0\n  cwd: {str(tmp_path)!r}\n",
                      [sys.executable])
        assert not verdict.ok and verdict.detail["actual_code"] == 4

    def test_non_utf8_output_returns_evidence_instead_of_raising(self,
                                                                  tmp_path):
        cmd = (f'"{sys.executable}" -c '
               '"import sys; sys.stdout.buffer.write(bytes([255]))"')
        result = verify_text(
            "- type: command_exits\n"
            f"  cmd: {cmd!r}\n  cwd: {str(tmp_path)!r}\n",
            allowed_commands=[sys.executable])
        assert isinstance(result, RunResult)
        assert result.exit_code == 0
        assert "stdout='�'" in result.results[0].verdict.evidence

    def test_default_policy_denies_mutation_before_execution(self, tmp_path):
        target = tmp_path / "must-not-exist.txt"
        cmd = (f'"{sys.executable}" -c '
               f'"from pathlib import Path; Path({str(target)!r}).write_text(\'x\')"')

        result = verify_text(yaml.safe_dump([{
            "type": "command_exits",
            "cmd": cmd,
            "cwd": str(tmp_path),
        }], sort_keys=False))

        assert result.exit_code == 1
        assert "command policy denied" in result.results[0].verdict.evidence
        assert not target.exists()

    def test_relative_allowlist_cannot_be_redirected_by_claim_cwd(
            self, tmp_path, monkeypatch):
        safe = tmp_path / "safe"
        attack = tmp_path / "attack"
        safe.mkdir()
        attack.mkdir()
        name = "same-name.exe" if os.name == "nt" else "same-name"
        shutil.copy2(sys.executable, safe / name)
        shutil.copy2(sys.executable, attack / name)
        if os.name != "nt":
            (safe / name).chmod(0o755)
            (attack / name).chmod(0o755)
        monkeypatch.chdir(safe)
        claims = yaml.safe_dump([{
            "type": "command_exits",
            "cmd": f"./{name} --version",
            "cwd": str(attack),
        }], sort_keys=False)

        result = verify_text(claims, allowed_commands=[f"./{name}"])

        assert result.exit_code == 1
        verdict = result.results[0].verdict
        assert "command policy denied" in verdict.evidence
        assert os.path.normcase(str(attack / name)) == verdict.detail["executable"]


class TestGitCheckers:
    def test_head_is_accepts_short_sha(self, landed_repo):
        sha = git(landed_repo, "rev-parse", "--short", "HEAD")
        verdict = run("- type: git_head_is\n  repo: .\n"
                      f"  sha: '{sha}'\n")
        assert verdict.ok, verdict.evidence

    @pytest.mark.parametrize("symbolic", ["HEAD", "main", "HEAD~1"])
    def test_head_is_rejects_symbolic_revision(self, landed_repo, symbolic):
        verdict = run("- type: git_head_is\n  repo: .\n"
                      f"  sha: {symbolic}\n")
        assert not verdict.ok
        assert "not a hexadecimal object id" in verdict.evidence

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

    def test_pushed_honours_requested_remote(self, landed_repo, tmp_path):
        upstream = tmp_path / "upstream.git"
        subprocess.run(["git", "init", "--bare", "-b", "main",
                        str(upstream)], check=True, capture_output=True)
        git(landed_repo, "remote", "add", "upstream", str(upstream))
        git(landed_repo, "push", "-u", "upstream", "main")
        (landed_repo / "only-upstream.txt").write_text("x\n", encoding="utf-8")
        git(landed_repo, "add", "-A")
        git(landed_repo, "commit", "-m", "only upstream")
        git(landed_repo, "push", "upstream", "main")

        verdict = run("- type: git_pushed\n  repo: .\n  remote: origin\n")
        assert not verdict.ok
        assert "not requested remote 'origin'" in verdict.evidence

        local_ref = run("- type: git_pushed\n  repo: .\n  remote: origin\n"
                        "  ref: refs/heads/main\n")
        assert not local_ref.ok
        assert "not a remote-tracking ref" in local_ref.evidence

        explicit = run("- type: git_pushed\n  repo: .\n  remote: upstream\n"
                       "  ref: main\n")
        assert explicit.ok, explicit.evidence
