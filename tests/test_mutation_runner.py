"""Tests for the mutation-lens runner (mutants -> real test runs -> confidence)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from avera.mutation import MutationLensError, run_mutation_lens
from avera.mutation import runner as runner_mod
from avera.mutation.score import UNVERIFIED, VERIFIED

AGES = "def is_adult(age):\n    return age >= 18\n"

STRONG_CHECK = (
    "from ages import is_adult\n"
    "assert is_adult(18) is True\n"
    "assert is_adult(17) is False\n"
)

WEAK_CHECK = "from ages import is_adult\nassert is_adult(30)\n"


def _project(tmp_path: Path, module: str, check: str, name: str = "ages") -> tuple[Path, list[str]]:
    src = tmp_path / f"{name}.py"
    src.write_text(module, encoding="utf-8")
    check_file = tmp_path / "check.py"
    check_file.write_text(check, encoding="utf-8")
    return src, [sys.executable, str(check_file)]


# ---------------------------------------------------------------------------
# Kill / survive
# ---------------------------------------------------------------------------

def test_strong_tests_kill_every_mutant(tmp_path):
    src, cmd = _project(tmp_path, AGES, STRONG_CHECK)
    report = run_mutation_lens(src, cmd, cwd=tmp_path)
    assert report.confidence.total == 3
    assert report.confidence.killed == 3
    assert report.confidence.verdict == VERIFIED
    assert report.confidence.is_blind_spot is False


def test_weak_tests_leave_survivors_as_blind_spot(tmp_path):
    src, cmd = _project(tmp_path, AGES, WEAK_CHECK)
    report = run_mutation_lens(src, cmd, cwd=tmp_path)
    # Only `return None` is caught; the boundary and the constant survive.
    assert report.confidence.killed == 1
    assert report.confidence.survived == 2
    assert report.confidence.verdict == UNVERIFIED
    assert report.confidence.is_blind_spot is True
    survivors = " | ".join(report.confidence.survivors)
    assert "GtE -> Gt" in survivors
    assert "18 -> 19" in survivors


def test_function_scope_limits_mutations(tmp_path):
    module = AGES + "\n\ndef other(x):\n    return x < 5\n"
    src, cmd = _project(tmp_path, module, STRONG_CHECK)
    report = run_mutation_lens(src, cmd, function="is_adult", cwd=tmp_path)
    assert report.confidence.total == 3
    assert all(o.lineno <= 2 for o in report.outcomes)


def test_unknown_function_fails_closed(tmp_path):
    src, cmd = _project(tmp_path, AGES, STRONG_CHECK)
    with pytest.raises(MutationLensError, match="not found"):
        run_mutation_lens(src, cmd, function="nope", cwd=tmp_path)


# ---------------------------------------------------------------------------
# Honesty guards
# ---------------------------------------------------------------------------

def test_red_baseline_fails_closed_and_leaves_file_untouched(tmp_path):
    src, cmd = _project(tmp_path, AGES, "raise SystemExit(1)\n")
    before = src.read_bytes()
    with pytest.raises(MutationLensError, match="baseline"):
        run_mutation_lens(src, cmd, cwd=tmp_path)
    assert src.read_bytes() == before


def test_null_mutant_must_pass(tmp_path, monkeypatch):
    # If re-serialising the module (ast.unparse) changed behaviour, every "kill"
    # would be meaningless — the lens must refuse instead of reporting a score.
    src, cmd = _project(tmp_path, AGES, STRONG_CHECK)
    before = src.read_bytes()
    monkeypatch.setattr(runner_mod, "_null_mutant", lambda source: "raise SystemExit(1)\n")
    with pytest.raises(MutationLensError, match="unparse"):
        run_mutation_lens(src, cmd, cwd=tmp_path)
    assert src.read_bytes() == before


def test_same_size_mutants_are_not_masked_by_stale_bytecode(tmp_path):
    # `1 + 2` -> `1 - 2` has the same byte length as the null mutant and is written
    # within the same second; a stale .pyc would silently run the ORIGINAL code and
    # the mutant would falsely "survive".
    module = "def f():\n    return 1 + 2\n"
    check = "from calc import f\nassert f() == 3\n"
    src, cmd = _project(tmp_path, module, check, name="calc")
    report = run_mutation_lens(src, cmd, cwd=tmp_path)
    assert report.confidence.survived == 0, report.confidence.survivors
    assert not (tmp_path / "__pycache__").exists()


def test_hanging_mutant_counts_as_killed(tmp_path):
    # Add -> Sub turns the loop infinite; the suite would hang, which CI detects.
    module = "def count():\n    n = 0\n    while n < 3:\n        n = n + 1\n    return n\n"
    check = "from loop import count\nassert count() == 3\n"
    src, cmd = _project(tmp_path, module, check, name="loop")
    report = run_mutation_lens(src, cmd, cwd=tmp_path, timeout=3.0)
    hung = [o for o in report.outcomes if o.timed_out]
    assert hung, "expected the Add -> Sub mutant to time out"
    assert all(o.killed for o in hung)


# ---------------------------------------------------------------------------
# Source safety
# ---------------------------------------------------------------------------

def test_original_restored_byte_for_byte(tmp_path):
    module = "# keep this comment\r\ndef is_adult(age):  \r\n    return age >= 18\r\n"
    src, cmd = _project(tmp_path, module, STRONG_CHECK)
    before = src.read_bytes()
    run_mutation_lens(src, cmd, cwd=tmp_path)
    assert src.read_bytes() == before


def test_original_restored_when_run_crashes_midway(tmp_path, monkeypatch):
    src, cmd = _project(tmp_path, AGES, STRONG_CHECK)
    before = src.read_bytes()
    real_run = runner_mod._run_tests
    calls = {"n": 0}

    def crashing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:  # baseline, null mutant, then the first real mutant
            raise KeyboardInterrupt
        return real_run(*args, **kwargs)

    monkeypatch.setattr(runner_mod, "_run_tests", crashing)
    with pytest.raises(KeyboardInterrupt):
        run_mutation_lens(src, cmd, cwd=tmp_path)
    assert src.read_bytes() == before


def test_report_serialises(tmp_path):
    src, cmd = _project(tmp_path, AGES, WEAK_CHECK)
    d = run_mutation_lens(src, cmd, cwd=tmp_path).to_dict()
    assert d["confidence"]["total"] == 3
    assert len(d["outcomes"]) == 3
    assert d["function"] is None
    assert {"index", "operator", "description", "lineno", "killed", "timed_out"} <= set(d["outcomes"][0])
