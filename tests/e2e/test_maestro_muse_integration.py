"""Integration tests for the Maestro stress test → muse-work/ output contract.

These tests exercise the artifact-saving and manifest-emission functions from
``scripts/e2e/stress_test.py`` in isolation — no live Storpheus or Maestro
service is required.

All async tests use ``@pytest.mark.anyio``.
"""
from __future__ import annotations

import base64
import json
import pathlib
import sys
from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# stress_test.py lives in scripts/e2e/ which is not a package; import it via
# sys.path manipulation so it's available to the test suite without modifying
# production code.
_SCRIPTS_E2E = pathlib.Path(__file__).parents[2] / "scripts" / "e2e"
if str(_SCRIPTS_E2E) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_E2E))

from stress_test import (  # noqa: E402
    ArtifactSet,
    MuseBatchFile,
    RequestResult,
    emit_muse_batch_json,
    save_artifacts_to_muse_work,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    genre: str = "jazz",
    bars: int = 4,
    instruments: list[str] | None = None,
    success: bool = True,
    cache_hit: bool = False,
    composition_id: str = "comp-0001",
) -> RequestResult:
    return RequestResult(
        genre=genre,
        tempo=110,
        instruments=instruments or ["drums", "bass"],
        bars=bars,
        quality_preset="fast",
        intent_profile="neutral",
        key=None,
        success=success,
        composition_id=composition_id,
        cache_hit=cache_hit,
    )


def _make_artifact(
    composition_id: str = "comp-0001",
    genre: str = "jazz",
    bars: int = 4,
    with_mid: bool = True,
    with_mp3: bool = True,
    with_webp: bool = True,
) -> ArtifactSet:
    art = ArtifactSet(composition_id=composition_id, genre=genre, bars=bars)
    if with_mid:
        art.mid_b64 = base64.b64encode(b"MIDI-DATA").decode()
    if with_mp3:
        art.mp3_b64 = base64.b64encode(b"MP3-DATA").decode()
    if with_webp:
        art.webp_b64 = base64.b64encode(b"WEBP-DATA").decode()
    return art


# ---------------------------------------------------------------------------
# test_stress_test_writes_muse_work_layout
# ---------------------------------------------------------------------------


def test_stress_test_writes_muse_work_layout(tmp_path: pathlib.Path) -> None:
    """Files appear in correct subdirectories after save_artifacts_to_muse_work."""
    output_dir = tmp_path / "muse-work"
    result = _make_result(
        genre="jazz", bars=4, instruments=["drums", "bass"], composition_id="comp-abc"
    )
    artifact = _make_artifact(composition_id="comp-abc", genre="jazz", bars=4)
    artifacts = {"comp-abc": artifact}

    batch_files = save_artifacts_to_muse_work(output_dir, [result], artifacts)

    # MIDI → tracks/<instruments>/
    mid_files = list((output_dir / "tracks").rglob("*.mid"))
    assert len(mid_files) == 1, "Exactly one MIDI file should be written"
    assert "drums_bass" in str(mid_files[0])
    assert "jazz_4b_comp-abc.mid" == mid_files[0].name

    # MP3 → renders/
    mp3_files = list((output_dir / "renders").rglob("*.mp3"))
    assert len(mp3_files) == 1
    assert "jazz_4b_comp-abc.mp3" == mp3_files[0].name

    # WebP → previews/
    webp_files = list((output_dir / "previews").rglob("*.webp"))
    assert len(webp_files) == 1
    assert "jazz_4b_comp-abc.webp" == webp_files[0].name

    # Meta JSON → meta/
    meta_files = list((output_dir / "meta").rglob("*.json"))
    assert len(meta_files) == 1
    assert "jazz_4b_comp-abc.json" == meta_files[0].name

    # Verify batch_files roles
    roles = {f.role for f in batch_files}
    assert roles == {"midi", "mp3", "webp", "meta"}


# ---------------------------------------------------------------------------
# test_muse_batch_json_schema
# ---------------------------------------------------------------------------


