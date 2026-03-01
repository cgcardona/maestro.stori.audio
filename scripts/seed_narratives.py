"""MuseHub narrative scenario seed script.

Creates 5 interconnected stories that make demo data feel alive:

  1. Bach Remix War — marcus forks gabriel/neo-baroque, 808 bass PR rejected
     with 15-comment traditionalist vs modernist debate, consolation trap release.

  2. Chopin+Coltrane — yuki + fatou 3-way merge conflict, 20-comment resolution
     debate, joint authorship merge commit, jazz edition release.

  3. Ragtime EDM Collab — 3-participant session (marcus, fatou, aaliya), 8
     commits, fatou's polyrhythm commit gets fire reactions, "Maple Leaf Drops"
     release.

  4. Community Chaos — "community-jam" repo with 5 simultaneous open PRs, 25-
     comment key signature debate, 3 conflict PRs, 70 % milestone completion.

  5. Goldberg Milestone — gabriel's 30-variation project, 28/30 done, Variation
     25 debate (18 comments), Variation 29 PR with yuki requesting ornamentation.

Run inside the container (after seed_musehub.py):
  docker compose exec maestro python3 /app/scripts/seed_narratives.py

Idempotent: checks for the sentinel repo ID before inserting.
Pass --force to wipe narrative data and re-insert.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from maestro.config import settings
from maestro.db.musehub_models import (
    MusehubBranch,
    MusehubComment,
    MusehubCommit,
    MusehubFork,
    MusehubIssue,
    MusehubIssueComment,
    MusehubMilestone,
    MusehubPRComment,
    MusehubPRReview,
    MusehubProfile,
    MusehubPullRequest,
    MusehubReaction,
    MusehubRelease,
    MusehubRepo,
    MusehubSession,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _now(days: int = 0, hours: int = 0) -> datetime:
    return datetime.now(tz=UTC) - timedelta(days=days, hours=hours)


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _uid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.md5(seed.encode()).digest()))


# ---------------------------------------------------------------------------
# Stable IDs for narrative repos (never conflict with seed_musehub IDs)
# ---------------------------------------------------------------------------

# Scenario 1 — Bach Remix War
REPO_NEO_BAROQUE = "narr-neo-baroque-00001"
REPO_NEO_BAROQUE_FORK = "narr-neo-baroque-fork1"

# Scenario 2 — Chopin+Coltrane
REPO_NOCTURNE = "narr-nocturne-op9-00001"

# Scenario 3 — Ragtime EDM Collab
REPO_RAGTIME_EDM = "narr-ragtime-edm-00001"

# Scenario 4 — Community Chaos
REPO_COMMUNITY_JAM = "narr-community-jam-001"

# Scenario 5 — Goldberg Milestone
REPO_GOLDBERG = "narr-goldberg-var-00001"

# Sentinel: if this repo exists we consider the narratives already seeded.
SENTINEL_REPO_ID = REPO_NEO_BAROQUE

# User stable IDs (match seed_musehub.py)
GABRIEL = "user-gabriel-001"
MARCUS = "user-marcus-003"
YUKI = "user-yuki-004"
FATOU = "user-fatou-007"
PIERRE = "user-pierre-008"
SOFIA = "user-sofia-002"
AALIYA = "user-aaliya-005"
CHEN = "user-chen-006"


# ---------------------------------------------------------------------------
# Scenario 1 — Bach Remix War
# ---------------------------------------------------------------------------

async def _seed_bach_remix_war(db: AsyncSession) -> None:
    """Bach Remix War: marcus forks gabriel/neo-baroque, proposes an 808 bass
    line, gets rejected after 15 comments between purists and modernists, then
    releases the fork as 'v1.0.0-trap'.

    Story arc: gabriel creates neo-baroque in strict counterpoint style.
    marcus adds an 808 sub-bass and opens a PR.  gabriel, pierre, and chen
    defend the original; marcus, fatou, and aaliya champion the fusion.
    PR is closed as "not a fit for this project." marcus releases his fork.
    """
    # Repos
    db.add(MusehubRepo(
        repo_id=REPO_NEO_BAROQUE,
        name="Neo-Baroque Counterpoint",
        owner="gabriel",
        slug="neo-baroque-counterpoint",
        owner_user_id=GABRIEL,
        visibility="public",
        description="Strict counterpoint in the style of J.S. Bach — no anachronisms.",
        tags=["baroque", "counterpoint", "Bach", "harpsichord", "strict"],
        key_signature="D minor",
        tempo_bpm=72,
        created_at=_now(days=60),
    ))
    db.add(MusehubRepo(
        repo_id=REPO_NEO_BAROQUE_FORK,
        name="Neo-Baroque Counterpoint",
        owner="marcus",
        slug="neo-baroque-counterpoint",
        owner_user_id=MARCUS,
        visibility="public",
        description="Fork of gabriel/neo-baroque-counterpoint — trap-baroque fusion experiment.",
        tags=["baroque", "trap", "808", "fusion", "fork"],
        key_signature="D minor",
        tempo_bpm=72,
        created_at=_now(days=30),
    ))

    # Fork record
    db.add(MusehubFork(
        fork_id=_uid("narr-fork-neo-baroque-marcus"),
        source_repo_id=REPO_NEO_BAROQUE,
        fork_repo_id=REPO_NEO_BAROQUE_FORK,
        forked_by="marcus",
        created_at=_now(days=30),
    ))

    # Commits on original repo
    orig_commits: list[dict[str, Any]] = [
        dict(message="init: D minor counterpoint skeleton at 72 BPM", author="gabriel", days=60),
        dict(message="feat(soprano): Bach-style cantus firmus — whole notes", author="gabriel", days=58),
        dict(message="feat(alto): first species counterpoint against cantus", author="gabriel", days=56),
        dict(message="feat(tenor): second species — half-note counterpoint", author="gabriel", days=54),
        dict(message="feat(bass): third species — quarter-note passing motion", author="gabriel", days=52),
        dict(message="refactor(harmony): correct parallel-fifth error in bar 6", author="gabriel", days=50),
        dict(message="feat(harpsichord): continuo realisation — figured bass", author="gabriel", days=48),
    ]
    prev_id: str | None = None
    for i, c in enumerate(orig_commits):
        cid = _sha(f"narr-neo-baroque-orig-{i}")
        db.add(MusehubCommit(
            commit_id=cid,
            repo_id=REPO_NEO_BAROQUE,
            branch="main",
            parent_ids=[prev_id] if prev_id else [],
            message=c["message"],
            author=c["author"],
            timestamp=_now(days=c["days"]),
            snapshot_id=_sha(f"snap-narr-neo-baroque-{i}"),
        ))
        prev_id = cid
    db.add(MusehubBranch(
        repo_id=REPO_NEO_BAROQUE,
        name="main",
        head_commit_id=_sha("narr-neo-baroque-orig-6"),
    ))

    # Commits on fork (marcus adds trap elements)
    fork_commits: list[dict[str, Any]] = [
        dict(message="init: fork from gabriel/neo-baroque-counterpoint", author="marcus", days=30),
        dict(message="feat(808): sub-bass 808 kick on beats 1 and 3", author="marcus", days=28),
        dict(message="feat(hihat): hi-hat rolls between counterpoint lines", author="marcus", days=27),
        dict(message="feat(808): pitched 808 bass following cantus firmus notes", author="marcus", days=26),
        dict(message="refactor(808): tune 808 to D2 — matches bass line root", author="marcus", days=25),
    ]
    fork_prev: str | None = None
    for i, c in enumerate(fork_commits):
        cid = _sha(f"narr-neo-baroque-fork-{i}")
        db.add(MusehubCommit(
            commit_id=cid,
            repo_id=REPO_NEO_BAROQUE_FORK,
            branch="main",
            parent_ids=[fork_prev] if fork_prev else [],
            message=c["message"],
            author=c["author"],
            timestamp=_now(days=c["days"]),
            snapshot_id=_sha(f"snap-narr-neo-baroque-fork-{i}"),
        ))
        fork_prev = cid
    db.add(MusehubBranch(
        repo_id=REPO_NEO_BAROQUE_FORK,
        name="main",
        head_commit_id=_sha("narr-neo-baroque-fork-4"),
    ))
    db.add(MusehubBranch(
        repo_id=REPO_NEO_BAROQUE_FORK,
        name="feat/808-bass-layer",
        head_commit_id=_sha("narr-neo-baroque-fork-4"),
    ))

    # The contested PR — closed after 15-comment debate
    pr_id = _uid("narr-pr-neo-baroque-808")
    db.add(MusehubPullRequest(
        pr_id=pr_id,
        repo_id=REPO_NEO_BAROQUE,
        title="Feat: 808 sub-bass layer — trap baroque fusion",
        body=(
            "## Summary\n\nAdds a Roland TR-808 sub-bass layer beneath the counterpoint.\n\n"
            "The 808 follows the cantus firmus pitch contour but occupies the sub-register "
            "(20-80 Hz), creating physical impact without obscuring the counterpoint.\n\n"
            "## Why\n\nBaroque structures work beautifully under modern production — "
            "the formal rigour of counterpoint gives trap beats a melodic backbone they lack.\n\n"
            "## Test\n- [ ] Counterpoint lines still audible at -3 dB monitor level\n"
            "- [ ] No parallel 5ths introduced by 808 root motion"
        ),
        state="closed",
        from_branch="feat/808-bass-layer",
        to_branch="main",
        author="marcus",
        created_at=_now(days=24),
    ))

    # 15-comment debate on the PR
    pr_comment_thread: list[tuple[str, str]] = [
        ("gabriel", "Marcus, this is interesting technically, but the 808 completely undermines the contrapuntal texture. Bach's counterpoint is meant to be heard as independent voices — the sub-bass collapses everything into a single bass layer."),
        ("marcus",  "That's a fair point on voice independence, but listeners who'd never touch harpsichord music are discovering Bach through this. Isn't expanded reach worth a small compromise?"),
        ("pierre",  "I have to side with Gabriel. Counterpoint is a living tradition, not a museum piece — but 808 trap beats are a fundamentally different aesthetic. The two don't coexist musically."),
        ("fatou",   "Disagree. West African djembe tradition has always layered percussion over melodic lines. The idea that sub-bass destroys voice independence is a purely Western conservatory assumption."),
        ("chen",    "The spectral argument is valid — 808s around 50 Hz produce intermodulation products that muddy mid-range clarity. You'd need a steep high-pass on the counterpoint lines to compensate."),
        ("aaliya",  "Afrobeat composers have been layering bass-heavy production over complex polyphony for decades. This isn't a compromise — it's a new genre."),
        ("gabriel", "I hear you, Fatou and Aaliya, and I respect those traditions. But the *intent* of this repo is strict counterpoint study. If Marcus releases this as a fork-project, I'll follow it. Just not here."),
        ("marcus",  "What if I bring the 808 down 12 dB and pitch it an octave below the bass line? It becomes sub-perceptual texture rather than a competing voice."),
        ("pierre",  "Sub-perceptual by definition doesn't contribute musically. Why add it at all?"),
        ("fatou",   "Because it's *felt*, not heard. That's the whole point of 808 production — physical resonance."),
        ("yuki",    "From a granular synthesis perspective, the transient of an 808 kick is a broadband impulse that *does* interfere with upper partials. Chen's intermod point is technically accurate."),
        ("marcus",  "Yuki, what if we high-pass the counterpoint tracks at 80 Hz? Each voice stays clean in its register."),
        ("chen",    "An 80 Hz high-pass on a harpsichord loses the warmth of the lower-register strings. The instrument's character lives in 60-200 Hz."),
        ("aaliya",  "I think the real disagreement is curatorial, not acoustic. Gabriel has a vision for this repo and the 808 doesn't fit it. The fork is the right call. Marcus should ship v1.0.0-trap from his fork."),
        ("gabriel", "Aaliya said it better than I could. Closing this PR — not because the idea is bad, but because it belongs in a different project. Marcus, please ship that fork. I'll be the first to star it."),
    ]
    parent: str | None = None
    for i, (author, body) in enumerate(pr_comment_thread):
        cid = _uid(f"narr-pr-comment-bach-{i}")
        db.add(MusehubPRComment(
            comment_id=cid,
            pr_id=pr_id,
            repo_id=REPO_NEO_BAROQUE,
            author=author,
            body=body,
            target_type="general",
            parent_comment_id=parent if i > 0 else None,
            created_at=_now(days=24 - i),
        ))
        parent = cid if i == 0 else parent  # thread from first comment

    # Consolation release on the fork
    db.add(MusehubRelease(
        repo_id=REPO_NEO_BAROQUE_FORK,
        tag="v1.0.0-trap",
        title="Trap Baroque — v1.0.0",
        body=(
            "## v1.0.0-trap — Trap Baroque Fusion\n\n"
            "The PR may have been closed, but the music lives here.\n\n"
            "### What's in this release\n"
            "- D minor counterpoint (Bach-style, 4 voices)\n"
            "- 808 sub-bass following cantus firmus contour\n"
            "- Hi-hat rolls between phrase endings\n"
            "- High-pass at 80 Hz on harpsichord tracks\n\n"
            "### Philosophy\n"
            "Bach wrote for the instruments of his time. He'd have used 808s.\n\n"
            "Thanks gabriel for the counterpoint foundation and for keeping it civil."
        ),
        commit_id=_sha("narr-neo-baroque-fork-4"),
        download_urls={
            "midi_bundle": f"/releases/{REPO_NEO_BAROQUE_FORK}-v1.0.0-trap.zip",
            "mp3": f"/releases/{REPO_NEO_BAROQUE_FORK}-v1.0.0-trap.mp3",
        },
        author="marcus",
        created_at=_now(days=22),
    ))

    print("  ✅ Scenario 1: Bach Remix War — 15-comment PR debate, trap release")


# ---------------------------------------------------------------------------
# Scenario 2 — Chopin+Coltrane (3-way merge)
# ---------------------------------------------------------------------------

async def _seed_chopin_coltrane(db: AsyncSession) -> None:
    """Chopin+Coltrane: pierre owns Nocturne Op.9 No.2 repo. yuki adds jazz
    harmony, fatou adds Afro-Cuban rhythmic reinterpretation. A 3-way merge
    conflict arises — yuki's chord voicings clash with fatou's rhythm track.
    20-comment resolution debate. Joint authorship merge commit. Jazz release.
    """
    db.add(MusehubRepo(
        repo_id=REPO_NOCTURNE,
        name="Nocturne Op.9 No.2",
        owner="pierre",
        slug="nocturne-op9-no2",
        owner_user_id=PIERRE,
        visibility="public",
        description="Chopin's Nocturne Op.9 No.2 — reimagined as a jazz fusion triptych.",
        tags=["Chopin", "Coltrane", "nocturne", "jazz", "fusion", "piano"],
        key_signature="Eb major",
        tempo_bpm=66,
        created_at=_now(days=55),
    ))

    # Original commits by pierre
    nocturne_base: list[dict[str, Any]] = [
        dict(message="init: Eb major nocturne skeleton — cantilena melody", author="pierre", days=55),
        dict(message="feat(piano): ornamental triplets in RH — bars 1-4", author="pierre", days=53),
        dict(message="feat(piano): LH arpeggiated accompaniment — 6/4 feel", author="pierre", days=51),
        dict(message="feat(cello): added cello doubling melody in lower 8va", author="pierre", days=49),
    ]
    prev: str | None = None
    for i, c in enumerate(nocturne_base):
        cid = _sha(f"narr-nocturne-main-{i}")
        db.add(MusehubCommit(
            commit_id=cid,
            repo_id=REPO_NOCTURNE,
            branch="main",
            parent_ids=[prev] if prev else [],
            message=c["message"],
            author=c["author"],
            timestamp=_now(days=c["days"]),
            snapshot_id=_sha(f"snap-narr-nocturne-{i}"),
        ))
        prev = cid
    base_commit = _sha("narr-nocturne-main-3")

    # yuki's jazz harmony branch
    yuki_commits: list[dict[str, Any]] = [
        dict(message="feat(harmony): Coltrane substitutions — ii-V-I into Eb7#11", author="yuki", days=45),
        dict(message="feat(piano): quartal voicings over Chopin melody — McCoy Tyner style", author="yuki", days=44),
        dict(message="feat(harmony): tritone sub on bar 4 turnaround — A7 → Eb7", author="yuki", days=43),
    ]
    yuki_prev = base_commit
    for i, c in enumerate(yuki_commits):
        cid = _sha(f"narr-nocturne-yuki-{i}")
        db.add(MusehubCommit(
            commit_id=cid,
            repo_id=REPO_NOCTURNE,
            branch="feat/coltrane-harmony",
            parent_ids=[yuki_prev],
            message=c["message"],
            author=c["author"],
            timestamp=_now(days=c["days"]),
            snapshot_id=_sha(f"snap-narr-nocturne-yuki-{i}"),
        ))
        yuki_prev = cid
    yuki_head = _sha("narr-nocturne-yuki-2")

    # fatou's rhythm branch
    fatou_commits: list[dict[str, Any]] = [
        dict(message="feat(percussion): Afro-Cuban clave pattern under nocturne", author="fatou", days=45),
        dict(message="feat(bass): Fender bass line — Afrobeat bass register", author="fatou", days=44),
        dict(message="feat(percussion): bata drum call-and-response in bridge", author="fatou", days=43),
    ]
    fatou_prev = base_commit
    for i, c in enumerate(fatou_commits):
        cid = _sha(f"narr-nocturne-fatou-{i}")
        db.add(MusehubCommit(
            commit_id=cid,
            repo_id=REPO_NOCTURNE,
            branch="feat/afro-rhythm",
            parent_ids=[fatou_prev],
            message=c["message"],
            author=c["author"],
            timestamp=_now(days=c["days"]),
            snapshot_id=_sha(f"snap-narr-nocturne-fatou-{i}"),
        ))
        fatou_prev = cid
    fatou_head = _sha("narr-nocturne-fatou-2")

    # Branches
    db.add(MusehubBranch(repo_id=REPO_NOCTURNE, name="main", head_commit_id=base_commit))
    db.add(MusehubBranch(repo_id=REPO_NOCTURNE, name="feat/coltrane-harmony", head_commit_id=yuki_head))
    db.add(MusehubBranch(repo_id=REPO_NOCTURNE, name="feat/afro-rhythm", head_commit_id=fatou_head))

    # PRs — both open, conflict in piano.mid
    pr_yuki_id = _uid("narr-pr-nocturne-yuki")
    pr_fatou_id = _uid("narr-pr-nocturne-fatou")

    db.add(MusehubPullRequest(
        pr_id=pr_yuki_id,
        repo_id=REPO_NOCTURNE,
        title="Feat: Coltrane jazz harmony layer — quartal voicings + subs",
        body=(
            "Layers Coltrane-inspired reharmonisation beneath Chopin's cantilena.\n\n"
            "Key changes:\n"
            "- Piano voicings → quartal stacks (McCoy Tyner style)\n"
            "- Tritone sub on bar 4 turnaround\n"
            "- ii-V-I substitution into Eb7#11\n\n"
            "⚠️ Conflict with feat/afro-rhythm on `tracks/piano.mid` — "
            "both branches modified the piano track. Needs resolution discussion."
        ),
        state="merged",
        from_branch="feat/coltrane-harmony",
        to_branch="main",
        merge_commit_id=_sha("narr-nocturne-merge"),
        merged_at=_now(days=38),
        author="yuki",
        created_at=_now(days=42),
    ))

    db.add(MusehubPullRequest(
        pr_id=pr_fatou_id,
        repo_id=REPO_NOCTURNE,
        title="Feat: Afro-Cuban rhythmic reinterpretation — clave + bata",
        body=(
            "Adds Afro-Cuban percussion and bass under the nocturne.\n\n"
            "Key changes:\n"
            "- Clave pattern (3-2 son clave) under Chopin melody\n"
            "- Fender bass groove following harmonic rhythm\n"
            "- Bata drum call-and-response in bridge section\n\n"
            "⚠️ Conflict with feat/coltrane-harmony on `tracks/piano.mid` — "
            "we both touch the piano accompaniment register."
        ),
        state="merged",
        from_branch="feat/afro-rhythm",
        to_branch="main",
        merge_commit_id=_sha("narr-nocturne-merge"),
        merged_at=_now(days=38),
        author="fatou",
        created_at=_now(days=42),
    ))

    # Merge commit (joint authorship)
    db.add(MusehubCommit(
        commit_id=_sha("narr-nocturne-merge"),
        repo_id=REPO_NOCTURNE,
        branch="main",
        parent_ids=[yuki_head, fatou_head],
        message=(
            "merge: resolve piano.mid conflict — quartal voicings + clave coexist\n\n"
            "Co-authored-by: yuki <yuki@stori.music>\n"
            "Co-authored-by: fatou <fatou@stori.music>\n\n"
            "Resolution: yuki's quartal voicings moved to octave above middle C, "
            "fatou's bass register kept below C3. No spectral overlap."
        ),
        author="pierre",
        timestamp=_now(days=38),
        snapshot_id=_sha("snap-narr-nocturne-merge"),
    ))

    # 20-comment resolution debate (on yuki's PR as the primary thread)
    resolution_debate: list[tuple[str, str]] = [
        ("pierre",  "Both PRs touch `tracks/piano.mid`. We have a conflict. Let's figure out the musical resolution before forcing a merge."),
        ("yuki",    "My quartal voicings sit in the mid-register piano — bar 2, beats 1-3. Fatou, where exactly does your bass line land?"),
        ("fatou",   "Fender bass is C2-E3. The clave lives on a separate track. The conflict is actually just the piano LH accompaniment — I moved the arpeggios to staccato comping."),
        ("yuki",    "Staccato comping could work. But if you changed the LH *rhythm*, my tritone sub on bar 4 might clash harmonically — I'm expecting the 6/4 swing from pierre's original."),
        ("pierre",  "Fatou, can you share the specific beats you're hitting on the piano comping?"),
        ("fatou",   "Beats 1, 2.5, 3.5 — anticipating the clave pattern. Think of it as the piano agreeing with the clave rather than fighting it."),
        ("yuki",    "That's actually beautiful. My Coltrane sub lands on beat 4 — we don't collide at all if your comping leaves beat 4 open."),
        ("fatou",   "I can leave beat 4 open. That gives your tritone sub space to breathe."),
        ("pierre",  "I'm starting to hear this. The piano is playing two roles at once — jazz harmony AND Afro-Cuban accompaniment. That's the whole concept of the piece."),
        ("aaliya",  "Following this thread and I love where it's going. Chopin's nocturne melody stays pure, but underneath it's having a conversation between two entirely different rhythmic traditions."),
        ("yuki",    "Pierre, are you comfortable with us both modifying your LH part? I want to make sure the cantilena stays yours."),
        ("pierre",  "The melody is the nocturne. The accompaniment can transform. Bach's continuo players improvised freely — this is the same spirit."),
        ("fatou",   "That's a beautiful way to frame it. OK — I'll rebase onto yuki's branch, adjust my piano comping to leave beat 4 open, and we resolve the conflict manually."),
        ("yuki",    "Perfect. I'll pull fatou's rhythm track as-is and just make sure my voicings don't occupy the bass register below C3."),
        ("pierre",  "This is what open-source composition should look like. Three people, two traditions, one piece of music."),
        ("sofia",   "I've been watching this. The resolution you've found is genuinely innovative — quartal jazz harmony + clave + Chopin cantilena. That's a new genre."),
        ("yuki",    "sofia — we're calling it 'Nocturn-é' for now. A nocturne that refuses to stay in one century."),
        ("fatou",   "I added a comment to the merge commit explaining the register split. pierre, can you do the final merge? Joint authorship commit."),
        ("pierre",  "Done. Merged with joint authorship. Both of you are credited in the commit message. Release coming."),
        ("aaliya",  "🔥 This is the most interesting thing on MuseHub right now."),
    ]
    pr_parent: str | None = None
    for i, comment in enumerate(resolution_debate):
        author, body = comment
        cid = _uid(f"narr-pr-comment-nocturne-{i}")
        db.add(MusehubPRComment(
            comment_id=cid,
            pr_id=pr_yuki_id,
            repo_id=REPO_NOCTURNE,
            author=author,
            body=body,
            target_type="general",
            parent_comment_id=None,
            created_at=_now(days=42 - i // 2),
        ))

    # Release
    db.add(MusehubRelease(
        repo_id=REPO_NOCTURNE,
        tag="v1.0.0",
        title="Nocturne Op.9 No.2 (Jazz Edition)",
        body=(
            "## Nocturne Op.9 No.2 (Jazz Edition) — v1.0.0\n\n"
            "Chopin meets Coltrane, mediated by Afro-Cuban rhythm.\n\n"
            "### Musicians\n"
            "- **pierre** — Chopin melody (cantilena), piano LH framework\n"
            "- **yuki** — Jazz harmony (Coltrane substitutions, quartal voicings)\n"
            "- **fatou** — Afro-Cuban rhythm (clave, bata, Fender bass)\n\n"
            "### Technical resolution\n"
            "3-way merge conflict resolved by register split: "
            "yuki's voicings above C4, fatou's bass below C3, beat 4 left open "
            "for the tritone substitution.\n\n"
            "### Downloads\nMIDI bundle, MP3 stereo mix, stems by instrument family"
        ),
        commit_id=_sha("narr-nocturne-merge"),
        download_urls={
            "midi_bundle": f"/releases/{REPO_NOCTURNE}-v1.0.0.zip",
            "mp3": f"/releases/{REPO_NOCTURNE}-v1.0.0.mp3",
            "stems": f"/releases/{REPO_NOCTURNE}-v1.0.0-stems.zip",
        },
        author="pierre",
        created_at=_now(days=37),
    ))

    print("  ✅ Scenario 2: Chopin+Coltrane — 20-comment 3-way merge resolution, jazz release")


# ---------------------------------------------------------------------------
# Scenario 3 — Ragtime EDM Collab
# ---------------------------------------------------------------------------

async def _seed_ragtime_edm(db: AsyncSession) -> None:
    """Ragtime EDM Collab: marcus, fatou, aaliya co-author a ragtime-EDM fusion.
    8 commits across 3 participants. fatou's polyrhythm commit gets 🔥👏😢 reactions.
    'Maple Leaf Drops' release.
    """
    db.add(MusehubRepo(
        repo_id=REPO_RAGTIME_EDM,
        name="Maple Leaf Drops",
        owner="marcus",
        slug="maple-leaf-drops",
        owner_user_id=MARCUS,
        visibility="public",
        description="Joplin meets Berghain. Ragtime piano structures over 4/4 techno pulse.",
        tags=["ragtime", "EDM", "Joplin", "techno", "fusion", "piano"],
        key_signature="Ab major",
        tempo_bpm=130,
        created_at=_now(days=40),
    ))

    # 8 commits, 3 authors
    ragtime_commits: list[dict[str, Any]] = [
        dict(message="init: Maple Leaf Rag skeleton at 130 BPM — piano only", author="marcus", days=40),
        dict(message="feat(synth): techno bass pulse — four-on-the-floor under ragtime", author="marcus", days=38),
        dict(message="feat(drums): 909 kick + clap replacing ragtime march snare", author="aaliya", days=36),
        dict(message="feat(piano): Joplin B section — 4-bar strain over synth bass", author="marcus", days=34),
        dict(message="feat(perc): West African polyrhythm layer — fatou's surprise", author="fatou", days=32),
        dict(message="feat(synth): acid 303 line weaving through ragtime changes", author="aaliya", days=30),
        dict(message="refactor(drums): sidechained kick — duck the piano on beat 1", author="marcus", days=28),
        dict(message="feat(breakdown): 8-bar ragtime-only breakdown before drop", author="fatou", days=26),
    ]
    prev_cid: str | None = None
    for i, c in enumerate(ragtime_commits):
        cid = _sha(f"narr-ragtime-{i}")
        db.add(MusehubCommit(
            commit_id=cid,
            repo_id=REPO_RAGTIME_EDM,
            branch="main",
            parent_ids=[prev_cid] if prev_cid else [],
            message=c["message"],
            author=c["author"],
            timestamp=_now(days=c["days"]),
            snapshot_id=_sha(f"snap-narr-ragtime-{i}"),
        ))
        prev_cid = cid

    db.add(MusehubBranch(
        repo_id=REPO_RAGTIME_EDM,
        name="main",
        head_commit_id=_sha("narr-ragtime-7"),
    ))

    # Session — 3-participant collab
    db.add(MusehubSession(
        session_id=_uid("narr-session-ragtime-1"),
        repo_id=REPO_RAGTIME_EDM,
        started_at=_now(days=40),
        ended_at=_now(days=40, hours=-4),
        participants=["marcus", "fatou", "aaliya"],
        location="Electric Lady Studios (remote async)",
        intent="Ragtime EDM fusion — lay down the structural skeleton",
        commits=[_sha("narr-ragtime-0"), _sha("narr-ragtime-1")],
        notes="Decided on 130 BPM — fast enough for floor, slow enough for ragtime syncopation.",
        is_active=False,
        created_at=_now(days=40),
    ))
    db.add(MusehubSession(
        session_id=_uid("narr-session-ragtime-2"),
        repo_id=REPO_RAGTIME_EDM,
        started_at=_now(days=32),
        ended_at=_now(days=32, hours=-5),
        participants=["marcus", "fatou", "aaliya"],
        location="Remote",
        intent="fatou's polyrhythm layer + aaliya's acid line",
        commits=[
            _sha("narr-ragtime-4"),
            _sha("narr-ragtime-5"),
            _sha("narr-ragtime-6"),
        ],
        notes="fatou's polyrhythm commit caused spontaneous celebration in the call.",
        is_active=False,
        created_at=_now(days=32),
    ))

    # Reactions on fatou's polyrhythm commit (commit index 4)
    fatou_commit_id = _sha("narr-ragtime-4")
    fatou_reactions = [
        (MARCUS, "🔥"),
        (AALIYA, "🔥"),
        (GABRIEL, "🔥"),
        (SOFIA, "🔥"),
        (MARCUS, "👏"),
        (AALIYA, "👏"),
        (YUKI, "👏"),
        (PIERRE, "👏"),
        (CHEN, "😢"),   # chen weeps for the death of pure ragtime
        (FATOU, "😢"),  # fatou weeps tears of joy
    ]
    for user_id, emoji in fatou_reactions:
        try:
            db.add(MusehubReaction(
                reaction_id=_uid(f"narr-reaction-ragtime-{fatou_commit_id[:8]}-{user_id}-{emoji}"),
                repo_id=REPO_RAGTIME_EDM,
                target_type="commit",
                target_id=fatou_commit_id,
                user_id=user_id,
                emoji=emoji,
                created_at=_now(days=32),
            ))
        except Exception:
            pass

    # Comments on fatou's commit
    fatou_comments: list[tuple[str, str]] = [
        ("marcus", "Fatou WHAT. I was not expecting this. This is everything."),
        ("aaliya", "This polyrhythm layer turns the whole track into something else entirely. Ragtime was just the scaffolding — this is the music."),
        ("gabriel", "The way the West African pattern locks into the ragtime's inherent syncopation is... I need to sit with this."),
        ("yuki", "The spectral relationship between the 130 BPM techno grid and the polyrhythm's implied triple meter creates a beautiful tension. I hear 3-against-4 every 8 bars."),
        ("chen", "For the record — I think this might be too much. Joplin's architecture is already polyrhythmic. Adding another layer might obscure rather than enhance. (Still reacting with 😢 because I'm moved either way.)"),
        ("fatou", "Chen — fair criticism. The polyrhythm is deliberately *in front* of the mix here. I can pull it back -4 dB in the final mix so it's felt rather than heard."),
    ]
    for i, (author, body) in enumerate(fatou_comments):
        db.add(MusehubComment(
            comment_id=_uid(f"narr-comment-ragtime-fatou-{i}"),
            repo_id=REPO_RAGTIME_EDM,
            target_type="commit",
            target_id=fatou_commit_id,
            author=author,
            body=body,
            created_at=_now(days=32, hours=-i),
        ))

    # Release
    db.add(MusehubRelease(
        repo_id=REPO_RAGTIME_EDM,
        tag="v1.0.0",
        title="Maple Leaf Drops",
        body=(
            "## Maple Leaf Drops — v1.0.0\n\n"
            "Scott Joplin didn't live to see techno. We fixed that.\n\n"
            "### Musicians\n"
            "- **marcus** — Piano (Joplin arrangements), production, 909 drums\n"
            "- **fatou** — West African polyrhythm layer, breakdown arrangement\n"
            "- **aaliya** — Acid 303 line, 909 arrangement, sidechain design\n\n"
            "### Highlights\n"
            "- Joplin's Maple Leaf Rag chord structures at 130 BPM\n"
            "- 4-on-the-floor kick beneath Joplin's inherent 3-against-4\n"
            "- fatou's polyrhythm layer — the spiritual centre of the track\n"
            "- 8-bar ragtime-only breakdown before the final drop\n\n"
            "### Formats\nMIDI bundle, MP3 club master (-1 LUFS), stems"
        ),
        commit_id=_sha("narr-ragtime-7"),
        download_urls={
            "midi_bundle": f"/releases/{REPO_RAGTIME_EDM}-v1.0.0.zip",
            "mp3": f"/releases/{REPO_RAGTIME_EDM}-v1.0.0.mp3",
            "stems": f"/releases/{REPO_RAGTIME_EDM}-v1.0.0-stems.zip",
        },
        author="marcus",
        created_at=_now(days=24),
    ))

    print("  ✅ Scenario 3: Ragtime EDM Collab — 8 commits, fire reactions, Maple Leaf Drops release")


# ---------------------------------------------------------------------------
# Scenario 4 — Community Chaos
# ---------------------------------------------------------------------------

async def _seed_community_chaos(db: AsyncSession) -> None:
    """Community Chaos: 5 simultaneous open PRs on 'community-jam', a 25-comment
    key signature debate on a central issue, 3 PRs in conflict state, and a
    milestone at 70% completion.
    """
    db.add(MusehubRepo(
        repo_id=REPO_COMMUNITY_JAM,
        name="Community Jam Vol. 1",
        owner="gabriel",
        slug="community-jam-vol-1",
        owner_user_id=GABRIEL,
        visibility="public",
        description="Open contribution jam — everyone adds something. Organised chaos.",
        tags=["community", "collab", "open", "jam", "experimental"],
        key_signature="C major",  # the disputed key
        tempo_bpm=95,
        created_at=_now(days=50),
    ))

    # Base commits
    base_commits: list[dict[str, Any]] = [
        dict(message="init: community jam template — C major at 95 BPM", author="gabriel", days=50),
        dict(message="feat(piano): opening vamp — community starting point", author="gabriel", days=48),
        dict(message="feat(bass): walking bass line — open for all to build on", author="marcus", days=46),
        dict(message="feat(drums): groove template — pocket at 95 BPM", author="fatou", days=44),
    ]
    jam_prev: str | None = None
    for i, c in enumerate(base_commits):
        cid = _sha(f"narr-community-{i}")
        db.add(MusehubCommit(
            commit_id=cid,
            repo_id=REPO_COMMUNITY_JAM,
            branch="main",
            parent_ids=[jam_prev] if jam_prev else [],
            message=c["message"],
            author=c["author"],
            timestamp=_now(days=c["days"]),
            snapshot_id=_sha(f"snap-narr-community-{i}"),
        ))
        jam_prev = cid
    db.add(MusehubBranch(
        repo_id=REPO_COMMUNITY_JAM,
        name="main",
        head_commit_id=_sha("narr-community-3"),
    ))

    # 5 simultaneous open PRs
    pr_configs: list[dict[str, Any]] = [
        dict(n=0, title="Feat: modulate to A minor — more emotional depth",
             body="C major is too bright for this jam. Relative minor gives it soul.",
             author="yuki", branch="feat/a-minor-modal"),
        dict(n=1, title="Feat: add jazz reharmonisation — ii-V-I substitutions",
             body="The vamp needs harmonic movement. Jazz subs every 4 bars.",
             author="marcus", branch="feat/jazz-reharmony"),
        dict(n=2, title="Feat: Afrobeat key shift — G major groove",
             body="G major sits better with the highlife guitar pattern I'm adding.",
             author="aaliya", branch="feat/g-major-afrobeat"),
        dict(n=3, title="Feat: microtonal drift — 31-TET temperament",
             body="C major in equal temperament is compromised. 31-TET gives pure intervals.",
             author="chen", branch="feat/31-tet"),
        dict(n=4, title="Feat: stay in C major — add pedal point for tension",
             body="The key is fine. Add a B pedal under the vamp for maximum dissonance.",
             author="pierre", branch="feat/c-pedal-point"),
    ]
    pr_ids: list[str] = []
    for pc in pr_configs:
        pr_id = _uid(f"narr-community-pr-{pc['n']}")
        pr_ids.append(pr_id)
        # PRs 1, 2, 3 are in conflict
        db.add(MusehubPullRequest(
            pr_id=pr_id,
            repo_id=REPO_COMMUNITY_JAM,
            title=pc["title"],
            body=pc["body"] + (
                "\n\n⚠️ **CONFLICT**: Multiple PRs modify `tracks/piano.mid` and "
                "`tracks/bass.mid`. Needs key signature resolution before merge."
                if pc["n"] in (1, 2, 3) else ""
            ),
            state="open",
            from_branch=pc["branch"],
            to_branch="main",
            author=pc["author"],
            created_at=_now(days=40 - pc["n"] * 2),
        ))

    # The key signature debate issue (25 comments)
    key_debate_issue_id = _uid("narr-community-issue-key-sig")
    db.add(MusehubIssue(
        issue_id=key_debate_issue_id,
        repo_id=REPO_COMMUNITY_JAM,
        number=1,
        title="[DEBATE] Which key should Community Jam Vol. 1 be in?",
        body=(
            "We have 5 open PRs proposing 5 different keys. "
            "This issue is the canonical place to resolve it. "
            "Please make your case. Voting closes when we reach consensus or "
            "gabriel makes a unilateral decision.\n\n"
            "Current proposals:\n"
            "- C major (original)\n"
            "- A minor (relative minor)\n"
            "- G major (Afrobeat fit)\n"
            "- 31-TET C major (microtonal)\n"
            "- C major + B pedal (stay, add tension)\n\n"
            "Make. Your. Case."
        ),
        state="open",
        labels=["key-signature", "debate", "community", "blocker"],
        author="gabriel",
        created_at=_now(days=38),
    ))

    # 25-comment key signature debate
    key_debate: list[tuple[str, str]] = [
        ("gabriel",  "I opened C major as a neutral starting point — maximum accessibility. But I'm genuinely open to being overruled."),
        ("yuki",     "C major is a compositional blank slate — it's fine for exercises but emotionally empty. A minor gives us minor seconds, tritones, modal possibilities."),
        ("marcus",   "A minor is fine but the walking bass I wrote assumes C major changes. Any key shift means rewriting the bass line."),
        ("aaliya",   "G major suits the highlife pattern I'm adding. The guitar's open G string rings sympathetically with the root. It's not just theory — it's physics."),
        ("chen",     "Can we pause and acknowledge that 'key' in equal temperament is already a compromise? 31-TET gives us pure major thirds (386 cents vs 400 cents ET). The difference is audible."),
        ("pierre",   "Chen, I respect the microtonal argument but this is a community jam, not a tuning experiment. Most contributors can't work in 31-TET."),
        ("chen",     "That's a valid practical constraint. I'll withdraw 31-TET if we at least acknowledge it as the theoretically superior option."),
        ("fatou",    "Practically speaking — I've been writing drums. Drums don't care what key you're in. But the bass line does. Marcus, can you adapt the walking bass to G major?"),
        ("marcus",   "Walking bass in G major works. But then yuki's jazz subs don't resolve correctly — they're written for C major ii-V-I."),
        ("yuki",     "My jazz subs work in any key if you transpose. The *structure* is the same. But I'd need to redo the voice leading."),
        ("aaliya",   "This is the problem — every key choice benefits some contributors and costs others. We need a decision principle, not a debate."),
        ("gabriel",  "Decision principle: the key that requires the least total rework across all active PRs. Can everyone estimate their rework in hours?"),
        ("marcus",   "C major → no rework. G major → 2 hours. A minor → 1 hour."),
        ("yuki",     "C major → no rework (jazz subs already written). A minor → 30 min transpose. G major → 1 hour."),
        ("aaliya",   "C major → 3 hours (re-record guitar in sympathetic key). G major → 0 rework. A minor → 2 hours."),
        ("chen",     "C major → 0 rework (I'm withdrawing 31-TET). G major → 0 rework (I have no key-specific content yet)."),
        ("pierre",   "C major → 0. G major → 1 hour. A minor → 30 min."),
        ("fatou",    "C major → 0. G major → 0. A minor → 0."),
        ("gabriel",  "Total rework: C major = 3h, G major = 3h, A minor = 4h. It's a tie between C and G."),
        ("aaliya",   "Then G major wins on musical grounds — it gives the Afrobeat guitar its natural resonance AND ties with C on total effort."),
        ("marcus",   "I'll support G major if we accept that the walking bass rewrite is part of the scope."),
        ("yuki",     "G major. Fine. I'll transpose my jazz subs tonight."),
        ("pierre",   "G major it is. Though I still think the B pedal idea works better in C. I'll adapt."),
        ("chen",     "G major. I'll note for the record that G major in 31-TET is 19 steps of the 31-tone octave. Just saying."),
        ("gabriel",  "We have consensus: G major, 95 BPM. Aaliya's PR (#3) is the base. All other PRs should rebase onto her branch. Closing the debate. Thanks everyone — this is what community composition looks like."),
    ]
    issue_parent: str | None = None
    for i, comment in enumerate(key_debate):
        author, body = comment
        cid = _uid(f"narr-community-debate-{i}")
        db.add(MusehubIssueComment(
            comment_id=cid,
            issue_id=key_debate_issue_id,
            repo_id=REPO_COMMUNITY_JAM,
            author=author,
            body=body,
            parent_id=None,
            musical_refs=[],
            created_at=_now(days=38 - i // 2),
        ))

    # Milestone at 70% completion (14/20 tasks done)
    milestone_id = _uid("narr-community-milestone-1")
    db.add(MusehubMilestone(
        milestone_id=milestone_id,
        repo_id=REPO_COMMUNITY_JAM,
        number=1,
        title="Community Jam Vol. 1 — Full Release",
        description="All tracks recorded, key signature resolved, final mix complete.",
        state="open",
        author="gabriel",
        due_on=_now(days=-14),  # 2 weeks from now
        created_at=_now(days=50),
    ))

    # Issues tracking milestone tasks (14 closed = 70%, 6 open = 30%)
    milestone_tasks: list[dict[str, Any]] = [
        # Closed (done)
        dict(n=2,  title="Set tempo at 95 BPM", state="closed", labels=["done"]),
        dict(n=3,  title="Piano vamp template", state="closed", labels=["done"]),
        dict(n=4,  title="Walking bass template", state="closed", labels=["done"]),
        dict(n=5,  title="Drums groove template", state="closed", labels=["done"]),
        dict(n=6,  title="Define song structure (AABA)", state="closed", labels=["done"]),
        dict(n=7,  title="Record piano intro 4 bars", state="closed", labels=["done"]),
        dict(n=8,  title="Record piano A section", state="closed", labels=["done"]),
        dict(n=9,  title="Record piano B section", state="closed", labels=["done"]),
        dict(n=10, title="Record piano outro", state="closed", labels=["done"]),
        dict(n=11, title="Drum arrangement complete", state="closed", labels=["done"]),
        dict(n=12, title="Bass line complete", state="closed", labels=["done"]),
        dict(n=13, title="Afrobeat guitar layer", state="closed", labels=["done"]),
        dict(n=14, title="Acid 303 layer", state="closed", labels=["done"]),
        dict(n=15, title="Resolve key signature debate", state="closed", labels=["done"]),
        # Open (30% remaining)
        dict(n=16, title="Rebase all PRs onto G major", state="open", labels=["blocked"]),
        dict(n=17, title="Jazz reharmonisation (yuki)", state="open", labels=["in-progress"]),
        dict(n=18, title="Microtonal spice layer (chen)", state="open", labels=["in-progress"]),
        dict(n=19, title="Final mix and master", state="open", labels=["todo"]),
        dict(n=20, title="Release v1.0.0", state="open", labels=["todo"]),
        dict(n=21, title="Write liner notes", state="open", labels=["todo"]),
    ]
    for task in milestone_tasks:
        db.add(MusehubIssue(
            repo_id=REPO_COMMUNITY_JAM,
            number=task["n"],
            title=task["title"],
            body=f"Milestone task: {task['title']}",
            state=task["state"],
            labels=task["labels"],
            author="gabriel",
            milestone_id=milestone_id,
            created_at=_now(days=50 - task["n"]),
        ))

    print("  ✅ Scenario 4: Community Chaos — 5 open PRs, 25-comment debate, 70% milestone")


# ---------------------------------------------------------------------------
# Scenario 5 — Goldberg Milestone
# ---------------------------------------------------------------------------

async def _seed_goldberg_milestone(db: AsyncSession) -> None:
    """Goldberg Milestone: gabriel's 30-variation Goldberg project. 28/30 done.
    Variation 25 has an 18-comment debate (slow vs fast). Variation 29 PR
    with yuki requesting 'more ornamentation'.
    """
    db.add(MusehubRepo(
        repo_id=REPO_GOLDBERG,
        name="Goldberg Variations (Complete)",
        owner="gabriel",
        slug="goldberg-variations",
        owner_user_id=GABRIEL,
        visibility="public",
        description=(
            "Bach's Goldberg Variations — all 30. "
            "Aria + 30 variations + Aria da capo. "
            "Each variation is a separate branch, PR, and closed issue."
        ),
        tags=["Bach", "Goldberg", "variations", "harpsichord", "G major", "classical"],
        key_signature="G major",
        tempo_bpm=80,
        created_at=_now(days=120),
    ))

    # Base commit — the Aria
    db.add(MusehubCommit(
        commit_id=_sha("narr-goldberg-aria"),
        repo_id=REPO_GOLDBERG,
        branch="main",
        parent_ids=[],
        message="init: Aria — G major sarabande, 32-bar binary form",
        author="gabriel",
        timestamp=_now(days=120),
        snapshot_id=_sha("snap-narr-goldberg-aria"),
    ))
    db.add(MusehubBranch(
        repo_id=REPO_GOLDBERG,
        name="main",
        head_commit_id=_sha("narr-goldberg-aria"),
    ))

    # Milestone for all 30 variations
    milestone_id = _uid("narr-goldberg-milestone-30")
    db.add(MusehubMilestone(
        milestone_id=milestone_id,
        repo_id=REPO_GOLDBERG,
        number=1,
        title="All 30 Variations Complete",
        description=(
            "Track progress through all 30 Goldberg variations. "
            "Each variation gets its own issue, branch, commits, and PR. "
            "28/30 complete."
        ),
        state="open",
        author="gabriel",
        due_on=_now(days=-30),
        created_at=_now(days=120),
    ))

    # 28 closed variation issues + Var 25 issue with 18-comment debate
    # + Var 29 issue (open) + Var 30 issue (open)
    variation_metadata: dict[int, dict[str, str]] = {
        1:  dict(style="canon at the octave", key="G major", character="simple two-voice canon"),
        2:  dict(style="free composition", key="G major", character="crisp toccata-like passages"),
        3:  dict(style="canon at the ninth", key="G major", character="flowing melodic lines"),
        4:  dict(style="passepied", key="G major", character="brisk dance feel"),
        5:  dict(style="free with hand-crossing", key="G major", character="brilliant hand-crossing"),
        6:  dict(style="canon at the seventh", key="G major", character="lyrical canon"),
        7:  dict(style="gigue", key="G major", character="jig rhythm, 6/8"),
        8:  dict(style="free with hand-crossing", key="G major", character="energetic, sparkling"),
        9:  dict(style="canon at the fifth", key="E minor", character="tender, intimate"),
        10: dict(style="fughetta", key="G major", character="four-voice fugue sketch"),
        11: dict(style="free with hand-crossing", key="G major", character="rapid hand-crossing"),
        12: dict(style="canon at the fourth", key="G major", character="inverted canon"),
        13: dict(style="free", key="G major", character="gentle, flowing ornaments"),
        14: dict(style="free with hand-crossing", key="G major", character="brilliant two-voice"),
        15: dict(style="canon at the fifth (inverted)", key="G minor", character="profound, slow"),
        16: dict(style="overture", key="G major", character="French overture style"),
        17: dict(style="free", key="G major", character="syncopated, off-beat accents"),
        18: dict(style="canon at the sixth", key="G major", character="stately canon"),
        19: dict(style="minuet", key="G major", character="elegant dance"),
        20: dict(style="free with hand-crossing", key="G major", character="percussive virtuosity"),
        21: dict(style="canon at the seventh", key="G minor", character="contemplative, modal"),
        22: dict(style="alla breve", key="G major", character="learned counterpoint"),
        23: dict(style="free", key="G major", character="brilliant, showy"),
        24: dict(style="canon at the octave", key="G major", character="graceful, symmetrical"),
        25: dict(style="adagio (siciliana)", key="G minor", character="profoundly expressive, slow"),
        26: dict(style="free with hand-crossing", key="G major", character="rapid parallel thirds"),
        27: dict(style="canon at the ninth", key="G major", character="clear, clean"),
        28: dict(style="free with trills", key="G major", character="trill-saturated virtuosity"),
        29: dict(style="quodlibet", key="G major", character="folk songs woven into counterpoint"),
        30: dict(style="aria da capo", key="G major", character="return of the opening Aria"),
    }

    for n in range(1, 31):
        meta = variation_metadata[n]
        is_done = n <= 28  # 28/30 complete
        issue_id = _uid(f"narr-goldberg-issue-var-{n}")

        db.add(MusehubIssue(
            issue_id=issue_id,
            repo_id=REPO_GOLDBERG,
            number=n,
            title=f"Variation {n} — {meta['style']}",
            body=(
                f"**Style:** {meta['style']}\n"
                f"**Key:** {meta['key']}\n"
                f"**Character:** {meta['character']}\n\n"
                f"Acceptance criteria:\n"
                f"- [ ] Correct ornaments (trills, mordents, turns)\n"
                f"- [ ] Correct articulation (slurs, staccati)\n"
                f"- [ ] Voice leading reviewed\n"
                f"- [ ] Tempo marking verified against Urtext"
            ),
            state="closed" if is_done else "open",
            labels=["variation", f"variation-{n}", meta["key"].replace(" ", "-").lower()],
            author="gabriel",
            milestone_id=milestone_id,
            created_at=_now(days=120 - n * 3),
        ))

        # Add commits for closed variations
        if is_done:
            commit_id = _sha(f"narr-goldberg-var-{n}")
            db.add(MusehubCommit(
                commit_id=commit_id,
                repo_id=REPO_GOLDBERG,
                branch="main",
                parent_ids=[_sha(f"narr-goldberg-var-{n-1}") if n > 1 else _sha("narr-goldberg-aria")],
                message=f"feat(var{n}): Variation {n} — {meta['style']} — {meta['character']}",
                author="gabriel",
                timestamp=_now(days=120 - n * 3 + 1),
                snapshot_id=_sha(f"snap-narr-goldberg-var-{n}"),
            ))

    # Variation 25 debate — 18 comments (slow vs fast tempo)
    var25_issue_id = _uid("narr-goldberg-issue-var-25")
    var25_debate: list[tuple[str, str]] = [
        ("gabriel",  "Variation 25 is the emotional heart of the Goldberg. G minor, adagio. I'm recording at ♩=44. Is this too slow?"),
        ("pierre",   "Glenn Gould's 1981 recording is ♩=40. Rosalyn Tureck goes as slow as ♩=36. ♩=44 is actually on the fast side for this variation."),
        ("sofia",    "♩=44 is where I'd want it too. Below ♩=40 and the ornaments lose their shape — the trills become sludge."),
        ("yuki",     "The ornaments at ♩=44 have to be measured very precisely or they rush. Have you tried recording with a click and then humanizing?"),
        ("gabriel",  "I'm recording against a click at ♩=44 but the 32nd-note ornaments in bar 13 feel mechanical. Maybe ♩=40 gives them more breathing room?"),
        ("pierre",   "♩=40 is where the silence *between* ornament notes starts to breathe. That's where this variation lives — in the silence."),
        ("sofia",    "But the melodic line can't be too fragmentary. There's a balance between ornament breathing room and melodic continuity."),
        ("marcus",   "From a production perspective — at ♩=40 the decay of each harpsichord note is long enough that adjacent notes overlap in the reverb. That creates a natural legato even on a plucked instrument."),
        ("gabriel",  "That's a point I hadn't considered. The harpsichord's decay effectively determines the minimum tempo for legato character."),
        ("chen",     "The acoustics of the instrument being used matter here. What's the decay time on your harpsichord model? At what tempo does the decay of beat 1 reach -60 dB before beat 2 hits?"),
        ("gabriel",  "Good question — with the harpsichord sample I'm using, decay reaches -60 dB in about 1.2 seconds. At ♩=40, a quarter note = 1.5 seconds. So there IS overlap."),
        ("marcus",   "At ♩=44, quarter note = 1.36 seconds, so barely any overlap. ♩=40 gives you legato for free."),
        ("pierre",   "And the ornamentation question answers itself — at ♩=40, the ornaments land in the natural decay tail of the preceding note. They're not rushed because they're riding the resonance."),
        ("yuki",     "This is the right analysis. ♩=40. The ornaments will feel inevitable rather than imposed."),
        ("sofia",    "Agreed. ♩=40 and let the harpsichord's physics make the artistic decision."),
        ("gabriel",  "OK — ♩=40 it is. I'll re-record bar 13 and the coda. Thank you for this. The physics argument is the one that convinced me."),
        ("pierre",   "Document the tempo decision in the commit message. Future contributors will wonder why ♩=40 and not ♩=44."),
        ("gabriel",  "Already done: 'tempo: adagio at q=40 — harpsichord decay overlap creates natural legato; ornaments ride resonance tail (see issue #25)'"),
    ]
    for i, comment in enumerate(var25_debate):
        author, body = comment
        db.add(MusehubIssueComment(
            comment_id=_uid(f"narr-goldberg-var25-comment-{i}"),
            issue_id=var25_issue_id,
            repo_id=REPO_GOLDBERG,
            author=author,
            body=body,
            parent_id=None,
            musical_refs=[],
            created_at=_now(days=120 - 25 * 3 + i // 3),
        ))

    # Variation 29 PR — open, yuki requests more ornamentation
    var29_pr_id = _uid("narr-goldberg-pr-var-29")
    db.add(MusehubCommit(
        commit_id=_sha("narr-goldberg-var-29-draft"),
        repo_id=REPO_GOLDBERG,
        branch="feat/var-29-quodlibet",
        parent_ids=[_sha("narr-goldberg-var-28")],
        message="feat(var29): Variation 29 draft — quodlibet with folk songs",
        author="gabriel",
        timestamp=_now(days=8),
        snapshot_id=_sha("snap-narr-goldberg-var-29-draft"),
    ))
    db.add(MusehubBranch(
        repo_id=REPO_GOLDBERG,
        name="feat/var-29-quodlibet",
        head_commit_id=_sha("narr-goldberg-var-29-draft"),
    ))
    db.add(MusehubPullRequest(
        pr_id=var29_pr_id,
        repo_id=REPO_GOLDBERG,
        title="Feat: Variation 29 — Quodlibet (folk songs in counterpoint)",
        body=(
            "## Variation 29 — Quodlibet\n\n"
            "Bach's joke variation — two folk songs ('I've been so long away from you' "
            "and 'Cabbage and turnips') woven into strict four-voice counterpoint.\n\n"
            "## What's here\n"
            "- Folk song 1: soprano voice, bars 1-8\n"
            "- Folk song 2: alto voice, bars 1-8\n"
            "- Bass line: walking quodlibet bass\n"
            "- Tenor: free counterpoint bridging folk tunes\n\n"
            "## What needs work\n"
            "- Ornamentation is sparse — I've not yet added the trills and mordents\n"
            "  that appear in the Urtext\n"
            "- Need a review from yuki on ornament placement\n\n"
            "Closes #29"
        ),
        state="open",
        from_branch="feat/var-29-quodlibet",
        to_branch="main",
        author="gabriel",
        created_at=_now(days=7),
    ))

    # yuki's review — requesting more ornamentation
    yuki_review_id = _uid("narr-goldberg-review-yuki-var29")
    db.add(MusehubPRReview(
        id=yuki_review_id,
        pr_id=var29_pr_id,
        reviewer_username="yuki",
        state="changes_requested",
        body=(
            "The counterpoint structure is clean and the folk song placements are musically correct. "
            "However, the ornamentation is *significantly* under-specified. "
            "This is the penultimate variation — listeners have been through 28 variations of "
            "increasing complexity. They expect maximum ornamental density here before the Aria returns.\n\n"
            "**Specific requests:**\n\n"
            "1. Bar 3, soprano, beat 2: add a double mordent (pralltriller) on the C\n"
            "2. Bar 5, alto, beat 1: the folk melody needs a trill on the leading tone\n"
            "3. Bar 7-8: the authentic cadence needs an ornamental turn (Doppelschlag) "
            "on the penultimate note — this is standard Baroque practice at cadences\n"
            "4. General: consider adding short appoggiaturas throughout to soften the "
            "counterpoint into something more human\n\n"
            "**Reference:** Look at Variation 13 — similar character, full ornamental treatment. "
            "That's the standard for this project.\n\n"
            "More ornamentation, please. This is Bach's joke variation — it should feel lavish, "
            "not ascetic."
        ),
        submitted_at=_now(days=6),
        created_at=_now(days=6),
    ))

    # gabriel's response comment
    db.add(MusehubPRComment(
        comment_id=_uid("narr-goldberg-var29-gabriel-response"),
        pr_id=var29_pr_id,
        repo_id=REPO_GOLDBERG,
        author="gabriel",
        body=(
            "yuki — thank you for the detailed feedback. You're right that I was too conservative.\n\n"
            "I'll add:\n"
            "- Double mordent on bar 3 soprano C ✓\n"
            "- Trill on bar 5 alto leading tone ✓\n"
            "- Doppelschlag at bar 7-8 cadence ✓\n"
            "- Appoggiaturas throughout — going to listen to Var 13 again and match the density\n\n"
            "Give me 24 hours. The quodlibet deserves to arrive well-dressed."
        ),
        target_type="general",
        created_at=_now(days=5),
    ))

    print("  ✅ Scenario 5: Goldberg Milestone — 28/30 done, Var 25 debate, Var 29 ornament review")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def seed_narratives(db: AsyncSession, force: bool = False) -> None:
    """Seed all 5 narrative scenarios into the database.

    Idempotent — checks for the sentinel repo before inserting. Pass ``force``
    to wipe existing narrative data and re-seed from scratch.
    """
    print("🎭 Seeding MuseHub narrative scenarios…")

    result = await db.execute(
        text("SELECT COUNT(*) FROM musehub_repos WHERE repo_id = :rid"),
        {"rid": SENTINEL_REPO_ID},
    )
    already_seeded = (result.scalar() or 0) > 0

    if already_seeded and not force:
        print("  ⚠️  Narrative scenarios already seeded — skipping. Pass --force to reseed.")
        return

    if already_seeded and force:
        print("  🗑  --force: clearing existing narrative data…")
        narrative_repos = [
            REPO_NEO_BAROQUE, REPO_NEO_BAROQUE_FORK,
            REPO_NOCTURNE,
            REPO_RAGTIME_EDM,
            REPO_COMMUNITY_JAM,
            REPO_GOLDBERG,
        ]
        # Delete dependent records first, then repos (cascade handles the rest)
        for rid in narrative_repos:
            await db.execute(text("DELETE FROM musehub_repos WHERE repo_id = :rid"), {"rid": rid})
        await db.flush()

    await _seed_bach_remix_war(db)
    await db.flush()

    await _seed_chopin_coltrane(db)
    await db.flush()

    await _seed_ragtime_edm(db)
    await db.flush()

    await _seed_community_chaos(db)
    await db.flush()

    await _seed_goldberg_milestone(db)
    await db.flush()

    await db.commit()

    print()
    print("=" * 72)
    print("🎭  NARRATIVE SCENARIOS — SEEDED")
    print("=" * 72)
    BASE = "http://localhost:10001/musehub/ui"
    print(f"\n1. Bach Remix War:     {BASE}/gabriel/neo-baroque-counterpoint")
    print(f"   Fork (trap):         {BASE}/marcus/neo-baroque-counterpoint")
    print(f"\n2. Chopin+Coltrane:    {BASE}/pierre/nocturne-op9-no2")
    print(f"\n3. Ragtime EDM:        {BASE}/marcus/maple-leaf-drops")
    print(f"\n4. Community Chaos:    {BASE}/gabriel/community-jam-vol-1")
    print(f"\n5. Goldberg Milestone: {BASE}/gabriel/goldberg-variations")
    print()
    print("=" * 72)
    print("✅  Narrative seed complete.")
    print("=" * 72)


async def main() -> None:
    """Entry point — run all narrative scenarios."""
    force = "--force" in sys.argv
    db_url: str = settings.database_url or ""
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]  # SQLAlchemy: sessionmaker with class_=AsyncSession triggers call-overload false positive
    async with async_session() as db:
        await seed_narratives(db, force=force)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
