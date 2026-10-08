# `avera.check/v0` — evidence envelope (experimental)

`avera check --json` emits, next to its existing keys, an `evidence` object: a
small, digest-bound record a downstream review tool can attach to its own claim
as external evidence. Designed in the open with the first consumer,
[CounterProof](https://github.com/hippoley/CounterProof) — see
[#12](https://github.com/mikheil-galoian/avera/issues/12) and
[hippoley/CounterProof#81](https://github.com/hippoley/CounterProof/issues/81).

**Status: v0, experimental.** The shape may change. Do not hard-depend on it.

## Example

Frozen example, produced by the tool from the inputs next to it:
[`examples/check-evidence-v0/`](../examples/check-evidence-v0/)
(`baseline.xml`, `current.xml`, `envelope.json`). A test rebuilds it byte-for-byte,
so it cannot drift silently.

```bash
avera check --baseline examples/check-evidence-v0/baseline.xml \
            --current  examples/check-evidence-v0/current.xml --json | jq .evidence
```

```json
{
  "schema_version": "avera.check/v0",
  "tool": { "name": "avera", "version": "0.2.0" },
  "policy": "general.v1",
  "inputs": {
    "baseline_sha256": "8b5d3e64a68b29d9dffaaf6c7ab5466b127849087228759de9152be1aa9eba0c",
    "current_sha256":  "9436c5784dcecf69b449a73abda08829f0b06d4005b2e9efdd25f8dd1aae3e51"
  },
  "result": {
    "verdict": "confirmed_regression",
    "gate_status": "block",
    "introduced_failures": ["tests.test_pricing.test_discount_applies_at_threshold"],
    "risk": "medium",
    "confidence": "medium",
    "confidence_score": 0.66
  },
  "digest": "3220e9f4f8016c97784aec3fdb8d72f715cc5988a70784d249980f4eae70560d"
}
```

## Fields

| Field | Meaning |
|---|---|
| `schema_version` | Always `"avera.check/v0"` for this version. Consumers gate on it. |
| `tool.name`, `tool.version` | Producer and its version. |
| `policy` | Versioned id of the gate policy that was applied (e.g. `general.v1`). |
| `inputs.baseline_sha256`, `inputs.current_sha256` | SHA-256 (lowercase hex) of the exact bytes of the two JUnit files compared. |
| `result.verdict`, `result.gate_status`, `result.introduced_failures`, `result.risk`, `result.confidence`, `result.confidence_score` | AVERA's result, with AVERA's meaning — see [the verdict specification](AVERA_VERDICT_SPECIFICATION.md). |
| `digest` | SHA-256 over the rest of the envelope (rule below). |

`--report-only` changes only the exit code, never the evidence.

## Digest rule

1. Take the envelope **without** the `digest` key.
2. Serialise it as **UTF-8 JSON** with **object keys sorted recursively**, **no
   insignificant whitespace** (`,` and `:` separators), non-ASCII emitted as
   UTF-8 (not `\u` escapes), and **no NaN/Infinity**.
3. `digest` = **SHA-256** of those bytes, **lowercase hex** (64 characters).

Reference implementation (Python):

```python
import hashlib, json

def avera_check_v0_digest(envelope: dict) -> str:
    payload = {k: v for k, v in envelope.items() if k != "digest"}
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

### Numbers across languages

Numbers follow Python's `json` formatting. Python and JavaScript render some
floats differently (`1.0` vs `1`, `0.0`/`-0.0` vs `0`, `1e-07` vs `1e-7`), so for
those values a verifier that serialises numbers natively in another language
gets a different digest. This is a **documented v0 boundary, not a protocol
defect**; it was first exercised by CounterProof's independent Node verifier
([hippoley/CounterProof#168](https://github.com/hippoley/CounterProof/pull/168)).

**Producer guarantee (current AVERA):** the only number in the envelope is
`result.confidence_score`. It is rounded to 2 decimals and, on every branch of
the confidence pipeline, lies strictly between 0 and 1 (in practice 0.15–0.95),
so it is never integral and never written with an exponent. For every such
value Python and JavaScript produce the same text, and therefore the same
digest. A test enumerates the pipeline and fails if that ever stops being true
(`tests/test_check_envelope_numeric.py`).

**Conformance vectors** — the frozen example with `result.confidence_score`
replaced by the value shown. "AVERA digest" is the reference implementation
above; "JS-native digest" is what a verifier gets by recursively sorting keys
and using `JSON.stringify` for numbers.

| `confidence_score` | Python text | JS text | AVERA digest | JS-native digest |
|---|---|---|---|---|
| `0.66` | `0.66` | `0.66` | `3220e9f4f8016c97784aec3fdb8d72f715cc5988a70784d249980f4eae70560d` | same |
| `1.0` | `1.0` | `1` | `2bb6d912adabae22fee427b10b83e4ca119b59bc2fcaac6cec183764ddae669b` | `d539813284e7f7a9c0933495b7b7e97ce248c9bc95ac821b9d4e7f21da1001d0` |
| `0.0` | `0.0` | `0` | `ae313af9314433a7fe9fb758edd4bb134bded628a8fcb398357ec95e5dba310d` | `5c69e9053544eb06d25788cc3bb9c94925daa9c484491bc20b98a011f9462138` |
| `-0.0` | `-0.0` | `0` | `c2b36dd87cbc24dacfd4f21284b9b56fc281fcf0257935762f80f029eae9380a` | `5c69e9053544eb06d25788cc3bb9c94925daa9c484491bc20b98a011f9462138` |
| `1e-07` | `1e-07` | `1e-7` | `c335f3add2ce314bc57e6248041d0bf03133deba76e86702e7b0b94563f2e09b` | `c70a67cc4302d515fd19c7e37e7d819706cc883ea2e8b88047247d9bd487c50f` |

The last four values are not produced by AVERA today; they are adversarial
vectors for verifiers. If this ever becomes a stable cross-language contract,
[RFC 8785 (JCS)](https://www.rfc-editor.org/rfc/rfc8785) is the intended
migration target, triggered by a real second-language consumer rather than in
advance.

## What the envelope does and does not claim

- **The digest is change detection, not authenticity.** Anyone can recompute
  it. It shows the record was not altered after being produced, not who
  produced it.
- **The input hashes identify the JUnit artifacts, not the source code.** They
  prove *which test reports* were compared, not *which code candidates*
  produced those reports. Binding a result to a candidate (commit, dirty
  worktree, snapshot) is the consumer's job; if AVERA ever carries it, it will
  be an additive, producer-defined field, not "git SHA only".
- **AVERA's verdict semantics stay AVERA's.** A consumer attaches the envelope
  as provenance to its own claim and evaluates that claim with its own
  semantics; it should not translate AVERA's verdict into its own vocabulary.
- **No consumer-specific fields.** The envelope is the same for every consumer.
