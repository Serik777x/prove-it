"""M003 worked examples and CLI exit contract."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def installed_console():
    executable = Path(sys.executable).with_name(
        "prove-it.exe" if os.name == "nt" else "prove-it")
    assert executable.is_file(), f"console entry point is not installed: {executable}"
    return executable


def git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def invoke(tmp_path, body, *extra):
    claims = tmp_path / "claims.yaml"
    claims.write_text(body, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(os.path.dirname(os.path.dirname(__file__)))
    return subprocess.run(
        [sys.executable, "-m", "proveit", "verify", str(claims), *extra],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )


def test_e1_unknown_type_is_exit_2(tmp_path):
    proc = invoke(tmp_path, "- type: file_smells_right\n  path: README.md\n")
    assert proc.returncode == 2
    assert "unknown claim type 'file_smells_right'" in proc.stderr
    assert "known types:" in proc.stderr


def test_e2_missing_required_field_is_exit_2(tmp_path):
    proc = invoke(tmp_path, "- type: file_contains\n  path: README.md\n")
    assert proc.returncode == 2
    assert "missing required field 'text'" in proc.stderr


def test_stable_e3_false_claim_is_exit_1_through_installed_console():
    proc = subprocess.run(
        [str(installed_console()), "verify", "examples/e3-false-claim.yaml"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "FAIL stable-negative-example" in proc.stdout
    assert "PROVE_IT_E3_SENTINEL_MUST_STAY_ABSENT" in proc.stdout
    assert "file exists, 1 line, text not present" in proc.stdout
    assert "in the working tree" in proc.stdout


def test_approved_e3_literal_is_exit_1_through_installed_console(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    (work / "proveit").mkdir(parents=True)
    git(work, "init", "-b", "main")
    git(work, "config", "user.email", "tests@example.invalid")
    git(work, "config", "user.name", "tests")
    git(work, "config", "commit.gpgsign", "false")
    git(work, "remote", "add", "origin", str(remote))
    shutil.copy2(PROJECT_ROOT / "proveit" / "checkers.py",
                 work / "proveit" / "checkers.py")
    git(work, "add", "proveit/checkers.py")
    git(work, "commit", "-m", "candidate")
    git(work, "push", "-u", "origin", "main")

    claims = work / "claims.yaml"
    claims.write_text(
        "- type: file_contains\n"
        "  path: proveit/checkers.py\n"
        "  text: def check_git_pushed\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(installed_console()), "verify", str(claims)],
        cwd=work, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "FAIL file_contains" in proc.stdout
    assert "def check_git_pushed" in proc.stdout
    assert "text not present" in proc.stdout


@pytest.mark.parametrize("json_mode", [False, True])
def test_non_utf8_command_output_still_emits_a_verdict(tmp_path, json_mode):
    cmd = (f'"{sys.executable}" -c '
           '"import sys; sys.stdout.buffer.write(bytes([255]))"')
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "- type: command_exits\n"
        f"  cmd: {cmd!r}\n  cwd: {str(tmp_path)!r}\n",
        encoding="utf-8",
    )
    args = [str(installed_console()), "verify", str(claims)]
    if json_mode:
        args.append("--json")
    proc = subprocess.run(args, capture_output=True, text=True)
    assert proc.returncode == 0
    if json_mode:
        payload = json.loads(proc.stdout)
        assert payload["claims"][0]["status"] == "PASS"
        assert "�" in payload["claims"][0]["evidence"]
    else:
        assert "PASS command_exits" in proc.stdout
        assert "�" in proc.stdout


def test_e4_true_claim_is_exit_0_with_evidence(tmp_path):
    target = tmp_path / "grammar.py"
    target.write_text("hi", encoding="utf-8")
    proc = invoke(tmp_path, "- type: path_exists\n  path: grammar.py\n")
    assert proc.returncode == 0
    assert "PASS path_exists grammar.py" in proc.stdout
    assert "file, 2 bytes" in proc.stdout


def test_json_is_one_document_with_same_exit_code(tmp_path):
    proc = invoke(tmp_path, "- type: path_absent\n  path: missing\n", "--json")
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert payload["claims"][0]["status"] == "PASS"


def test_unreadable_claims_path_is_exit_2(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(os.path.dirname(os.path.dirname(__file__)))
    proc = subprocess.run(
        [sys.executable, "-m", "proveit", "verify", "absent.yaml"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "could not read claims file" in proc.stderr
