"""Guard the packaged copy of the gate policies.

`avera check` must work from a plain `pip install avera`, with no git checkout
and no environment variables. That only holds while the policy JSON files are
shipped inside the wheel AND stay identical to the canonical repository copy.

These tests fail closed on both halves of that contract:

1. every built-in policy exists in the packaged directory and parses;
2. the packaged copy is byte-identical to the repository ``policies/`` copy,
   so the two can never silently drift.
"""

from __future__ import annotations

import json

import pytest

from avera.gates.policy_loader import (
    BUILTIN_POLICIES,
    PACKAGED_POLICIES_DIR,
    POLICIES_DIR,
    load_builtin_policy,
    load_policy,
)


def test_packaged_policies_directory_exists() -> None:
    assert PACKAGED_POLICIES_DIR.is_dir(), (
        f"packaged policies directory missing: {PACKAGED_POLICIES_DIR}. "
        "Without it, `avera check` raises PolicyError outside a source checkout."
    )


@pytest.mark.parametrize("name,stem", sorted(BUILTIN_POLICIES.items()))
def test_every_builtin_policy_is_packaged_and_parses(name: str, stem: str) -> None:
    path = PACKAGED_POLICIES_DIR / f"{stem}.json"
    assert path.is_file(), f"built-in policy {name!r} is not shipped in the wheel: {path}"

    policy = load_policy(path)
    assert policy.policy_id, f"packaged policy {name!r} has an empty policy_id"


@pytest.mark.parametrize("name", sorted(BUILTIN_POLICIES))
def test_builtin_policy_loads_by_name(name: str) -> None:
    """The public entry point used by `avera check` resolves every policy."""
    assert load_builtin_policy(name).policy_id


def test_packaged_policies_match_repository_copy() -> None:
    """The wheel copy and the repository copy must not drift apart."""
    if not POLICIES_DIR.is_dir():
        pytest.skip("no source checkout: repository policies/ directory is absent")

    repo_files = {p.name: p for p in sorted(POLICIES_DIR.glob("*.json"))}
    packaged_files = {p.name: p for p in sorted(PACKAGED_POLICIES_DIR.glob("*.json"))}

    assert repo_files.keys() == packaged_files.keys(), (
        "policy file sets differ between policies/ and src/avera/gates/policies/. "
        f"only in repo: {sorted(repo_files.keys() - packaged_files.keys())}; "
        f"only in package: {sorted(packaged_files.keys() - repo_files.keys())}"
    )

    for filename, repo_path in repo_files.items():
        packaged_path = packaged_files[filename]
        assert json.loads(repo_path.read_text(encoding="utf-8")) == json.loads(
            packaged_path.read_text(encoding="utf-8")
        ), (
            f"{filename} differs between policies/ and src/avera/gates/policies/. "
            "Update both copies (the packaged one is what pip-installed users get)."
        )
