"""Tests for ``muse init`` — initialise a new Muse repository.

Covers every acceptance criterion from issue #31:
- Creates .muse/ with all required files
- Idempotent: exits 1 without --force when .muse/ already exists
- --force reinitialises and preserves existing repo_id
- muse status after init shows "On branch main, no commits yet"

Covers acceptance criteria from issue #85:
- --bare creates .muse/ without muse-work/ and writes bare = true in config.toml
- --template copies template directory contents into muse-work/
- --default-branch names the initial branch instead of "main"
- All flags are combinable

All filesystem operations use ``tmp_path`` + ``os.chdir`` or the
``MUSE_REPO_ROOT`` env-var override so tests are fully isolated.
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
from click.testing import Result
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_init(tmp_path: pathlib.Path, *extra_args: str) -> Result:
    """``chdir`` into *tmp_path* and invoke ``muse init``."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        return runner.invoke(cli, ["init", *extra_args])
    finally:
        os.chdir(prev)


def _run_cmd(tmp_path: pathlib.Path, *args: str) -> Result:
    """``chdir`` into *tmp_path* and invoke the CLI with *args*."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        return runner.invoke(cli, list(args))
    finally:
        os.chdir(prev)


# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------


def test_init_creates_muse_directory(tmp_path: pathlib.Path) -> None:
    """``.muse/`` directory is created after ``muse init``."""
    result = _run_init(tmp_path)
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".muse").is_dir()


def test_init_creates_refs_heads_directory(tmp_path: pathlib.Path) -> None:
    """``.muse/refs/heads/`` sub-tree is created."""
    _run_init(tmp_path)
    assert (tmp_path / ".muse" / "refs" / "heads").is_dir()


# ---------------------------------------------------------------------------
# repo.json
# ---------------------------------------------------------------------------


def test_init_writes_repo_json(tmp_path: pathlib.Path) -> None:
    """``repo.json`` contains ``repo_id`` (valid UUID), ``schema_version``, ``created_at``."""
    _run_init(tmp_path)
    repo_json_path = tmp_path / ".muse" / "repo.json"
    assert repo_json_path.exists(), "repo.json missing"

    data = json.loads(repo_json_path.read_text())
    assert "repo_id" in data
    assert "schema_version" in data
    assert "created_at" in data

    # repo_id must be a valid UUID
    parsed = uuid.UUID(data["repo_id"])
    assert str(parsed) == data["repo_id"]


def test_init_repo_json_schema_version_is_1(tmp_path: pathlib.Path) -> None:
    """``schema_version`` is ``"1"`` in the initial repo.json."""
    _run_init(tmp_path)
    data = json.loads((tmp_path / ".muse" / "repo.json").read_text())
    assert data["schema_version"] == "1"


# ---------------------------------------------------------------------------
# HEAD file
# ---------------------------------------------------------------------------


def test_init_writes_head_file(tmp_path: pathlib.Path) -> None:
    """``.muse/HEAD`` is written and points to ``refs/heads/main``."""
    _run_init(tmp_path)
    head_path = tmp_path / ".muse" / "HEAD"
    assert head_path.exists(), ".muse/HEAD missing"
    assert head_path.read_text().strip() == "refs/heads/main"


def test_init_writes_main_ref(tmp_path: pathlib.Path) -> None:
    """``.muse/refs/heads/main`` exists (empty — no commits yet)."""
    _run_init(tmp_path)
    ref_path = tmp_path / ".muse" / "refs" / "heads" / "main"
    assert ref_path.exists(), ".muse/refs/heads/main missing"
    # Empty content = no commits on this branch
    assert ref_path.read_text().strip() == ""


# ---------------------------------------------------------------------------
# config.toml
# ---------------------------------------------------------------------------


def test_init_writes_config_toml(tmp_path: pathlib.Path) -> None:
    """``config.toml`` is created with ``[user]``, ``[auth]``, ``[remotes]`` sections."""
    _run_init(tmp_path)
    config_path = tmp_path / ".muse" / "config.toml"
    assert config_path.exists(), "config.toml missing"

    content = config_path.read_text()
    assert "[user]" in content
    assert "[auth]" in content
    assert "[remotes]" in content


def test_init_config_toml_is_valid_toml(tmp_path: pathlib.Path) -> None:
    """``config.toml`` produced by ``muse init`` is valid TOML (parseable via stdlib)."""
    import tomllib  # Python 3.11+ stdlib

    _run_init(tmp_path)
    config_path = tmp_path / ".muse" / "config.toml"
    with config_path.open("rb") as fh:
        parsed = tomllib.load(fh)
    assert "user" in parsed
    assert "auth" in parsed


# ---------------------------------------------------------------------------
# Idempotency / --force behaviour
# ---------------------------------------------------------------------------


def test_init_idempotent_without_force_exits_1(tmp_path: pathlib.Path) -> None:
    """Second ``muse init`` without ``--force`` exits 1 with an informative message."""
    _run_init(tmp_path)
    result = _run_init(tmp_path)  # second call

    assert result.exit_code == int(ExitCode.USER_ERROR), result.output
    assert "Already a Muse repository" in result.output
    assert "--force" in result.output


def test_init_force_reinitialises(tmp_path: pathlib.Path) -> None:
    """``muse init --force`` succeeds even when ``.muse/`` already exists."""
    _run_init(tmp_path)
    result = _run_init(tmp_path, "--force")

    assert result.exit_code == 0, result.output
    assert "Reinitialised" in result.output


def test_init_force_preserves_repo_id(tmp_path: pathlib.Path) -> None:
    """``muse init --force`` preserves the existing ``repo_id`` from ``repo.json``."""
    _run_init(tmp_path)
    first_id = json.loads((tmp_path / ".muse" / "repo.json").read_text())["repo_id"]

    _run_init(tmp_path, "--force")
    second_id = json.loads((tmp_path / ".muse" / "repo.json").read_text())["repo_id"]

    assert first_id == second_id, "repo_id must survive --force reinitialise"


def test_init_force_does_not_overwrite_config_toml(tmp_path: pathlib.Path) -> None:
    """``muse init --force`` does NOT overwrite an existing ``config.toml``."""
    _run_init(tmp_path)
    config_path = tmp_path / ".muse" / "config.toml"
    config_path.write_text('[user]\nname = "Gabriel"\nemail = "g@example.com"\n\n[auth]\ntoken = "tok"\n\n[remotes]\n')

    _run_init(tmp_path, "--force")
    content = config_path.read_text()
    assert 'name = "Gabriel"' in content, "--force must not overwrite config.toml"


def test_init_success_output_contains_path(tmp_path: pathlib.Path) -> None:
    """Success message includes the ``.muse`` directory path."""
    result = _run_init(tmp_path)
    assert ".muse" in result.output


# ---------------------------------------------------------------------------
# muse status after muse init (acceptance criterion from issue #31)
# ---------------------------------------------------------------------------


def test_status_shows_on_branch_main_no_commits(tmp_path: pathlib.Path) -> None:
    """``muse status`` immediately after ``muse init`` shows 'On branch main, no commits yet'."""
    _run_init(tmp_path)
    result = _run_cmd(tmp_path, "status")

    assert result.exit_code == 0, result.output
    assert "On branch main" in result.output
    assert "no commits yet" in result.output


# ---------------------------------------------------------------------------
# Error handling — filesystem permission failures (regression for permission bug)
# ---------------------------------------------------------------------------


def test_init_permission_error_exits_1(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``muse init`` exits 1 with a clean message when the CWD is not writable.

    Regression test: previously a raw ``PermissionError`` traceback was shown
    (reproduced by running ``docker compose exec maestro muse init`` from
    ``/app/``, which is owned by root and not writable by the container user).
    """

    def _raise_permission(*args: object, **kwargs: object) -> None:
        raise PermissionError("[Errno 13] Permission denied: '/app/.muse'")

    monkeypatch.setattr(pathlib.Path, "mkdir", _raise_permission)

    result = _run_init(tmp_path)

    assert result.exit_code == int(ExitCode.USER_ERROR), result.output
    assert "Permission denied" in result.output
    assert "write access" in result.output
    assert "mkdir -p" in result.output
    # Must NOT produce a raw Python traceback.
    assert "Traceback" not in result.output
    assert "PermissionError" not in result.output


