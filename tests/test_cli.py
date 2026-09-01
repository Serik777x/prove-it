"""M003 worked examples and CLI exit contract."""

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    executable = Path(sys.executable).with_name(
        "prove-it.exe" if os.name == "nt" else "prove-it")
    assert executable.is_file(), f"console entry point is not installed: {executable}"
    proc = subprocess.run(
        [str(executable), "verify", "examples/e3-false-claim.yaml"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "FAIL stable-negative-example" in proc.stdout
    assert "PROVE_IT_E3_SENTINEL_MUST_STAY_ABSENT" in proc.stdout
    assert "file exists, 1 line, text not present" in proc.stdout
    assert "in the working tree" in proc.stdout


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