def test_muse_batch_json_schema(tmp_path: pathlib.Path) -> None:
    """muse-batch.json is valid JSON matching the required schema."""
    output_dir = tmp_path / "muse-work"
    result = _make_result(composition_id="comp-0001", genre="house", bars=8)
    artifact = _make_artifact(composition_id="comp-0001", genre="house", bars=8)
    artifacts = {"comp-0001": artifact}

    batch_files = save_artifacts_to_muse_work(output_dir, [result], artifacts)

    provenance: dict[str, Any] = {
        "prompt": "stress_test.py --quick --genre house",
        "model": "storpheus",
        "seed": "stress-20260227_172919",
        "storpheus_version": "1.0.0",
    }
    batch_path = emit_muse_batch_json(
        batch_root=tmp_path,
        run_id="stress-20260227_172919",
        generated_at="2026-02-27T17:29:19Z",
        batch_files=batch_files,
        results=[result],
        provenance=provenance,
    )

    assert batch_path.exists(), "muse-batch.json must be written"
    data = json.loads(batch_path.read_text())

    # Required top-level keys
    assert "run_id" in data
    assert "generated_at" in data
    assert "commit_message_suggestion" in data
    assert "files" in data
    assert "provenance" in data

    assert data["run_id"] == "stress-20260227_172919"
    assert data["generated_at"] == "2026-02-27T17:29:19Z"
    assert isinstance(data["commit_message_suggestion"], str)
    assert len(data["commit_message_suggestion"]) > 0

    # Each file entry must have required fields
    for entry in data["files"]:
        assert "path" in entry
        assert "role" in entry
        assert "genre" in entry
        assert "bars" in entry
        assert entry["role"] in ("midi", "mp3", "webp", "meta")

    # Paths must be relative (no leading /)
    for entry in data["files"]:
        assert not entry["path"].startswith("/"), "paths must be relative to repo root"
        assert entry["path"].startswith("muse-work/")

    # Provenance fields
    prov = data["provenance"]
    assert "prompt" in prov
    assert "model" in prov
    assert "seed" in prov
    assert "storpheus_version" in prov


# ---------------------------------------------------------------------------
# test_muse_batch_includes_only_successes
# ---------------------------------------------------------------------------


def test_muse_batch_includes_only_successes(tmp_path: pathlib.Path) -> None:
    """Failed results are absent from the files[] array in muse-batch.json."""
    output_dir = tmp_path / "muse-work"

    success_result = _make_result(
        genre="jazz", composition_id="comp-ok", success=True
    )
    failed_result = _make_result(
        genre="house", composition_id="comp-fail", success=False
    )
    failed_result.error = "GPU timeout"

    artifacts = {
        "comp-ok": _make_artifact(composition_id="comp-ok"),
        # No artifact for comp-fail (failed generation)
    }

    batch_files = save_artifacts_to_muse_work(
        output_dir, [success_result, failed_result], artifacts
    )

    batch_path = emit_muse_batch_json(
        batch_root=tmp_path,
        run_id="stress-test",
        generated_at="2026-02-27T00:00:00Z",
        batch_files=batch_files,
        results=[success_result, failed_result],
        provenance={},
    )

    data = json.loads(batch_path.read_text())

    # Only the successful jazz result should appear
    genres_in_batch = {e["genre"] for e in data["files"]}
    assert "jazz" in genres_in_batch
    assert "house" not in genres_in_batch, "Failed result must be omitted from batch"

    # Verify no comp-fail paths
    paths_in_batch = [e["path"] for e in data["files"]]
    assert not any("comp-fail" in p for p in paths_in_batch)


# ---------------------------------------------------------------------------
# test_muse_batch_cache_hits_have_cached_flag
# ---------------------------------------------------------------------------