def test_init_oserror_exits_3(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``muse init`` exits 3 with a clean message on unexpected ``OSError``."""

    def _raise_os(*args: object, **kwargs: object) -> None:
        raise OSError("[Errno 28] No space left on device")

    monkeypatch.setattr(pathlib.Path, "mkdir", _raise_os)

    result = _run_init(tmp_path)

    assert result.exit_code == int(ExitCode.INTERNAL_ERROR), result.output
    assert "Failed to initialise" in result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# --bare flag (issue #85)
# ---------------------------------------------------------------------------


def test_bare_creates_muse_directory(tmp_path: pathlib.Path) -> None:
    """``muse init --bare`` creates a ``.muse/`` directory."""
    result = _run_init(tmp_path, "--bare")
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".muse").is_dir()


def test_bare_does_not_create_muse_work(tmp_path: pathlib.Path) -> None:
    """``muse init --bare`` must NOT create ``muse-work/``."""
    _run_init(tmp_path, "--bare")
    assert not (tmp_path / "muse-work").exists()


def test_bare_writes_bare_flag_in_repo_json(tmp_path: pathlib.Path) -> None:
    """``muse init --bare`` writes ``bare = true`` into ``repo.json``."""
    _run_init(tmp_path, "--bare")
    data = json.loads((tmp_path / ".muse" / "repo.json").read_text())
    assert data.get("bare") is True


def test_bare_writes_bare_flag_in_config_toml(tmp_path: pathlib.Path) -> None:
    """``muse init --bare`` writes ``bare = true`` into ``config.toml``."""
    _run_init(tmp_path, "--bare")
    content = (tmp_path / ".muse" / "config.toml").read_text()
    assert "bare = true" in content


def test_bare_output_mentions_bare(tmp_path: pathlib.Path) -> None:
    """Success message for ``--bare`` includes the word 'bare'."""
    result = _run_init(tmp_path, "--bare")
    assert "bare" in result.output.lower()


def test_normal_init_creates_muse_work(tmp_path: pathlib.Path) -> None:
    """A regular (non-bare) ``muse init`` creates the ``muse-work/`` directory."""
    _run_init(tmp_path)
    assert (tmp_path / "muse-work").is_dir()


def test_normal_init_repo_json_has_no_bare_flag(tmp_path: pathlib.Path) -> None:
    """A non-bare ``muse init`` does not write ``bare`` into ``repo.json``."""
    _run_init(tmp_path)
    data = json.loads((tmp_path / ".muse" / "repo.json").read_text())
    assert "bare" not in data


# ---------------------------------------------------------------------------
# --template flag (issue #85)
# ---------------------------------------------------------------------------


def test_template_copies_contents_into_muse_work(
    tmp_path: pathlib.Path,
) -> None:
    """``muse init --template <path>`` copies template contents into ``muse-work/``."""
    template_dir = tmp_path / "tmpl"
    template_dir.mkdir()
    (template_dir / "drums").mkdir()
    (template_dir / "bass").mkdir()
    (template_dir / "README.md").write_text("studio template\n")

    work_dir = tmp_path / "project"
    work_dir.mkdir()

    result = _run_init(work_dir, "--template", str(template_dir))
    assert result.exit_code == 0, result.output

    muse_work = work_dir / "muse-work"
    assert (muse_work / "drums").is_dir()
    assert (muse_work / "bass").is_dir()
    assert (muse_work / "README.md").read_text() == "studio template\n"


def test_template_nonexistent_path_exits_1(tmp_path: pathlib.Path) -> None:
    """``muse init --template`` exits 1 when the template path does not exist."""
    result = _run_init(tmp_path, "--template", str(tmp_path / "no_such_dir"))
    assert result.exit_code == int(ExitCode.USER_ERROR), result.output
    assert "does not exist" in result.output or "not a directory" in result.output


def test_template_ignored_for_bare_repos(tmp_path: pathlib.Path) -> None:
    """``muse init --bare --template`` does not create ``muse-work/`` (bare takes priority)."""
    template_dir = tmp_path / "tmpl"
    template_dir.mkdir()
    (template_dir / "keys").mkdir()

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    result = _run_init(project_dir, "--bare", "--template", str(template_dir))
    assert result.exit_code == 0, result.output
    assert not (project_dir / "muse-work").exists()


# ---------------------------------------------------------------------------
# --default-branch flag (issue #85)
# ---------------------------------------------------------------------------


def test_default_branch_sets_head_pointer(tmp_path: pathlib.Path) -> None:
    """``muse init --default-branch develop`` writes ``refs/heads/develop`` into HEAD."""
    result = _run_init(tmp_path, "--default-branch", "develop")
    assert result.exit_code == 0, result.output
    head = (tmp_path / ".muse" / "HEAD").read_text().strip()
    assert head == "refs/heads/develop"


def test_default_branch_creates_ref_file(tmp_path: pathlib.Path) -> None:
    """``muse init --default-branch release`` creates ``.muse/refs/heads/release``."""
    _run_init(tmp_path, "--default-branch", "release")
    assert (tmp_path / ".muse" / "refs" / "heads" / "release").exists()


def test_default_branch_default_is_main(tmp_path: pathlib.Path) -> None:
    """Without ``--default-branch``, HEAD points to ``refs/heads/main`` (regression guard)."""
    _run_init(tmp_path)
    head = (tmp_path / ".muse" / "HEAD").read_text().strip()
    assert head == "refs/heads/main"


def test_default_branch_combined_with_bare(tmp_path: pathlib.Path) -> None:
    """``--default-branch`` and ``--bare`` are combinable."""
    result = _run_init(tmp_path, "--bare", "--default-branch", "trunk")
    assert result.exit_code == 0, result.output
    head = (tmp_path / ".muse" / "HEAD").read_text().strip()
    assert head == "refs/heads/trunk"
    assert (tmp_path / ".muse" / "refs" / "heads" / "trunk").exists()
    assert not (tmp_path / "muse-work").exists()


def test_default_branch_combined_with_template(tmp_path: pathlib.Path) -> None:
    """``--default-branch`` and ``--template`` are combinable."""
    template_dir = tmp_path / "tmpl"
    template_dir.mkdir()
    (template_dir / "vocals").mkdir()

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    result = _run_init(
        project_dir, "--default-branch", "studio", "--template", str(template_dir)
    )
    assert result.exit_code == 0, result.output
    head = (project_dir / ".muse" / "HEAD").read_text().strip()
    assert head == "refs/heads/studio"
    assert (project_dir / "muse-work" / "vocals").is_dir()


def test_all_three_flags_combined(tmp_path: pathlib.Path) -> None:
    """``--bare``, ``--template``, and ``--default-branch`` can all be passed together.

    When --bare is used, muse-work/ is never created even if --template is given.
    """
    template_dir = tmp_path / "tmpl"
    template_dir.mkdir()
    (template_dir / "drums").mkdir()

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    result = _run_init(
        project_dir,
        "--bare",
        "--template",
        str(template_dir),
        "--default-branch",
        "develop",
    )
    assert result.exit_code == 0, result.output
    head = (project_dir / ".muse" / "HEAD").read_text().strip()
    assert head == "refs/heads/develop"
    data = json.loads((project_dir / ".muse" / "repo.json").read_text())
    assert data.get("bare") is True
    assert not (project_dir / "muse-work").exists()
