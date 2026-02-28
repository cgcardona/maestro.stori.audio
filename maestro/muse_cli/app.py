"""Muse CLI — Typer application root.

Entry point for the ``muse`` console script. Registers all MVP
subcommands (amend, arrange, ask, blame, cat-object, checkout, chord-map, commit,
commit-tree, context, contour, describe, diff, divergence, dynamics, emotion-diff,
export, fetch, find, form, grep, groove-check, harmony, hash-object, humanize, import,
init, inspect, key, log, merge, meter, motif, open, play, pull, push, read-tree, rebase,
recall, release, remote, render-preview, reset, resolve, restore, rev-parse, revert, session, show,
similarity, stash, status, swing, symbolic-ref, tag, tempo, tempo-scale, timeline,
update-ref, validate, write-tree) as Typer sub-applications.
"""
from __future__ import annotations

import typer

from maestro.muse_cli.commands import (
    amend,
    arrange,
    ask,
    blame,
    cat_object,
    checkout,
    chord_map,
    commit,
    commit_tree,
    context,
    contour,
    describe,
    diff,
    divergence,
    dynamics,
    emotion_diff,
    export,
    fetch,
    find,
    form,
    grep_cmd,
    groove_check,
    harmony,
    hash_object,
    humanize,
    import_cmd,
    init,
    inspect,
    key,
    log,
    merge,
    meter,
    motif,
    open_cmd,
    play,
    pull,
    push,
    read_tree,
    rebase,
    recall,
    release,
    remote,
    render_preview,
    reset,
    resolve,
    restore,
    rev_parse,
    revert,
    session,
    show,
    similarity,
    stash,
    status,
    swing,
    symbolic_ref,
    tag,
    tempo,
    tempo_scale,
    timeline,
    update_ref,
    validate,
    write_tree,
)

cli = typer.Typer(
    name="muse",
    help="Muse — Git-style version control for musical compositions.",
    no_args_is_help=True,
)

cli.add_typer(amend.app, name="amend", help="Fold working-tree changes into the most recent commit.")
cli.add_typer(blame.app, name="blame", help="Annotate files with the commit that last changed each one.")
cli.add_typer(cat_object.app, name="cat-object", help="Read and display a stored object by its SHA-256 hash.")
cli.add_typer(hash_object.app, name="hash-object", help="Compute the SHA-256 object ID for a file (or stdin) and optionally store it.")
cli.add_typer(chord_map.app, name="chord-map", help="Visualize the chord progression embedded in a commit.")
cli.add_typer(contour.app, name="contour", help="Analyze the melodic contour and phrase shape of a commit.")
cli.add_typer(init.app, name="init", help="Initialise a new Muse repository.")
cli.add_typer(status.app, name="status", help="Show working-tree drift against HEAD.")
cli.add_typer(dynamics.app, name="dynamics", help="Analyse the dynamic (velocity) profile of a commit.")
cli.add_typer(commit.app, name="commit", help="Record a new variation in history.")
cli.add_typer(
    commit_tree.app,
    name="commit-tree",
    help="Create a raw commit object from an existing snapshot (plumbing).",
)
cli.add_typer(grep_cmd.app, name="grep", help="Search for a musical pattern across all commits.")
cli.add_typer(log.app, name="log", help="Display the variation history graph.")
cli.add_typer(find.app, name="find", help="Search commit history by musical properties.")
cli.add_typer(harmony.app, name="harmony", help="Analyze harmonic content (key, mode, chords, tension) of a commit.")
cli.add_typer(inspect.app, name="inspect", help="Print structured JSON of the Muse commit graph.")
cli.add_typer(checkout.app, name="checkout", help="Checkout a historical variation.")
cli.add_typer(merge.app, name="merge", help="Three-way merge two variation branches.")
cli.add_typer(remote.app, name="remote", help="Manage remote server connections.")
cli.add_typer(fetch.app, name="fetch", help="Fetch refs from remote without merging.")
cli.add_typer(push.app, name="push", help="Upload local variations to a remote.")
cli.add_typer(pull.app, name="pull", help="Download remote variations locally.")
cli.add_typer(describe.app, name="describe", help="Describe what changed musically in a commit.")
cli.add_typer(diff.app, name="diff", help="Compare two commits across musical dimensions (harmonic, rhythmic, melodic, structural, dynamic).")
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
cli.add_typer(read_tree.app, name="read-tree", help="Read a snapshot into muse-work/ without updating HEAD.")
cli.add_typer(rebase.app, name="rebase", help="Rebase commits onto a new base, producing a linear history.")
cli.add_typer(recall.app, name="recall", help="Search commit history by natural-language description.")
cli.add_typer(release.app, name="release", help="Export a tagged commit as distribution-ready release artifacts.")
cli.add_typer(revert.app, name="revert", help="Create a new commit that undoes a prior commit without rewriting history.")
cli.add_typer(key.app, name="key", help="Read or annotate the musical key of a commit.")
cli.add_typer(humanize.app, name="humanize", help="Apply micro-timing and velocity humanization to quantized MIDI.")
cli.add_typer(context.app, name="context", help="Output structured musical context for AI agent consumption.")
cli.add_typer(divergence.app, name="divergence", help="Show how two branches have diverged musically.")
cli.add_typer(motif.app, name="motif", help="Identify, track, and compare recurring melodic motifs.")
cli.add_typer(emotion_diff.app, name="emotion-diff", help="Compare emotion vectors between two commits.")
cli.add_typer(rev_parse.app, name="rev-parse", help="Resolve a revision expression to a commit ID.")
cli.add_typer(symbolic_ref.app, name="symbolic-ref", help="Read or write a symbolic ref (e.g. HEAD).")
cli.add_typer(show.app, name="show", help="Inspect a commit: metadata, snapshot, diff, MIDI files, and audio preview.")
cli.add_typer(render_preview.app, name="render-preview", help="Generate an audio preview of a commit's snapshot.")
cli.add_typer(reset.app, name="reset", help="Reset the branch pointer to a prior commit.")
cli.add_typer(resolve.app, name="resolve", help="Mark a conflicted file as resolved (--ours or --theirs).")
cli.add_typer(restore.app, name="restore", help="Restore specific files from a commit or index into muse-work/.")
cli.add_typer(groove_check.app, name="groove-check", help="Analyze rhythmic drift across commits to find groove regressions.")
cli.add_typer(form.app, name="form", help="Analyze or annotate the formal structure (sections) of a commit.")
cli.add_typer(similarity.app, name="similarity", help="Compare two commits by musical similarity score.")
cli.add_typer(stash.app, name="stash", help="Temporarily shelve uncommitted muse-work/ changes.")
cli.add_typer(tempo_scale.app, name="tempo-scale", help="Stretch or compress the timing of a commit.")
cli.add_typer(timeline.app, name="timeline", help="Visualize musical evolution chronologically.")
cli.add_typer(update_ref.app, name="update-ref", help="Write or delete a ref (branch or tag pointer).")
cli.add_typer(validate.app, name="validate", help="Check musical integrity of the working tree.")
cli.add_typer(write_tree.app, name="write-tree", help="Write the current muse-work/ state as a snapshot (tree) object.")


if __name__ == "__main__":
    cli()