def test_muse_batch_cache_hits_have_cached_flag(tmp_path: pathlib.Path) -> None:
    """Cache-hit results are included in muse-batch.json with cached=True."""
    output_dir = tmp_path / "muse-work"

    cached_result = _make_result(
        genre="boom_bap", composition_id="comp-cached", success=True, cache_hit=True
    )
    fresh_result = _make_result(
        genre="techno", composition_id="comp-fresh", success=True, cache_hit=False
    )
    artifacts = {
        "comp-cached": _make_artifact(
            composition_id="comp-cached", genre="boom_bap", with_mp3=False, with_webp=False
        ),
        "comp-fresh": _make_artifact(
            composition_id="comp-fresh", genre="techno", with_mp3=False, with_webp=False
        ),
    }

    batch_files = save_artifacts_to_muse_work(
        output_dir, [cached_result, fresh_result], artifacts
    )

    batch_path = emit_muse_batch_json(
        batch_root=tmp_path,
        run_id="stress-cache-test",
        generated_at="2026-02-27T00:00:00Z",
        batch_files=batch_files,
        results=[cached_result, fresh_result],
        provenance={},
    )

    data = json.loads(batch_path.read_text())

    cached_entries = [e for e in data["files"] if "comp-cached" in e["path"]]
    fresh_entries = [e for e in data["files"] if "comp-fresh" in e["path"]]

    assert len(cached_entries) > 0, "Cache hit must appear in batch"
    assert all(e["cached"] is True for e in cached_entries)

    assert len(fresh_entries) > 0, "Fresh result must appear in batch"
    assert all(e["cached"] is False for e in fresh_entries)


# ---------------------------------------------------------------------------
# test_muse_batch_commit_message_suggestion_multi_genre
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "genres, expected_prefix",
    [
        (["jazz"], "feat: jazz stress test"),
        (["jazz", "house"], "feat: 2-genre stress test"),
        (["jazz", "house", "techno"], "feat: 3-genre stress test"),
    ],
)
def test_muse_batch_commit_message_suggestion(
    tmp_path: pathlib.Path,
    genres: list[str],
    expected_prefix: str,
) -> None:
    """commit_message_suggestion reflects the number and names of genres."""
    output_dir = tmp_path / "muse-work"
    results = []
    artifacts: dict[str, ArtifactSet] = {}

    for i, genre in enumerate(genres):
        comp_id = f"comp-{i:04d}"
        r = _make_result(genre=genre, composition_id=comp_id, success=True)
        results.append(r)
        artifacts[comp_id] = _make_artifact(
            composition_id=comp_id, genre=genre, with_mp3=False, with_webp=False
        )

    batch_files = save_artifacts_to_muse_work(output_dir, results, artifacts)
    batch_path = emit_muse_batch_json(
        batch_root=tmp_path,
        run_id="stress-msg-test",
        generated_at="2026-02-27T00:00:00Z",
        batch_files=batch_files,
        results=results,
        provenance={},
    )

    data = json.loads(batch_path.read_text())
    suggestion = data["commit_message_suggestion"]
    assert suggestion.startswith(expected_prefix), (
        f"Expected suggestion to start with {expected_prefix!r}, got {suggestion!r}"
    )


# ---------------------------------------------------------------------------
# test_muse_batch_no_artifacts_uses_genres_from_results
# ---------------------------------------------------------------------------


def test_muse_batch_no_artifacts_uses_genres_from_results(
    tmp_path: pathlib.Path,
) -> None:
    """When no artifacts are available, commit_message_suggestion uses successful genres."""
    output_dir = tmp_path / "muse-work"
    result = _make_result(genre="ambient", composition_id="comp-no-art", success=True)

    # No artifacts → no files written → empty batch_files
    batch_files = save_artifacts_to_muse_work(output_dir, [result], {})

    batch_path = emit_muse_batch_json(
        batch_root=tmp_path,
        run_id="stress-no-art",
        generated_at="2026-02-27T00:00:00Z",
        batch_files=batch_files,
        results=[result],
        provenance={},
    )

    data = json.loads(batch_path.read_text())
    suggestion = data["commit_message_suggestion"]
    # Should fall back to successful genres from results
    assert "ambient" in suggestion
