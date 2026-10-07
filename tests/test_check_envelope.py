"""`avera.check/v0` evidence envelope — the additive, digest-bound output of
`avera check --json` that downstream review tools can attach as evidence.

Shape and digest rule agreed in mikheil-galoian/avera#12; the spec lives in
docs/AVERA_CHECK_EVIDENCE_V0.md and the frozen example in
examples/check-evidence-v0/.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

import avera
from avera.cli import run_check
from avera.evidence.check_envelope import (
    SCHEMA_VERSION,
    build_check_envelope,
    canonical_bytes,
    compute_digest,
    verify_check_envelope,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "check-evidence-v0"

RESULT = {
    "verdict": "confirmed_regression",
    "gate_status": "block",
    "introduced_failures": ["m.t_reg"],
    "risk": "high",
    "confidence": "high",
    "confidence_score": 0.95,
}


def _files(tmp_path: Path) -> tuple[Path, Path]:
    baseline = tmp_path / "baseline.xml"
    current = tmp_path / "current.xml"
    baseline.write_bytes(b"<testsuite/>")
    current.write_bytes(b"<testsuite><testcase/></testsuite>")
    return baseline, current


def _envelope(tmp_path: Path, **overrides) -> dict:
    baseline, current = _files(tmp_path)
    kwargs = {"baseline": baseline, "current": current, "policy": "general", "result": RESULT}
    kwargs.update(overrides)
    return build_check_envelope(**kwargs)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

def test_envelope_has_exactly_the_agreed_v0_shape(tmp_path):
    env = _envelope(tmp_path)
    assert set(env) == {"schema_version", "tool", "policy", "inputs", "result", "digest"}
    assert env["schema_version"] == SCHEMA_VERSION == "avera.check/v0"
    assert env["tool"] == {"name": "avera", "version": avera.__version__}
    assert env["policy"] == "general"
    assert set(env["inputs"]) == {"baseline_sha256", "current_sha256"}
    assert env["result"] == RESULT


def test_result_carries_only_the_agreed_keys(tmp_path):
    env = _envelope(tmp_path, result={**RESULT, "report_only": True, "extra": 1})
    assert set(env["result"]) == set(RESULT)


def test_missing_result_key_fails_closed(tmp_path):
    incomplete = {k: v for k, v in RESULT.items() if k != "gate_status"}
    with pytest.raises(KeyError):
        _envelope(tmp_path, result=incomplete)


# ---------------------------------------------------------------------------
# Input hashes bind the exact JUnit bytes
# ---------------------------------------------------------------------------

def test_input_hashes_are_sha256_of_the_file_bytes(tmp_path):
    env = _envelope(tmp_path)
    assert env["inputs"]["baseline_sha256"] == hashlib.sha256(b"<testsuite/>").hexdigest()
    assert env["inputs"]["current_sha256"] == hashlib.sha256(
        b"<testsuite><testcase/></testsuite>"
    ).hexdigest()


def test_any_byte_change_in_an_input_changes_hash_and_digest(tmp_path):
    first = _envelope(tmp_path)
    (tmp_path / "current.xml").write_bytes(b"<testsuite><testcase/></testsuite>\n")
    baseline, current = tmp_path / "baseline.xml", tmp_path / "current.xml"
    second = build_check_envelope(baseline=baseline, current=current, policy="general", result=RESULT)
    assert second["inputs"]["current_sha256"] != first["inputs"]["current_sha256"]
    assert second["digest"] != first["digest"]


# ---------------------------------------------------------------------------
# Digest rule (explicit, per #12)
# ---------------------------------------------------------------------------

def test_canonical_bytes_rule():
    obj = {"b": 1, "a": {"d": [3, "é"], "c": None}}
    assert canonical_bytes(obj) == '{"a":{"c":null,"d":[3,"é"]},"b":1}'.encode()


def test_canonical_bytes_rejects_nan():
    with pytest.raises(ValueError):
        canonical_bytes({"x": float("nan")})


def test_digest_excludes_the_digest_field_and_is_lowercase_sha256_hex(tmp_path):
    env = _envelope(tmp_path)
    payload = {k: v for k, v in env.items() if k != "digest"}
    assert env["digest"] == hashlib.sha256(canonical_bytes(payload)).hexdigest()
    assert len(env["digest"]) == 64 and env["digest"] == env["digest"].lower()
    assert compute_digest(env) == env["digest"]  # an existing digest field is ignored


def test_digest_is_deterministic_and_key_order_independent(tmp_path):
    env = _envelope(tmp_path)
    reordered = {k: env[k] for k in reversed(list(env))}
    assert compute_digest(reordered) == env["digest"]
    assert _envelope(tmp_path)["digest"] == env["digest"]


def test_verify_accepts_untouched_and_rejects_any_change(tmp_path):
    env = _envelope(tmp_path)
    assert verify_check_envelope(env) is True

    for mutate in (
        lambda e: e["result"].__setitem__("gate_status", "pass"),
        lambda e: e["result"]["introduced_failures"].append("m.other"),
        lambda e: e["inputs"].__setitem__("baseline_sha256", "0" * 64),
        lambda e: e["tool"].__setitem__("version", "9.9.9"),
        lambda e: e.__setitem__("policy", "aviation"),
    ):
        tampered = copy.deepcopy(env)
        mutate(tampered)
        assert verify_check_envelope(tampered) is False


def test_verify_rejects_unknown_schema_and_missing_digest(tmp_path):
    env = _envelope(tmp_path)
    other = {**env, "schema_version": "avera.check/v1"}
    other["digest"] = compute_digest(other)
    assert verify_check_envelope(other) is False
    assert verify_check_envelope({k: v for k, v in env.items() if k != "digest"}) is False


# ---------------------------------------------------------------------------
# CLI: additive `evidence` key on `avera check --json`
# ---------------------------------------------------------------------------

_SUITE = '<testsuite name="s"><testcase classname="m" name="t_ok"/>{extra}</testsuite>'


def _junit(tmp_path: Path, name: str, failing: bool) -> Path:
    extra = (
        '<testcase classname="m" name="t_reg"><failure message="boom"/></testcase>'
        if failing
        else '<testcase classname="m" name="t_reg"/>'
    )
    path = tmp_path / name
    path.write_text(_SUITE.format(extra=extra), encoding="utf-8")
    return path


def test_check_json_keeps_old_keys_and_adds_verifiable_evidence(tmp_path, capsys):
    baseline = _junit(tmp_path, "base.xml", failing=False)
    current = _junit(tmp_path, "curr.xml", failing=True)
    run_check(baseline, current, "general", as_json=True)
    out = json.loads(capsys.readouterr().out)

    for key in ("verdict", "risk", "confidence", "confidence_score",
                "introduced_failures", "gate_status", "policy", "report_only"):
        assert key in out

    env = out["evidence"]
    assert verify_check_envelope(env)
    assert env["policy"] == out["policy"]
    for key in env["result"]:
        assert env["result"][key] == out[key]
    assert env["inputs"]["baseline_sha256"] == hashlib.sha256(baseline.read_bytes()).hexdigest()
    assert env["inputs"]["current_sha256"] == hashlib.sha256(current.read_bytes()).hexdigest()


def test_report_only_does_not_change_the_evidence(tmp_path, capsys):
    baseline = _junit(tmp_path, "base.xml", failing=False)
    current = _junit(tmp_path, "curr.xml", failing=True)
    run_check(baseline, current, "general", as_json=True)
    gated = json.loads(capsys.readouterr().out)["evidence"]
    run_check(baseline, current, "general", as_json=True, report_only=True)
    advisory = json.loads(capsys.readouterr().out)["evidence"]
    assert advisory == gated


# ---------------------------------------------------------------------------
# Frozen v0 example (golden)
# ---------------------------------------------------------------------------

def test_frozen_example_verifies():
    frozen = json.loads((EXAMPLE / "envelope.json").read_text(encoding="utf-8"))
    assert frozen["schema_version"] == "avera.check/v0"
    assert verify_check_envelope(frozen)


def test_frozen_example_is_reproduced_byte_for_byte(capsys):
    frozen_text = (EXAMPLE / "envelope.json").read_text(encoding="utf-8")
    frozen = json.loads(frozen_text)

    # The CLI takes the policy *name*; the envelope records the versioned policy id.
    run_check(EXAMPLE / "baseline.xml", EXAMPLE / "current.xml", "general", as_json=True)
    live = json.loads(capsys.readouterr().out)["evidence"]

    # Rebuild under the version that produced the example, so a version bump does
    # not break the golden while any change to shape, inputs, pipeline result or
    # digest rule does.
    rebuilt = build_check_envelope(
        baseline=EXAMPLE / "baseline.xml",
        current=EXAMPLE / "current.xml",
        policy=live["policy"],
        result=live["result"],
        tool_version=frozen["tool"]["version"],
    )
    assert rebuilt == frozen
    assert json.dumps(rebuilt, indent=2, ensure_ascii=False) + "\n" == frozen_text


# ---------------------------------------------------------------------------
# Version guard (the envelope reports tool.version)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is 3.11+")
def test_package_version_matches_pyproject():
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert avera.__version__ == pyproject["project"]["version"]
