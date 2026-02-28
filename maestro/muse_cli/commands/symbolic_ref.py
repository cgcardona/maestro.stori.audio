"""muse symbolic-ref — read or write a symbolic ref in the Muse repository.

A symbolic ref is a file whose contents point to another ref.  The canonical
example is ``.muse/HEAD``, which contains ``refs/heads/main`` when on the main
branch and a bare 40-char SHA when in detached HEAD state.

Usage
-----
::

    muse symbolic-ref HEAD                          # read: prints refs/heads/main
    muse symbolic-ref --short HEAD                  # read short: prints main
    muse symbolic-ref HEAD refs/heads/feature/x    # write new target
    muse symbolic-ref --delete HEAD                 # delete (detached HEAD scenarios)
    -q / --quiet suppresses error output when the ref is not symbolic.

Design notes
------------
- Pure filesystem operations — no DB session needed.
- The ``name`` argument is resolved relative to ``.muse/``, so callers pass
  bare names like ``HEAD`` or ``refs/heads/main``.
- ``--delete`` removes the file entirely. Callers that rely on HEAD being
  absent after deletion must handle ``FileNotFoundError`` themselves.
- Mirrors the contract of ``git symbolic-ref``.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Optional

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer()

_SYMBOLIC_REF_PREFIX = "refs/"


# ---------------------------------------------------------------------------
# Pure logic — testable without Typer
# ---------------------------------------------------------------------------


class SymbolicRefResult:
    """Structured result of a symbolic-ref read operation.

    Attributes
    ----------
    ref:
        The full target ref string stored in the file, e.g. ``refs/heads/main``.
    short:
        The short form — the last component of *ref* after the final ``/``.
        For ``refs/heads/main`` this is ``main``.
    name:
        The symbolic ref name that was queried, e.g. ``HEAD``.
    """

    __slots__ = ("ref", "short", "name")

    def __init__(self, *, name: str, ref: str) -> None:
        self.name = name
        self.ref = ref
        self.short = ref.rsplit("/", 1)[-1] if "/" in ref else ref


def read_symbolic_ref(
    muse_dir: pathlib.Path,
    name: str,
    *,
    quiet: bool = False,
) -> SymbolicRefResult | None:
    """Read a symbolic ref from ``muse_dir``.

    Returns a :class:`SymbolicRefResult` when the file exists and its content
    starts with ``refs/``.  Returns ``None`` when:
    - The file does not exist.
    - The content does not start with ``refs/`` (detached HEAD / bare SHA).

    When *quiet* is ``False`` and the ref is not symbolic, a warning is written
    via the module logger.  Callers may also echo to the user themselves.

    Args:
        muse_dir: Path to the ``.muse/`` directory.
        name: Ref name, e.g. ``HEAD`` or ``refs/heads/main``.
        quiet: Suppress warning log when the ref is not symbolic.

    Returns:
        :class:`SymbolicRefResult` or ``None``.
    """
    ref_path = muse_dir / name
    if not ref_path.exists():
        if not quiet:
            logger.warning("⚠️ symbolic-ref: %s not found in %s", name, muse_dir)
        return None

    content = ref_path.read_text().strip()
    if not content.startswith(_SYMBOLIC_REF_PREFIX):
        if not quiet:
            logger.warning(
                "⚠️ symbolic-ref: %s is not a symbolic ref (content: %r)", name, content
            )
        return None

    return SymbolicRefResult(name=name, ref=content)


def write_symbolic_ref(
    muse_dir: pathlib.Path,
    name: str,
    target: str,
) -> None:
    """Write *target* into the symbolic ref *name* inside *muse_dir*.

    Creates any intermediate directories needed so that writing
    ``refs/heads/feature/guitar`` works without a prior ``mkdir``.

    Args:
        muse_dir: Path to the ``.muse/`` directory.
        name: Ref name, e.g. ``HEAD`` or ``refs/heads/new-branch``.
        target: Full ref target, e.g. ``refs/heads/feature/guitar``.

    Raises:
        ValueError: If *target* does not start with ``refs/``.
    """
    if not target.startswith(_SYMBOLIC_REF_PREFIX):
        raise ValueError(
            f"Invalid symbolic-ref target {target!r}: must start with 'refs/'"
        )
    ref_path = muse_dir / name
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(target + "\n")
    logger.info("✅ symbolic-ref: wrote %r → %r", name, target)


def delete_symbolic_ref(
    muse_dir: pathlib.Path,
    name: str,
    *,
    quiet: bool = False,
) -> bool:
    """Delete the symbolic ref file *name* from *muse_dir*.

    Args:
        muse_dir: Path to the ``.muse/`` directory.
        name: Ref name to delete, e.g. ``HEAD``.
        quiet: When ``True``, suppress the warning log if the file is absent.

    Returns:
        ``True`` if the file was deleted, ``False`` if it was already absent.
    """
    ref_path = muse_dir / name
    if not ref_path.exists():
        if not quiet:
            logger.warning("⚠️ symbolic-ref: cannot delete %s — file not found", name)
        return False
    ref_path.unlink()
    logger.info("✅ symbolic-ref: deleted %r", name)
    return True


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def symbolic_ref(
    ctx: typer.Context,
    name: str = typer.Argument(
        ...,
        metavar="<name>",
        help="Symbolic ref name, e.g. HEAD or refs/heads/main.",
    ),
    new_target: Optional[str] = typer.Argument(
        None,
        metavar="<ref>",
        help=(
            "When supplied, write this target into the symbolic ref. "
            "Must start with 'refs/'."
        ),
    ),
    short: bool = typer.Option(
        False,
        "--short",
        help="Print just the branch name instead of the full ref path.",
    ),
    delete: bool = typer.Option(
        False,
        "--delete",
        "-d",
        help="Delete the symbolic ref file entirely.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress error output when the ref is not symbolic.",
    ),
) -> None:
    """Read or write a symbolic ref in the Muse repository.

    Read: ``muse symbolic-ref HEAD`` prints ``refs/heads/main``.
    Write: ``muse symbolic-ref HEAD refs/heads/feature/x`` updates .muse/HEAD.
    Short: ``muse symbolic-ref --short HEAD`` prints ``main``.
    Delete: ``muse symbolic-ref --delete HEAD`` removes the file.
    """
    root = require_repo()
    muse_dir = root / ".muse"

    if delete:
        deleted = delete_symbolic_ref(muse_dir, name, quiet=quiet)
        if not deleted:
            if not quiet:
                typer.echo(f"❌ {name}: not found — nothing to delete")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        typer.echo(f"✅ Deleted symbolic ref {name!r}")
        return

    if new_target is not None:
        if not new_target.startswith(_SYMBOLIC_REF_PREFIX):
            typer.echo(
                f"❌ Invalid symbolic-ref target {new_target!r}: must start with 'refs/'"
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)
        try:
            write_symbolic_ref(muse_dir, name, new_target)
        except ValueError as exc:
            typer.echo(f"❌ {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        except Exception as exc:
            typer.echo(f"❌ muse symbolic-ref write failed: {exc}")
            logger.error("❌ muse symbolic-ref write error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        typer.echo(f"✅ {name} → {new_target}")
        return

    # Read path
    result = read_symbolic_ref(muse_dir, name, quiet=quiet)
    if result is None:
        if not quiet:
            typer.echo(f"❌ {name} is not a symbolic ref or does not exist")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    typer.echo(result.short if short else result.ref)
