"""Numeric boundary of the `avera.check/v0` digest.

Python's `json` and JavaScript's `JSON.stringify` render some numbers
differently (`1.0` vs `1`, `0.0`/`-0.0` vs `0`, `1e-07` vs `1e-7`), so a v0
digest only reproduces across languages for numbers both render identically.
Found by CounterProof's independent Node verifier (hippoley/CounterProof#168).

v0 is deliberately unchanged. Instead these tests pin two things:

- the producer never emits an affected value: the only number in the envelope,
  `result.confidence_score`, is a 2-decimal value strictly between 0 and 1 on
  every branch of the confidence pipeline;
- the conformance vectors published in docs/AVERA_CHECK_EVIDENCE_V0.md are
  exactly what the reference implementation computes.
"""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path

import pytest

from avera.classify import verdicts
from avera.classify.confidence import HIGH, LOW, MEDIUM, score_confidence
from avera.classify.risk_classifier import _confidence_factors
from avera.evidence.check_envelope import compute_digest

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "examples" / "check-evidence-v0" / "envelope.json"
SPEC = ROOT / "docs" / "AVERA_CHECK_EVIDENCE_V0.md"

LABELS = (LOW, MEDIUM, HIGH)


def _cross_language_safe(value: float) -> bool:
    """True if Python and JavaScript render this float identically: a finite,
    non-integral value written in plain decimal (no exponent)."""
    text = json.dumps(value)
    return value != int(value) and "e" not in text.lower()


def test_cross_language_check_flags_the_known_divergent_values():
    for value in (0.66, 0.15, 0.95):
        assert _cross_language_safe(value)
    for value in (1.0, 0.0, -0.0, 1e-07):
        assert not _cross_language_safe(value)


def test_every_pipeline_branch_yields_a_cross_language_safe_score():
    present = ([], ["x"])
    scores = set()
    for verdict, label, intro, pre, reqs, files, env, metrics in itertools.product(
        verdicts.ALL_VERDICTS, LABELS, present, present, present, present, present, (True, False)
    ):
        # threshold_evidence only ever adds a "+" factor; the upper bound is
        # covered separately by test_no_verdict_can_reach_a_score_of_one.
        factors = _confidence_factors(verdict, intro, pre, [], reqs, files, env, metrics)
        score = score_confidence(label, verdict=verdict, factors=factors)
        assert 0 < score < 1, (verdict, label, factors, score)
        assert round(score, 2) == score, score
        assert _cross_language_safe(score), score
        scores.add(score)
    assert len(scores) > 10  # the enumeration really exercised the space


@pytest.mark.parametrize("verdict", [*verdicts.ALL_VERDICTS, "unknown_verdict"])
def test_no_verdict_can_reach_a_score_of_one(verdict):
    score = score_confidence(HIGH, verdict=verdict, factors=["+ x"] * 100)
    assert score < 1
    assert _cross_language_safe(score), score


# (value, Python canonical text, digest of the frozen example with that score).
# A list, not a dict: -0.0 == 0.0 in Python, so as dict keys they would collide.
VECTORS = [
    (0.66, "0.66", "3220e9f4f8016c97784aec3fdb8d72f715cc5988a70784d249980f4eae70560d"),
    (1.0, "1.0", "2bb6d912adabae22fee427b10b83e4ca119b59bc2fcaac6cec183764ddae669b"),
    (0.0, "0.0", "ae313af9314433a7fe9fb758edd4bb134bded628a8fcb398357ec95e5dba310d"),
    (-0.0, "-0.0", "c2b36dd87cbc24dacfd4f21284b9b56fc281fcf0257935762f80f029eae9380a"),
    (1e-07, "1e-07", "c335f3add2ce314bc57e6248041d0bf03133deba76e86702e7b0b94563f2e09b"),
]


def test_vectors_are_distinct():
    assert len({digest for _, _, digest in VECTORS}) == len(VECTORS)


@pytest.mark.parametrize(("value", "text", "digest"), VECTORS, ids=[t for _, t, _ in VECTORS])
def test_conformance_vector_is_exact_and_published(value, text, digest):
    envelope = copy.deepcopy(json.loads(FROZEN.read_text(encoding="utf-8")))
    envelope["result"]["confidence_score"] = value
    assert json.dumps(value) == text
    assert compute_digest(envelope) == digest
    assert digest in SPEC.read_text(encoding="utf-8")


def test_frozen_example_score_is_the_cross_language_vector():
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    value, _, digest = VECTORS[0]
    assert frozen["result"]["confidence_score"] == value
    assert frozen["digest"] == digest
