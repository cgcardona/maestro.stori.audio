"""Deterministic contract hashing for execution lineage verification.

Rules:
  - Structural fields participate in hashes.
  - Advisory / meta fields are excluded.
  - Serialization is canonical: sorted keys, no whitespace, json.dumps.
  - Hash is SHA-256, truncated to 16 hex chars (64-bit short hash).
  - No MD5, no pickle, no repr().

Excluded fields (advisory / meta / visual / runtime):
  contract_version, contract_hash, parent_contract_hash, execution_hash,
  l2_generate_prompt, region_name, gm_guidance,
  assigned_color, existing_track_id.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any


_HASH_EXCLUDED_FIELDS = frozenset({
    "contract_version",
    "contract_hash",
    "parent_contract_hash",
    "execution_hash",
    "l2_generate_prompt",
    "region_name",
    "gm_guidance",
    "assigned_color",
    "existing_track_id",
})


def _normalize_value(value: Any) -> Any:
    """Recursively normalize a value for canonical serialization."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return canonical_contract_dict(value)
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, (int, float, str, bool, type(None))):
        return value
    return str(value)


def canonical_contract_dict(obj: Any) -> dict[str, Any]:
    """Convert a frozen dataclass to a canonical ordered dict for hashing.

    Excludes advisory/meta fields defined in ``_HASH_EXCLUDED_FIELDS``.
    Recursively normalizes nested dataclasses and collections.
    Keys are sorted for deterministic serialization.

    Special case: ``CompositionContract.sections`` is serialized as a
    sorted list of section contract hashes (not full objects), keeping
    the root hash compact and order-independent.
    """
    if not dataclasses.is_dataclass(obj):
        raise TypeError(f"Expected a dataclass, got {type(obj).__name__}")

    _is_composition = type(obj).__name__ == "CompositionContract"

    result: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        if f.name in _HASH_EXCLUDED_FIELDS:
            continue
        value = getattr(obj, f.name)
        if _is_composition and f.name == "sections":
            result["sections"] = sorted(
                getattr(s, "contract_hash", "") for s in value
            )
        else:
            result[f.name] = _normalize_value(value)

    return dict(sorted(result.items()))


def compute_contract_hash(obj: Any) -> str:
    """Compute a deterministic SHA-256 short hash of structural contract fields.

    Returns the first 16 hex characters (64-bit collision resistance).
    """
    canonical = canonical_contract_dict(obj)
    serialized = json.dumps(canonical, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return digest[:16]


def seal_contract(obj: Any, parent_hash: str = "") -> None:
    """Compute and set ``contract_hash`` on a frozen dataclass.

    Uses ``object.__setattr__`` to bypass frozen enforcement.
    Optionally sets ``parent_contract_hash`` if provided.
    """
    if parent_hash:
        object.__setattr__(obj, "parent_contract_hash", parent_hash)
    h = compute_contract_hash(obj)
    object.__setattr__(obj, "contract_hash", h)


def set_parent_hash(obj: Any, parent_hash: str) -> None:
    """Set ``parent_contract_hash`` on a frozen dataclass."""
    object.__setattr__(obj, "parent_contract_hash", parent_hash)


def verify_contract_hash(obj: Any) -> bool:
    """Recompute hash and compare to the stored ``contract_hash``.

    Returns ``True`` if the stored hash matches the recomputed hash.
    """
    stored = getattr(obj, "contract_hash", "")
    if not stored:
        return False
    return compute_contract_hash(obj) == stored


def hash_list_canonical(items: list[str]) -> str:
    """Collision-proof parent hash from a list of child hashes.

    Sorts lexicographically, JSON-encodes the sorted list, then
    SHA-256 hashes the result.  Returns the first 16 hex chars.

    This replaces the old ``SHA256("hashA:hashB")`` pattern which
    was vulnerable to delimiter collisions.
    """
    serialized = json.dumps(sorted(items))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def compute_execution_hash(contract_hash: str, trace_id: str) -> str:
    """Bind an execution to a specific contract + session.

    Prevents replay attacks: same contract in a different session
    produces a different execution_hash.  Returns 16 hex chars.
    """
    payload = (contract_hash + trace_id).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
