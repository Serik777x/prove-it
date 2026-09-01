"""M002 -- checkers must fail loudly and say what they actually saw."""

import pytest

from proveit.checkers import CHECKERS
from proveit.parse import parse_claims


def run(text, tmp_path=None):
    claims, errors = parse_claims(text)
    assert errors == [], errors
    claim = claims[0]
    return CHECKERS[claim.type](claim)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestPathExists:
    def test_passes_for_a_real_file(self, repo):
        (repo / "a.txt").write_text("hi")
        v = run("- type: path_exists\n  path: a.txt\n")
        assert v.ok and "file, 2 bytes" in v.evidence

    def test_fails_when_missing(self, repo):
        v = run("- type: path_exists\n  path: nope.txt\n")
        assert not v.ok and "not found" in v.evidence

    def test_missing_parent_is_called_out(self, repo):
        v = run("- type: path_exists\n  path: no/such/dir/a.txt\n")
        assert "parent directory does not exist either" in v.evidence

    def test_kind_mismatch_fails(self, repo):
        (repo / "d").mkdir()
        v = run("- type: path_exists\n  path: d\n  kind: file\n")
        assert not v.ok and "is a dir, claimed file" in v.evidence

    def test_kind_any_accepts_a_directory(self, repo):
        (repo / "d").mkdir()
        assert run("- type: path_exists\n  path: d\n").ok


class TestPathAbsent:
    def test_passes_when_gone(self, repo):
        assert run("- type: path_absent\n  path: nope\n").ok

    def test_fails_when_still_there(self, repo):
        (repo / "a.txt").write_text("x")
        v = run("- type: path_absent\n  path: a.txt\n")
        assert not v.ok and "still present" in v.evidence


class TestPathMoved:
    def test_passes_on_a_real_move(self, repo):
        (repo / "b.txt").write_text("x")
        v = run("- type: path_moved\n  src: a.txt\n  dst: b.txt\n")
        assert v.ok

    def test_copy_is_not_a_move(self, repo):
        (repo / "a.txt").write_text("x")
        (repo / "b.txt").write_text("x")
        v = run("- type: path_moved\n  src: a.txt\n  dst: b.txt\n")
        assert not v.ok and "COPIED, not moved" in v.evidence

    def test_deletion_is_not_a_move(self, repo):
        v = run("- type: path_moved\n  src: a.txt\n  dst: b.txt\n")
        assert not v.ok and "deletion, not a move" in v.evidence

    def test_nothing_happened(self, repo):
        (repo / "a.txt").write_text("x")
        v = run("- type: path_moved\n  src: a.txt\n  dst: b.txt\n")
        assert not v.ok and "still present" in v.evidence


class TestFileContains:
    def test_passes_and_counts(self, repo):
        (repo / "a.py").write_text("def x():\n    pass\ndef x_again():\n")
        v = run("- type: file_contains\n  path: a.py\n  text: 'def x'\n")
        assert v.ok and v.detail["found"] == 2

    def test_fails_with_line_count_evidence(self, repo):
        (repo / "a.py").write_text("one\ntwo\nthree\n")
        v = run("- type: file_contains\n  path: a.py\n  text: absent\n")
        assert not v.ok
        assert "text not present" in v.evidence and "3 lines" in v.evidence

    def test_empty_file_reports_zero_lines(self, repo):
        (repo / "empty.txt").write_text("")
        v = run("- type: file_contains\n  path: empty.txt\n  text: absent\n")
        assert not v.ok and "0 lines" in v.evidence

    def test_exact_count_mismatch_fails(self, repo):
        (repo / "a.py").write_text("xx")
        v = run("- type: file_contains\n  path: a.py\n  text: x\n  count: 1\n")
        assert not v.ok and "found 2 times, claimed 1" in v.evidence

    def test_missing_file_fails(self, repo):
        v = run("- type: file_contains\n  path: nope\n  text: x\n")
        assert not v.ok and "does not exist" in v.evidence

    def test_directory_is_not_a_file(self, repo):
        (repo / "d").mkdir()
        v = run("- type: file_contains\n  path: d\n  text: x\n")
        assert not v.ok and "is a directory" in v.evidence


class TestWiring:
    def test_every_wired_checker_is_a_known_claim_type(self):
        from proveit.grammar import CLAIM_TYPES
        assert set(CHECKERS) == set(CLAIM_TYPES)

    def test_verdict_status_string(self, repo):
        (repo / "a").write_text("x")
        assert run("- type: path_exists\n  path: a\n").status == "PASS"
        assert run("- type: path_absent\n  path: a\n").status == "FAIL"
