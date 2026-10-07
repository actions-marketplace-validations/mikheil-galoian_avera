"""`avera.check/v0` — the experimental evidence envelope of `avera check --json`.

A small, digest-bound record that a downstream review tool can attach to its own
claim as external evidence (first consumer: CounterProof; see issue #12). Spec:
docs/AVERA_CHECK_EVIDENCE_V0.md.

Boundaries, on purpose:

- **v0** — the shape may still change; nobody should hard-depend on it yet.
- The **digest is change detection, not authenticity**: anyone can recompute it,
  so it shows the record was not altered, not who produced it.
- ``inputs`` hashes identify the exact **JUnit artifacts** that were compared,
  not the source-code candidates that produced them. Binding to a candidate is a
  consumer concern.
- ``result`` uses AVERA's verdict semantics (docs/AVERA_VERDICT_SPECIFICATION.md);
  it is not translated into any consumer's vocabulary.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from avera import __version__

SCHEMA_VERSION = "avera.check/v0"
TOOL_NAME = "avera"
RESULT_KEYS = (
    "verdict",
    "gate_status",
    "introduced_failures",
    "risk",
    "confidence",
    "confidence_score",
)


def canonical_bytes(obj: Any) -> bytes:
    """Digest serialisation: UTF-8 JSON, recursively sorted keys, no insignificant
    whitespace. Non-finite numbers are rejected rather than emitted as invalid JSON."""
    text = json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return text.encode("utf-8")


def compute_digest(envelope: dict[str, Any]) -> str:
    """Lowercase SHA-256 hex over the canonical envelope, excluding ``digest``."""
    payload = {key: value for key, value in envelope.items() if key != "digest"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_check_envelope(
    *,
    baseline: Path,
    current: Path,
    policy: str,
    result: dict[str, Any],
    tool_version: str | None = None,
) -> dict[str, Any]:
    """Build a v0 envelope. Raises ``KeyError`` if ``result`` lacks a required key."""
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": tool_version or __version__},
        "policy": policy,
        "inputs": {
            "baseline_sha256": _sha256_file(Path(baseline)),
            "current_sha256": _sha256_file(Path(current)),
        },
        "result": {key: result[key] for key in RESULT_KEYS},
    }
    envelope["digest"] = compute_digest(envelope)
    return envelope


def verify_check_envelope(envelope: dict[str, Any]) -> bool:
    """True if this is a v0 envelope whose digest matches its content."""
    if envelope.get("schema_version") != SCHEMA_VERSION:
        return False
    digest = envelope.get("digest")
    return isinstance(digest, str) and digest == compute_digest(envelope)
