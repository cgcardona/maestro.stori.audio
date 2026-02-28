"""Muse CLI — Typer application root.

Entry point for the ``muse`` console script. Registers all MVP
subcommands (arrange, ask, checkout, commit, context, describe, divergence,
dynamics, export, find, grep, import, init, log, merge, meter, open, play,
pull, push, recall, remote, session, status, swing, tag, tempo,
write_tree) as Typer sub-applications.
"""
from __future__ import annotations

import typer

from maestro.muse_cli.commands import (
    arrange,
    ask,
    checkout,
    commit,
    context,
    describe,
    divergence,
    dynamics,
    export,
    find,
    grep_cmd,
    import_cmd,
    init,
    log,
    merge,
    meter,
    open_cmd,
    play,
    pull,
    push,
    recall,
    remote,
    session,
    status,
    swing,
    tag,
    tempo,
    write_tree,
)

cli = typer.Typer(
    name="muse",
    help="Muse — Git-style version control for musical compositions.",
    no_args_is_help=True,
)

cli.add_typer(init.app, name="init", help="Initialise a new Muse repository.")
cli.add_typer(status.app, name="status", help="Show working-tree drift against HEAD.")
cli.add_typer(dynamics.app, name="dynamics", help="Analyse the dynamic (velocity) profile of a commit.")
cli.add_typer(commit.app, name="commit", help="Record a new variation in history.")
cli.add_typer(grep_cmd.app, name="grep", help="Search for a musical pattern across all commits.")
cli.add_typer(log.app, name="log", help="Display the variation history graph.")
cli.add_typer(find.app, name="find", help="Search commit history by musical properties.")
cli.add_typer(checkout.app, name="checkout", help="Checkout a historical variation.")
cli.add_typer(merge.app, name="merge", help="Three-way merge two variation branches.")
cli.add_typer(remote.app, name="remote", help="Manage remote server connections.")
cli.add_typer(push.app, name="push", help="Upload local variations to a remote.")
cli.add_typer(pull.app, name="pull", help="Download remote variations locally.")
cli.add_typer(describe.app, name="describe", help="Describe what changed musically in a commit.")
cli.add_typer(open_cmd.app, name="open", help="Open an artifact in the system default app (macOS).")
cli.add_typer(play.app, name="play", help="Play an audio artifact via afplay (macOS).")
cli.add_typer(arrange.app, name="arrange", help="Display arrangement map (instrument activity over sections).")
cli.add_typer(swing.app, name="swing", help="Analyze or annotate the swing factor of a composition.")
cli.add_typer(session.app, name="session", help="Record and query recording session metadata.")
cli.add_typer(export.app, name="export", help="Export a snapshot to MIDI, JSON, MusicXML, ABC, or WAV.")
cli.add_typer(ask.app, name="ask", help="Query musical history in natural language.")
cli.add_typer(meter.app, name="meter", help="Read or set the time signature of a commit.")
cli.add_typer(tag.app, name="tag", help="Attach and query music-semantic tags on commits.")
cli.add_typer(import_cmd.app, name="import", help="Import a MIDI or MusicXML file as a new Muse commit.")
cli.add_typer(tempo.app, name="tempo", help="Read or set the tempo (BPM) of a commit.")
cli.add_typer(recall.app, name="recall", help="Search commit history by natural-language description.")
cli.add_typer(context.app, name="context", help="Output structured musical context for AI agent consumption.")
cli.add_typer(divergence.app, name="divergence", help="Show how two branches have diverged musically.")
cli.add_typer(write_tree.app, name="write-tree", help="Write the current muse-work/ state as a snapshot (tree) object.")


if __name__ == "__main__":
    cli()
