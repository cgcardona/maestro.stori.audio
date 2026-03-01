# MuseHub Complete Taxonomy
## Muse Protocol · MuseHub URL Structure · Page Feature Matrix · Seed Data Blueprint

> **Status:** Living document — each section generates a batch of GitHub issues.
> **Audience:** Agents, engineers, and product stakeholders.

---

## TABLE OF CONTENTS

1. [Muse CLI Command Index](#1-muse-cli-command-index)
2. [MuseHub URL Taxonomy — Current vs Missing](#2-musehub-url-taxonomy)
3. [Per-Page Feature Matrix](#3-per-page-feature-matrix)
4. [Seed Data Blueprint](#4-seed-data-blueprint)
5. [GitHub Issue Backlog](#5-github-issue-backlog)

---

## 1. MUSE CLI COMMAND INDEX

> Muse is Git for music. Every command maps to a musical dimension.
> **Porcelain** = human-facing composites. **Plumbing** = low-level primitives.

### 1.1 Porcelain — Repository Lifecycle

| Command | Key Flags | Musical Meaning | MuseHub URL Exposed? |
|---|---|---|---|
| `muse init` | `--bare`, `--template` | Create a new Muse repo locally | POST `/repos` |
| `muse clone` | `--branch`, `--depth`, `--bare` | Clone from MuseHub remote | POST via sync |
| `muse commit` | `-m`, `--amend`, `--no-verify`, `--allow-empty` | Snapshot current DAW state | POST `/sync/push` |
| `muse amend` | `--no-edit`, `-m` | Rewrite the last commit message/contents | ❌ Missing UI |
| `muse push` | `--force`, `--tags`, `--set-upstream` | Upload commits to MuseHub | POST `/sync/push` |
| `muse pull` | `--rebase`, `--ff-only`, `--no-commit` | Download + integrate remote changes | POST `/sync/pull` |
| `muse fetch` | `--all`, `--tags`, `--prune` | Download refs without integrating | ❌ Missing API |
| `muse status` | `--short`, `--branch`, `--porcelain` | Show working-tree status | GET `/repos/{id}/status` |
| `muse log` | `--oneline`, `--graph`, `--since`, `--until`, `--author`, `--grep`, `--follow`, `-p` | Commit history | `/commits` page + graph |
| `muse show` | `--stat`, `--format`, `--name-only` | Inspect a single commit | `/commits/{id}` page |
| `muse diff` | `--stat`, `--cached`, `--word-diff`, `--name-only` | Multi-dimensional musical diff | `/compare/base...head` |
| `muse checkout` | `-b`, `--detach`, `--orphan`, `--track` | Switch branches / restore files | ❌ Missing UI |
| `muse restore` | `--staged`, `--source`, `--patch` | Discard working-tree changes | ❌ Missing UI |
| `muse reset` | `--soft`, `--mixed`, `--hard`, `--keep` | Move HEAD, optionally reset index | ❌ Missing UI |
| `muse revert` | `-n`, `--no-edit`, `--mainline` | Create inverse commit | ❌ Missing UI |
| `muse merge` | `--no-ff`, `--squash`, `--abort`, `--strategy` | Combine two branches | POST `/pull-requests/{id}/merge` |
| `muse rebase` | `-i`, `--onto`, `--abort`, `--continue`, `--exec` | Linearize commit history | ❌ Missing UI |
| `muse cherry-pick` | `-n`, `--continue`, `--abort`, `--edit` | Apply specific commit to current branch | ❌ Missing UI |
| `muse stash` | `push`, `pop`, `apply`, `drop`, `list`, `show` | Temporarily shelve changes | ❌ Missing API + UI |
| `muse tag` | `-a`, `-d`, `-l`, `--sort`, `-m`, `-f` | Semantic version milestones | GET/POST `/releases` |
| `muse remote` | `add`, `remove`, `set-url`, `-v`, `rename` | Manage remotes | ❌ Missing UI |
| `muse worktree` | `add`, `list`, `remove`, `prune` | Parallel branch workspaces | ❌ Missing UI |
| `muse bisect` | `start`, `good`, `bad`, `reset`, `log`, `replay` | Binary search through history for a change | ❌ Missing UI |

### 1.2 Porcelain — Musical Analysis (Muse-Native)

| Command | Key Flags | Musical Meaning | MuseHub URL Exposed? |
|---|---|---|---|
| `muse arrange` | `--ref`, `--format` | Section × instrument density matrix | `/arrange/{ref}` ✅ |
| `muse ask` | `--ref`, `--model`, `--stream` | AI Q&A about a commit | `/context/{ref}` ✅ |
| `muse blame` | `--ref`, `--track`, `--region`, `--beat-range` | Who wrote which notes, when | ❌ Missing UI |
| `muse chord-map` | `--ref`, `--window`, `--resolution` | Chord progression over time | `/analysis/{ref}/chord-map` ✅ |
| `muse contour` | `--ref`, `--track`, `--smooth` | Melodic contour visualization | `/analysis/{ref}/contour` ✅ |
| `muse describe` | `--ref`, `--long`, `--abbrev` | Human-readable musical summary | ❌ Missing standalone UI |
| `muse dynamics` | `--ref`, `--track`, `--window` | Velocity/dynamics envelope | `/analysis/{ref}/dynamics` ✅ |
| `muse emotion-diff` | `--base`, `--head`, `--dimensions` | Emotional shift between two commits | ❌ Missing UI (analysis diff) |
| `muse form` | `--ref`, `--sections`, `--labels` | Formal structure (verse/chorus/bridge) | `/analysis/{ref}/form` ✅ |
| `muse groove-check` | `--ref`, `--track`, `--window` | Rhythmic groove analysis (swing ratio, microtiming) | `/analysis/{ref}/groove` ✅ |
| `muse harmony` | `--ref`, `--key`, `--mode` | Harmonic analysis (Roman numerals, cadences) | ❌ Missing standalone UI |
| `muse humanize` | `--amount`, `--velocity`, `--timing`, `--ref` | Add human-feel microtiming to a commit | ❌ Missing UI |
| `muse inspect` | `--ref`, `--track`, `--region`, `--verbose` | Deep inspection of a specific object | ❌ Missing UI |
| `muse key` | `--ref`, `--confidence`, `--algorithm` | Key detection + modulation map | `/analysis/{ref}/key` ✅ |
| `muse meter` | `--ref`, `--set`, `--detect` | Time signature detection + annotation | `/analysis/{ref}/meter` ✅ |
| `muse motif` | `--ref`, `--min-length`, `--threshold`, `--track` | Recurring motif browser | `/analysis/{ref}/motifs` ✅ |
| `muse play` | `--ref`, `--track`, `--loop`, `--from`, `--to` | Playback a commit | `/listen/{ref}` ✅ |
| `muse recall` | `--query`, `--k`, `--ref` | Semantic memory search over history | ❌ Missing UI |
| `muse render-preview` | `--ref`, `--track`, `--format`, `--stem` | Render audio preview | `/listen/{ref}/{path}` ✅ |
| `muse session` | `start`, `end`, `log`, `annotate` | Recording session lifecycle | `/sessions` ✅ |
| `muse similarity` | `--base`, `--head`, `--dimensions`, `--threshold` | Cross-commit musical similarity score | ❌ Missing UI |
| `muse swing` | `--ref`, `--track`, `--amount`, `--style` | Quantize swing ratio analysis | `/analysis/{ref}/groove` (partial) |
| `muse tempo` | `--ref`, `--set`, `--detect`, `--scale` | Tempo detection + annotation | `/analysis/{ref}/tempo` ✅ |
| `muse tempo-scale` | `--factor`, `--ref`, `--preserve-feel` | Time-stretch a commit | ❌ Missing UI |
| `muse timeline` | `--ref`, `--branches`, `--tags`, `--since`, `--until` | Chronological SVG timeline | `/timeline` ✅ |
| `muse transpose` | `--semitones`, `--key`, `--ref`, `--track` | Pitch transposition with key annotation | ❌ Missing UI |
| `muse validate` | `--ref`, `--strict`, `--schema` | Validate MIDI structure | ❌ Missing UI |

### 1.3 Plumbing — Content-Addressed Store

| Command | Key Flags | Description | MuseHub Exposed? |
|---|---|---|---|
| `muse hash-object` | `-w`, `-t`, `--stdin` | Write blob to object store | POST `/objects` |
| `muse cat-object` | `-p`, `-t`, `-s`, `--batch` | Read object by hash | GET `/objects/{hash}` ✅ |
| `muse commit-tree` | `-p`, `-m`, `--no-gpg-sign` | Create commit from a tree | ❌ Plumbing only |
| `muse write-tree` | `--missing-ok`, `--prefix` | Write working tree to object store | ❌ Plumbing only |
| `muse read-tree` | `-m`, `--reset`, `-u` | Read a tree object into the index | ❌ Plumbing only |
| `muse rev-parse` | `--verify`, `--abbrev-ref`, `--symbolic` | Resolve refs to object hashes | GET `/repos/{id}/resolve/{ref}` |
| `muse symbolic-ref` | `--delete`, `-q`, `--short` | Read/write symbolic HEAD ref | ❌ Plumbing only |
| `muse update-ref` | `-d`, `--no-deref`, `--stdin` | Safely update a ref | ❌ Plumbing only |

### 1.4 Plumbing — Querying & Analysis

| Command | Key Flags | Description | MuseHub Exposed? |
|---|---|---|---|
| `muse context` | `--ref`, `--tokens`, `--format` | Generate AI context blob | GET `/analysis/{ref}/context` |
| `muse divergence` | `--base`, `--head`, `--dimensions` | Compute branch divergence scores | `/divergence` ✅ |
| `muse find` | `--commit`, `--tag`, `--branch`, `--pattern` | Search refs/objects by pattern | GET `/search` ✅ |
| `muse grep` | `--ref`, `--track`, `--pattern`, `--note`, `--pitch` | Search musical content | GET `/search?mode=musical` ✅ |
| `muse export` | `--format`, `--ref`, `--track`, `--stems` | Export to MIDI/MusicXML/stems | GET `/releases/{tag}` (partial) |
| `muse import` | `--format`, `--branch`, `--message` | Import external MIDI into Muse | ❌ Missing UI |
| `muse open` | `--commit`, `--ref` | Open in DAW | ❌ macOS DAW only |
| `muse resolve` | `--ours`, `--theirs`, `--manual` | Resolve merge conflicts | ❌ Missing UI |

### 1.5 Missing Commands — Not Yet Implemented

| Command | Description | Priority |
|---|---|---|
| `muse lock` | Lock a branch (require PR + review) | High |
| `muse sign` | GPG-sign a commit with artist identity | Medium |
| `muse verify` | Verify signed commit | Medium |
| `muse submodule` | Embed one Muse repo inside another (samples, loops) | High |
| `muse lfs` | Large file storage for audio stems | High |
| `muse bundle` | Package repo history for offline transfer | Low |
| `muse archive` | Export a snapshot as a tar/zip | Low |
| `muse clean` | Remove untracked working-tree files | Low |
| `muse maintenance` | Run background optimization tasks | Low |

---

## 2. MUSEHUB URL TAXONOMY

### 2.1 Current URL Surface

```
# Global
GET /musehub/ui/feed                          ← activity feed (current user)
GET /musehub/ui/search                        ← global cross-repo search
GET /musehub/ui/explore                       ← public repo discovery grid
GET /musehub/ui/trending                      ← repos sorted by stars

# User Profile
GET /musehub/ui/users/{username}              ← profile (repos, starred, watching tabs)

# Repo — Core
GET /musehub/ui/{owner}/{repo}                ← landing (README, stats, latest release)
GET /musehub/ui/{owner}/{repo}/commits        ← commit list (branch filter, search)
GET /musehub/ui/{owner}/{repo}/commits/{id}   ← commit detail + artifacts
GET /musehub/ui/{owner}/{repo}/commits/{id}/diff ← musical diff (radar, piano roll, A/B)
GET /musehub/ui/{owner}/{repo}/graph          ← DAG commit graph
GET /musehub/ui/{owner}/{repo}/tree/{ref}     ← file tree root
GET /musehub/ui/{owner}/{repo}/tree/{ref}/{path} ← file tree subdirectory
GET /musehub/ui/{owner}/{repo}/compare/{base}...{head} ← two-ref musical diff

# Repo — Collaboration
GET /musehub/ui/{owner}/{repo}/pulls          ← PR list
GET /musehub/ui/{owner}/{repo}/pulls/{pr_id}  ← PR detail (diff, comments, merge)
GET /musehub/ui/{owner}/{repo}/issues         ← issue list
GET /musehub/ui/{owner}/{repo}/issues/{number} ← issue detail + close/comment

# Repo — Musical Analysis
GET /musehub/ui/{owner}/{repo}/analysis/{ref}            ← dashboard (10 dimensions)
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/contour    ← melodic contour
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/tempo      ← tempo map
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/dynamics   ← dynamics envelope
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/key        ← key detection
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/meter      ← time signature
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/chord-map  ← chord progression
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/groove     ← rhythmic groove
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/emotion    ← emotion radar
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/form       ← formal structure
GET /musehub/ui/{owner}/{repo}/analysis/{ref}/motifs     ← motif browser

# Repo — Playback & Export
GET /musehub/ui/{owner}/{repo}/listen/{ref}              ← full-mix playback
GET /musehub/ui/{owner}/{repo}/listen/{ref}/{path}       ← single-stem playback
GET /musehub/ui/{owner}/{repo}/arrange/{ref}             ← arrangement matrix
GET /musehub/ui/{owner}/{repo}/piano-roll/{ref}          ← piano roll (all tracks)
GET /musehub/ui/{owner}/{repo}/piano-roll/{ref}/{path}   ← piano roll (single track)
GET /musehub/ui/{owner}/{repo}/embed/{ref}               ← iframe embeddable player

# Repo — Metadata
GET /musehub/ui/{owner}/{repo}/releases       ← release list
GET /musehub/ui/{owner}/{repo}/releases/{tag} ← release detail + downloads
GET /musehub/ui/{owner}/{repo}/sessions       ← recording session log
GET /musehub/ui/{owner}/{repo}/sessions/{id}  ← session detail
GET /musehub/ui/{owner}/{repo}/timeline       ← chronological SVG timeline
GET /musehub/ui/{owner}/{repo}/divergence     ← branch divergence radar
GET /musehub/ui/{owner}/{repo}/insights       ← repo analytics dashboard
GET /musehub/ui/{owner}/{repo}/credits        ← liner notes / contributor credits
GET /musehub/ui/{owner}/{repo}/context/{ref}  ← AI context viewer
GET /musehub/ui/{owner}/{repo}/search         ← in-repo search (4 modes)
```

### 2.2 Missing URLs — High Priority

```
# Repo — Missing Muse Commands
GET /musehub/ui/{owner}/{repo}/blame/{ref}/{path}   ← muse blame (note-level authorship)
GET /musehub/ui/{owner}/{repo}/stash                ← stash list + apply/drop
GET /musehub/ui/{owner}/{repo}/forks                ← fork network visualization
GET /musehub/ui/{owner}/{repo}/milestones           ← milestone list + progress
GET /musehub/ui/{owner}/{repo}/milestones/{number}  ← milestone detail + linked issues
GET /musehub/ui/{owner}/{repo}/labels               ← label management
GET /musehub/ui/{owner}/{repo}/similarity/{base}...{head} ← similarity score page
GET /musehub/ui/{owner}/{repo}/emotion-diff/{base}...{head} ← emotional delta viz
GET /musehub/ui/{owner}/{repo}/recall               ← semantic memory search over history
GET /musehub/ui/{owner}/{repo}/harmony/{ref}        ← full harmonic analysis (Roman numeral map)
GET /musehub/ui/{owner}/{repo}/webhooks             ← webhook management (admin)
GET /musehub/ui/{owner}/{repo}/settings             ← repo settings (visibility, description, tags)

# User — Missing
GET /musehub/ui/users/{username}/followers  ← follower list
GET /musehub/ui/users/{username}/following  ← following list

# Global — Missing
GET /musehub/ui/notifications               ← notification inbox
GET /musehub/ui/new                         ← new repo wizard
GET /musehub/ui/topics/{tag}                ← tag/topic browse page
GET /musehub/ui/collections                 ← curated repo collections
```

### 2.3 JSON API Parity Gaps (needed for agent consumers)

```
# Missing endpoints
GET  /api/v1/musehub/repos/{id}/blame/{ref}       ← muse blame output as JSON
GET  /api/v1/musehub/repos/{id}/stash             ← stash list
POST /api/v1/musehub/repos/{id}/stash             ← create stash
POST /api/v1/musehub/repos/{id}/stash/{stash_id}/pop
GET  /api/v1/musehub/repos/{id}/milestones        ← milestone list
POST /api/v1/musehub/repos/{id}/milestones
GET  /api/v1/musehub/repos/{id}/milestones/{n}
PATCH /api/v1/musehub/repos/{id}/milestones/{n}
DELETE /api/v1/musehub/repos/{id}/milestones/{n}
GET  /api/v1/musehub/repos/{id}/labels            ← label management
POST /api/v1/musehub/repos/{id}/labels
PATCH /api/v1/musehub/repos/{id}/issues/{n}/labels ← assign labels to issue
GET  /api/v1/musehub/repos/{id}/analysis/{ref}/similarity?compare={ref2}
GET  /api/v1/musehub/repos/{id}/analysis/{ref}/emotion-diff?base={ref}
GET  /api/v1/musehub/repos/{id}/analysis/{ref}/harmony
GET  /api/v1/musehub/repos/{id}/analysis/{ref}/recall?q={query}
GET  /api/v1/musehub/repos/{id}/settings
PATCH /api/v1/musehub/repos/{id}/settings
POST /api/v1/musehub/repos/{id}/transfer          ← transfer repo ownership
DELETE /api/v1/musehub/repos/{id}                 ← delete repo
GET  /api/v1/musehub/repos/{id}/collaborators     ← collaborator list
POST /api/v1/musehub/repos/{id}/collaborators
DELETE /api/v1/musehub/repos/{id}/collaborators/{username}
GET  /api/v1/musehub/topics/{tag}/repos           ← repos by topic
GET  /api/v1/musehub/users/{username}/activity    ← public activity feed
```

---

## 3. PER-PAGE FEATURE MATRIX

### 3.1 `/musehub/ui/explore` — Discovery Grid

**Current:** Repo cards with name, owner, description, star count, tags.

**Missing / Wanted:**
- [ ] Filter by genre tag (Classical, EDM, Jazz, Rock, Ambient, etc.)
- [ ] Filter by key signature (C major, A minor, etc.)
- [ ] Filter by tempo range (BPM slider)
- [ ] Filter by time signature
- [ ] Filter by license (CC0, CC BY, CC BY-SA, All Rights Reserved)
- [ ] Sort by: stars, forks, watchers, recently updated, recently created, most commits
- [ ] "Trending this week" / "Trending this month" toggle
- [ ] Inline audio preview on card hover (oEmbed player)
- [ ] Fork count badge on card
- [ ] "Made with Muse" verified badge
- [ ] Language/instrument breakdown chip (shows dominant instrument)
- [ ] Pagination + infinite scroll
- [ ] Topic/tag browse sidebar
- [ ] Featured collections carousel (curated by Stori team)

### 3.2 `/musehub/ui/{owner}/{repo}` — Repo Landing

**Current:** Branch selector, commit list preview, stats sidebar, README.

**Missing / Wanted:**
- [ ] README rendered as markdown (currently missing)
- [ ] "Latest release" banner with audio player
- [ ] Star / Watch / Fork action buttons with live counts
- [ ] Contributors list (avatar grid with commit counts)
- [ ] Activity graph (GitHub contribution-graph style, per-day commit heatmap)
- [ ] Language/instrument bar (% of MIDI by instrument family)
- [ ] Topics/tags as clickable chips → explore filter
- [ ] License badge
- [ ] Clone URL widget with copy button (`muse clone musehub://owner/repo`)
- [ ] Open in DAW button (deep link to Stori DAW)
- [ ] Branch picker with branch list + default branch indicator
- [ ] Last commit message + author + relative timestamp on branch selector
- [ ] File tree with MIDI file sizes
- [ ] "Used by" counter (repos that fork this)
- [ ] Pin to profile button (for own repos)
- [ ] Report / DMCA button
- [ ] Sponsor / support link (external URL from profile)
- [ ] oEmbed metadata `<meta>` tags for social previews

### 3.3 `/musehub/ui/{owner}/{repo}/commits` — Commit List

**Current:** Commit list with message, author, timestamp, hash.

**Missing / Wanted:**
- [ ] Branch / tag / commit selector dropdown
- [ ] Filter by author dropdown
- [ ] Filter by date range picker
- [ ] Search by commit message
- [ ] Filter by tag annotation (emotion:*, stage:*, key:*, tempo:*)
- [ ] Commit graph mini-lane on left (like GitHub's dot-and-line)
- [ ] Grouped by day headers
- [ ] Per-commit: changed instruments badge
- [ ] Per-commit: tempo_bpm badge if annotated
- [ ] Per-commit: key annotation badge if set
- [ ] Per-commit: emotional character chip (from `emotion:*` tag)
- [ ] Per-commit: musical summary from `muse describe` output
- [ ] "Compare" checkbox mode → compare two selected commits
- [ ] Copy commit hash button
- [ ] Download commit as MIDI button
- [ ] Verified/signed badge if commit is GPG-signed
- [ ] Merge commit visual indicator (two parents)
- [ ] Cherry-pick indicator badge

### 3.4 `/musehub/ui/{owner}/{repo}/commits/{id}` — Commit Detail

**Current:** Commit metadata, artifact list, diff link.

**Missing / Wanted:**
- [ ] Inline audio player (render_preview for this commit)
- [ ] Instrument breakdown pie/bar
- [ ] Full commit message with markdown rendering
- [ ] Parent commit link(s) (shows two for merge commits)
- [ ] All tags attached to this commit (emotion, stage, key, etc.)
- [ ] Metadata badges: tempo_bpm, key, time_signature, duration
- [ ] `muse describe` prose summary
- [ ] Changed files (MIDI tracks) with +/- change indicators
- [ ] Cherry-pick button (apply to another branch)
- [ ] Revert button
- [ ] Browse tree at this commit button
- [ ] Download button (MIDI bundle)
- [ ] Reactions (emoji reactions on the commit)
- [ ] Comments thread (general comments on the commit)
- [ ] Sessions that produced this commit (back-link to session)
- [ ] "Mentioned in PRs/Issues" cross-reference

### 3.5 `/musehub/ui/{owner}/{repo}/compare/{base}...{head}` — Multi-Diff

**Current:** Radar chart, piano roll, audio A/B.

**Missing / Wanted:**
- [ ] Similarity score percentage badge
- [ ] Emotion delta visualization (before/after radar)
- [ ] Changed instrument list with diff stats per instrument
- [ ] Merge/PR creation button from this view
- [ ] Note-level diff table (added/removed/changed notes)
- [ ] CC event diff (expression, modulation, sustain changes)
- [ ] Tempo change indicator
- [ ] Key change indicator
- [ ] Time signature change indicator
- [ ] `muse blame` panel showing per-note authorship in the diff
- [ ] Export diff as JSON button (for agents)
- [ ] "Open in DAW" button to load both refs for A/B comparison

### 3.6 `/musehub/ui/{owner}/{repo}/pulls` — PR List

**Current:** PR list with state filter.

**Missing / Wanted:**
- [ ] Filter by: Open / Merged / Closed / Draft
- [ ] Filter by: author, assignee, label, milestone
- [ ] Sort by: newest, oldest, most commented, recently updated
- [ ] Search PRs by title / body
- [ ] Assignee avatars on card
- [ ] Label chips on card
- [ ] Milestone indicator on card
- [ ] Comment count badge
- [ ] Reaction count badge
- [ ] Approval status (approved / changes requested / pending)
- [ ] CI/checks status badge
- [ ] "Draft PR" indicator
- [ ] Bulk actions (close multiple, assign milestone)

### 3.7 `/musehub/ui/{owner}/{repo}/pulls/{pr_id}` — PR Detail

**Current:** Diff, merge button, PR comments.

**Missing / Wanted:**
- [ ] PR description (markdown rendered)
- [ ] Labels
- [ ] Milestone link
- [ ] Assignees
- [ ] Reviewers + approval status per reviewer
- [ ] Linked issues (closes #N cross-reference)
- [ ] Check/CI status panel
- [ ] Conversation timeline (comments, reviews, events interspersed)
- [ ] Line-level (beat-level) review comments with threading
- [ ] Review summary ("Changes requested by X", "Approved by Y")
- [ ] Diff: note-level additions/deletions colour-coded
- [ ] Diff: CC event changes highlighted
- [ ] `muse emotion-diff` panel inline on PR
- [ ] Audio A/B player (base vs head) inline
- [ ] Merge options: merge commit / squash / rebase
- [ ] Auto-merge checkbox (merge when all checks pass)
- [ ] "Close and delete branch" button after merge
- [ ] Reopen PR button
- [ ] Edit PR title/body
- [ ] @mention notifications
- [ ] Reactions on PR body and on individual comments

### 3.8 `/musehub/ui/{owner}/{repo}/issues` — Issue List

**Current:** Issue list with open/closed filter.

**Missing / Wanted:**
- [ ] Filter by label
- [ ] Filter by milestone
- [ ] Filter by assignee
- [ ] Filter by author
- [ ] Search issues by title / body
- [ ] Sort by: newest, oldest, most commented, most reactions, recently updated
- [ ] Milestone progress bars in sidebar
- [ ] Label legend sidebar
- [ ] Bulk assign / bulk label / bulk close
- [ ] "No milestone" filter
- [ ] Issue templates (pre-filled forms for bug, feature, musical feedback)
- [ ] Duplicate detection (show similar open issues)
- [ ] Sub-issues / task lists (markdown checkboxes tracked as tasks)
- [ ] Lock/unlock issue button

### 3.9 `/musehub/ui/{owner}/{repo}/issues/{number}` — Issue Detail

**Current:** Issue body, close button.

**Missing / Wanted:**
- [ ] Markdown rendered body
- [ ] Labels display
- [ ] Milestone link
- [ ] Assignees
- [ ] Comments thread with full markdown
- [ ] Reactions on issue body and comments
- [ ] Threaded replies on comments
- [ ] Edit comment / delete comment
- [ ] Cross-references (linked PRs, linked commits)
- [ ] Timeline (comment/event interspersed: "closed by PR #5", "label added")
- [ ] Lock issue
- [ ] Pin issue to top of list
- [ ] Convert to PR
- [ ] @mention with notification
- [ ] Subscribe / unsubscribe to notifications

### 3.10 `/musehub/ui/{owner}/{repo}/milestones` — Milestones ❌ MISSING

**Wanted:**
- [ ] Milestone list with title, due date, open/closed issue count, progress bar
- [ ] Filter: open / closed
- [ ] Sort: due date, completeness, title
- [ ] Create milestone form
- [ ] Edit / delete milestone
- [ ] Per-milestone: link to filtered issue list

### 3.11 `/musehub/ui/{owner}/{repo}/releases` — Release List

**Current:** Release list with tag, title, commit link.

**Missing / Wanted:**
- [ ] Release description (markdown rendered)
- [ ] Embedded audio player per release
- [ ] Download stats per release
- [ ] Pre-release / latest badge
- [ ] Compare with previous release link
- [ ] Verify GPG signature badge
- [ ] RSS feed link for releases
- [ ] "Subscribe to releases" button (watch notifications)
- [ ] Draft release creation form
- [ ] Release assets panel (MIDI bundle, stems, MusicXML, MP3, metadata.json)

### 3.12 `/musehub/ui/{owner}/{repo}/sessions` — Recording Sessions

**Current:** Session log list.

**Missing / Wanted:**
- [ ] Session duration display
- [ ] Participants avatars
- [ ] Linked commits from session
- [ ] Session intent / notes rendered as markdown
- [ ] Timeline of events within session
- [ ] Filter by: participant, date range, active/ended
- [ ] Export session notes

### 3.13 `/musehub/ui/{owner}/{repo}/analysis/{ref}` — Analysis Dashboard

**Current:** 10-dimension panel with links to sub-pages.

**Missing / Wanted:**
- [ ] Historical trend charts (analysis over commit history)
- [ ] Compare with another ref inline
- [ ] "Analysis changed since last commit" delta indicators
- [ ] Export analysis as JSON
- [ ] Share analysis card (social preview)
- [ ] Annotation layer (add notes to analysis — saved as commit metadata)
- [ ] `muse similarity` score vs parent commit
- [ ] Harmonic analysis sub-page (`/analysis/{ref}/harmony`)
- [ ] Emotion-diff vs parent (`/analysis/{ref}/emotion-diff`)
- [ ] Recall search panel (`/analysis/{ref}/recall`)

### 3.14 `/musehub/ui/{owner}/{repo}/blame/{ref}/{path}` — Blame ❌ MISSING

**Wanted:**
- [ ] Per-note row: author avatar + username + commit hash + message
- [ ] Colour-coded by author
- [ ] Click on row → go to commit
- [ ] Filter by instrument/track
- [ ] Hover tooltip: full commit info
- [ ] Beat-range selector (zoom in on a region)

### 3.15 `/musehub/ui/{owner}/{repo}/forks` — Fork Network ❌ MISSING

**Wanted:**
- [ ] Fork tree visualization (original + fork children + their forks)
- [ ] Each node: owner avatar, star count, last updated, divergence score
- [ ] Filter: only show "active" forks (committed in last 90 days)
- [ ] "Compare my fork" shortcut
- [ ] "Contribute upstream" → create PR shortcut

### 3.16 `/musehub/ui/users/{username}` — User Profile

**Current:** Overview / Repositories / Starred / Watching tabs.

**Missing / Wanted:**
- [ ] Contribution graph (GitHub-style heatmap of commit activity)
- [ ] Pinned repos section (up to 6, from `musehub_profiles.pinned_repo_ids`)
- [ ] Bio + avatar + location + links
- [ ] Followers / following counts with links to lists
- [ ] Activity feed tab (recent commits, PRs, issues, stars)
- [ ] Achievement badges (e.g. "100 commits", "First fork", "Genre Pioneer")
- [ ] Organisations / collaborations section
- [ ] Sponsor / support link
- [ ] Follow / Unfollow button
- [ ] Block user button
- [ ] Recent releases section

### 3.17 `/musehub/ui/notifications` — Notification Inbox ❌ MISSING

**Wanted:**
- [ ] Grouped by repo
- [ ] Filter: unread / all / participating / mentioned
- [ ] Type icons (PR, issue, commit, review, mention)
- [ ] Mark as read individually
- [ ] Mark all read button
- [ ] Mute thread button
- [ ] Notification settings link

### 3.18 `/musehub/ui/{owner}/{repo}/insights` — Repo Analytics

**Current:** View counts, download events, star history.

**Missing / Wanted:**
- [ ] Commit frequency chart (daily/weekly/monthly toggle)
- [ ] Top contributors leaderboard
- [ ] Issues opened/closed trend
- [ ] PR merge rate
- [ ] Fork growth chart
- [ ] Watcher count history
- [ ] Most-referenced commits
- [ ] Most-downloaded releases
- [ ] Geographic origin of listeners (if available)
- [ ] Traffic sources (referrers)
- [ ] Instrument popularity over time (which instruments used most across commits)
- [ ] Tempo/key distribution histograms across commit history
- [ ] Average session duration trend

### 3.19 oEmbed / Embed Player

**Current:** Basic iframe embed exists.

**Missing / Wanted:**
- [ ] oEmbed endpoint returning rich metadata (title, author, thumbnail, duration)
- [ ] Open Graph `<meta>` tags on all repo/commit/release pages
- [ ] Twitter/X card metadata
- [ ] Spotify-style "Now Playing" card (shareable static image)
- [ ] Waveform thumbnail generation from render_job assets

---

## 4. SEED DATA BLUEPRINT

### 4.1 Real Creative Commons MIDI Artists

The following are public domain or Creative Commons licensed and suitable for attribution in seed data:

| Artist | Era / Genre | CC License | Source | Key Characteristics |
|---|---|---|---|---|
| **J.S. Bach** | Baroque, Classical | Public Domain | piano-midi.de, Mutopia | Complex counterpoint, fugues, all 24 keys, CC voicings |
| **Ludwig van Beethoven** | Classical, Romantic | Public Domain | piano-midi.de | Wide dynamic range, dramatic velocity changes, complex pedal |
| **Frédéric Chopin** | Romantic | Public Domain | piano-midi.de | Rubato, expressive CC1 (mod wheel), dense chord voicings |
| **Claude Debussy** | Impressionist | Public Domain | various | Whole tone scales, lush pedal, free meter, color harmony |
| **Scott Joplin** | Ragtime | Public Domain | multiple | Syncopated rhythm, ragtime groove, steady bass + ornate RH |
| **Kevin MacLeod** | Modern / Multi-genre | CC BY 4.0 | incompetech.com | Wide variety: cinematic, jazz, ambient, uptempo; MIDI available |
| **Kai Engel** | Ambient / Modern Classical | CC BY 4.0 | Free Music Archive | Long-form ambient, subtle dynamics, quiet velocity curves |
| **Broke for Free** | Electronic / Lo-fi | CC BY | Free Music Archive | Electronic textures, drum machine patterns, synth basslines |
| **Brad Sucks** | Indie Rock | CC BY | braadsucks.net | Guitar-driven, power chords, verse/chorus form, rock drumming |
| **Chris Zabriskie** | Ambient / Cinematic | CC BY 4.0 | chriszabriskie.com | Cinematic pads, slow evolution, minimal note changes |

### 4.2 Seed Users

| Username | Real-world Archetype | Role in Seed |
|---|---|---|
| `bach` | J.S. Bach (historical CC) | Source of classical repos |
| `chopin` | Frédéric Chopin (historical CC) | Source of romantic piano repos |
| `scott_joplin` | Scott Joplin (historical CC) | Source of ragtime repos |
| `kevin_macleod` | Kevin MacLeod (modern CC) | Multi-genre repos |
| `gabriel` | Primary active user | Forks, PRs, issues, collaborations |
| `sofia` | Active collaborator | Co-author on merge commits |
| `marcus` | EDM producer archetype | Electronic repos, forks Bach |
| `yuki` | Classical scholar | Studies Bach, opens analysis issues |
| `aaliya` | Jazz fusion artist | Forks Chopin, adds jazz voicings |
| `chen` | Film composer | Uses MacLeod as base, adds arrangements |
| `fatou` | Afrobeats producer | Cross-genre forks, unique rhythmic commits |
| `pierre` | French academic | Analysis-heavy commits, theory annotations |

### 4.3 Seed Repositories

| Repo | Owner | Genre | Description | Branches |
|---|---|---|---|---|
| `well-tempered-clavier` | `bach` | Baroque / Classical | All 48 preludes + fugues in all 24 keys | main, prelude-bk1, fugue-bk1, prelude-bk2, fugue-bk2 |
| `goldberg-variations` | `bach` | Baroque | 30 variations + aria | main, aria-only, variation-13-experimental |
| `nocturnes` | `chopin` | Romantic Piano | 21 nocturnes | main, op9, op15, op27, extended |
| `maple-leaf-rag` | `scott_joplin` | Ragtime | Maple Leaf Rag + variations | main, slow-version, marcus-edm-remix |
| `cinematic-strings` | `kevin_macleod` | Cinematic | Orchestral string pieces | main, orchestral, stripped-piano |
| `ambient-textures` | `kai_engel` | Ambient | Long-form ambient works | main, v1, v2-extended |
| `gabriel-neo-baroque` | `gabriel` | Cross-genre | Bach WTC fork with modern production | main, experiment/jazz-voicings, experiment/edm-bassline |
| `aaliya-jazz-chopin` | `aaliya` | Jazz Fusion | Chopin nocturnes fork with jazz reharmonisation | main, reharmonized, trio-arrangement |
| `marcus-ragtime-edm` | `marcus` | Electronic | Joplin fork with 808 drums + synth bass | main, trap-version, house-version |
| `chen-film-score` | `chen` | Cinematic | MacLeod fork adapted for film scenes | main, act1, act2, act3 |
| `fatou-polyrhythm` | `fatou` | Afrobeats | Original polyrhythmic compositions | main, 7-over-4, 5-over-3-experiment |
| `community-collab` | `gabriel` | Multi-genre | Open collaboration repo, all users contribute | main, sofias-counterpoint, yukis-ornaments, pierres-analysis |

### 4.4 Commit Data Requirements

Per repo, generate commits exercising:
- [ ] Simple single-instrument changes (velocity edits, note moves)
- [ ] Multi-instrument commits (bass + keys + drums changed together)
- [ ] Commits with `tempo_bpm` metadata annotation
- [ ] Commits with `key` metadata annotation  
- [ ] Commits with `emotion:*` tags (emotion:melancholic, emotion:joyful, emotion:tense, etc.)
- [ ] Commits with `stage:*` tags (stage:rough-mix, stage:arrangement, stage:production, stage:mastering)
- [ ] Commits with `ref:*` tags (ref:bach, ref:coltrane, ref:daft-punk)
- [ ] Commits with CC event data (CC1 modulation, CC7 volume, CC11 expression, CC64 sustain, CC91 reverb)
- [ ] Commits with full pitch_bends data
- [ ] Commits with aftertouch data
- [ ] Merge commits with two parents (cross-genre merges that should show conflicts)
- [ ] Empty commits (muse commit --allow-empty for session markers)
- [ ] Amend commits (shows --amend in history)
- [ ] Commits that revert previous commits
- [ ] Cherry-pick commits (applied from another branch)

Minimum: **50 commits per repo**, **12 repos** = **600+ commits total**

### 4.5 Branch Data Requirements

Per repo:
- [ ] `main` (default)
- [ ] At least 2 feature branches (active/merged)
- [ ] At least 1 abandoned branch (closed PR)
- [ ] At least 1 long-running branch with many commits (shows realistic divergence)
- [ ] At least 1 branch created from a tag (hotfix-like)

### 4.6 Muse CLI Data (Currently Unseeded)

#### muse_objects
- [ ] 50+ unique content-addressed blobs (MIDI file bytes, sha256-keyed)
- [ ] Objects with varying sizes (small loops 2KB → large orchestral pieces 200KB)
- [ ] Deduplicated objects (same MIDI file on two branches → same object_id)

#### muse_snapshots
- [ ] One snapshot per commit (manifest: `{path: object_id}`)
- [ ] Snapshots with 1 file (single instrument)
- [ ] Snapshots with 8-16 files (full ensemble)
- [ ] Snapshots with nested paths (e.g. `strings/violin.mid`, `brass/trumpet.mid`)

#### muse_commits
- [ ] Single-parent commits (linear history)
- [ ] Merge commits with `parent2_commit_id` populated
- [ ] Commits with `metadata.tempo_bpm` set
- [ ] Commits with `metadata.key` set
- [ ] Commits with `metadata.time_signature` set (future extension point)
- [ ] Commits by multiple authors on the same repo

#### muse_tags
- [ ] `emotion:melancholic`, `emotion:joyful`, `emotion:tense`, `emotion:serene`, `emotion:triumphant`, `emotion:mysterious`, `emotion:playful`
- [ ] `stage:sketch`, `stage:rough-mix`, `stage:arrangement`, `stage:production`, `stage:mixing`, `stage:mastering`, `stage:released`
- [ ] `key:C`, `key:Am`, `key:G`, `key:Em`, `key:Bb`, `key:F#`, `key:Db`, `key:Abm`
- [ ] `tempo:60bpm`, `tempo:80bpm`, `tempo:120bpm`, `tempo:140bpm`, `tempo:160bpm`
- [ ] `ref:bach`, `ref:chopin`, `ref:debussy`, `ref:coltrane`, `ref:daft-punk`, `ref:beethoven`
- [ ] `genre:baroque`, `genre:romantic`, `genre:ragtime`, `genre:edm`, `genre:ambient`, `genre:jazz`, `genre:afrobeats`
- [ ] Free-form: `needs-work`, `favourite`, `breakout`, `experimental`, `send-to-band`

#### muse_variations (DAW-level variation history)
- [ ] 20+ variations per active repo (accepted + discarded + pending)
- [ ] Variations with multiple phrases each
- [ ] Phrases with note_changes (add, remove, modify)
- [ ] Variations showing lineage (parent → child chain)
- [ ] Merge-type variations (parent2_variation_id set)
- [ ] Variations with CC event data in phrases
- [ ] Variations with pitch_bend data in phrases
- [ ] Variations with aftertouch data in phrases

### 4.7 MuseHub Social Data (10x Current Scale)

#### musehub_issues
Minimum **15 issues per repo** with:
- [ ] Mix of open / closed / locked
- [ ] Issues with labels: `bug`, `enhancement`, `question`, `analysis`, `merge-conflict`, `needs-arrangement`, `help-wanted`, `good-first-issue`, `musical-theory`, `performance`
- [ ] Issues assigned to multiple users
- [ ] Issues linked to milestones
- [ ] Issues with sub-tasks (markdown checkboxes in body)
- [ ] Cross-repo references (`owner/repo#N`)
- [ ] Issues closed by PR ("Closes #N" in PR body)

#### musehub_issue_comments
Minimum **5 comments per issue**:
- [ ] Comments with markdown formatting
- [ ] Comments with code blocks (MIDI snippet representations)
- [ ] Comments with musical notation references
- [ ] Threaded replies (`parent_id` set)
- [ ] Comments with @mentions
- [ ] Comments with reactions

#### musehub_milestones
Per repo:
- [ ] 3-5 milestones (v0.1, v0.2, v1.0, "Bach Complete", "EDM Edition", etc.)
- [ ] Mix of open / closed
- [ ] With and without `due_on` dates
- [ ] Linked to multiple issues

#### musehub_pull_requests
Minimum **8 PRs per repo**:
- [ ] Open PRs (awaiting review)
- [ ] Merged PRs (`merged_at` set, `merge_commit_id` set)
- [ ] Closed PRs (rejected without merge)
- [ ] Cross-genre "merge conflict" PRs (EDM bassline into classical piece)
- [ ] PRs with detailed musical diff descriptions
- [ ] PRs with linked issues (Closes #N)
- [ ] PRs with multiple review comments

#### musehub_pr_comments
Per PR, 3-8 inline review comments:
- [ ] General comments (`target_type: "general"`)
- [ ] Track-specific comments (`target_type: "track"`, `target_track: "piano"`)
- [ ] Region comments with beat range (`target_type: "region"`, `target_beat_start`, `target_beat_end`)
- [ ] Note-specific comments (`target_type: "note"`, `target_note_pitch: 60`)
- [ ] Threaded replies (`parent_comment_id` set)

#### musehub_releases
Per repo, 3-5 releases:
- [ ] Semantic versions: v0.1.0, v0.2.0, v1.0.0
- [ ] Release notes with markdown (what changed musically)
- [ ] `download_urls` with all package types: `midi_bundle`, `stems`, `mp3`, `musicxml`, `metadata`
- [ ] Pre-release and latest badges
- [ ] Releases linked to commits

#### musehub_sessions
Per repo, 5-10 sessions:
- [ ] Solo sessions (1 participant)
- [ ] Duo sessions (2 participants)
- [ ] Full-band sessions (4-6 participants)
- [ ] Sessions with `intent` (what the session was trying to achieve)
- [ ] Sessions with `notes` (what happened, discoveries, blockers)
- [ ] Sessions linked to commits (commits made during that session)
- [ ] Active sessions (1 per repo marked `is_active: true`)
- [ ] Sessions at different locations (home studio, studio, remote collaboration)

#### musehub_webhooks
Per repo, 1-3 webhooks:
- [ ] Webhook for `push` events (CI simulation)
- [ ] Webhook for `pull_request` events
- [ ] Webhook for `release` events (notify downstream consumers)
- [ ] Fernet-encrypted secrets
- [ ] Mix of active / inactive

#### musehub_webhook_deliveries
Per webhook, 5-20 deliveries:
- [ ] Successful deliveries (response_status: 200)
- [ ] Failed deliveries (response_status: 500, 404, 0 for timeout)
- [ ] Multiple retry attempts (`attempt`: 1, 2, 3)

#### musehub_render_jobs
Per repo, 5-15 render jobs:
- [ ] Completed jobs (status: "done", mp3_object_ids populated)
- [ ] Failed jobs (status: "failed", error_message set)
- [ ] Pending jobs (status: "pending")
- [ ] Jobs with multiple MIDI files (midi_count > 1)

#### musehub_events
Per repo, 20+ activity events:
- [ ] `push` events
- [ ] `pull_request.opened`, `pull_request.merged`, `pull_request.closed`
- [ ] `issue.opened`, `issue.closed`
- [ ] `release.published`
- [ ] `star` events (when users star)
- [ ] `fork` events
- [ ] `member.added` events (collaborator added)
- [ ] `session.started`, `session.ended`

#### Social Tables
- [ ] **musehub_stars**: Every user stars 3-8 repos (not their own)
- [ ] **musehub_watches**: Every user watches 4-10 repos
- [ ] **musehub_follows**: Rich follow graph (each user follows 3-6 others)
- [ ] **musehub_forks**: 2-4 forks per public repo, cross-genre where possible
- [ ] **musehub_comments**: 3-8 comments per commit/release/PR
- [ ] **musehub_reactions**: Full emoji set on commits, issues, comments (👍 ❤️ 🎵 🔥 🎹 👏 🤔 😢)
- [ ] **musehub_notifications**: 10-20 unread notifications per user
- [ ] **musehub_view_events**: 30-100 view events per repo (simulates real traffic)
- [ ] **musehub_download_events**: 5-20 download events per release

### 4.8 Dramatic Narrative Scenarios (for Rich Seed Data)

The seed data should tell actual stories:

1. **"The Bach Remix War"** — `marcus` forks `bach/well-tempered-clavier`, adds trap drums and 808 bass, opens PR back to `bach`. `yuki` leaves detailed analysis comments about harmonic integrity. `bach` (auto) closes the PR. Heated issue thread ensues. Eventually a compromise branch emerges.

2. **"Chopin Meets Coltrane"** — `aaliya` forks `chopin/nocturnes`, reharmonises Nocturne Op.9 No.2 with Coltrane changes. 3-way merge conflict when `gabriel` also forks and adds jazz voicings. Beautiful merge commit resolves the conflict.

3. **"The Ragtime EDM Collab"** — `marcus` and `fatou` open a collaborative session on `marcus/ragtime-edm`. 8-commit session, 3 participants, ends with a release "v1.0.0 - Maple Leaf Drops".

4. **"Community Collab Chaos"** — `gabriel/community-collab` has all 10 users contributing. Multiple open PRs from different users, some conflicting, some cleanly merged. An open issue "Resolve the key signature — are we in G major or E minor?" with 20+ comments and a heated debate.

5. **"The Goldberg Milestone"** — `bach/goldberg-variations` has milestone "All 30 Variations Complete". Issues track each variation as a task. 28/30 closed. 2 still open and hotly debated.

---

## 5. GITHUB ISSUE BACKLOG

> Each item below should become one GitHub issue with appropriate labels.
> Labels: `feature`, `missing-url`, `seed-data`, `analysis`, `ui`, `api`, `muse-command`

### GROUP A — Missing MuseHub URLs (UI Implementation)

- [ ] **A-01**: `GET /{owner}/{repo}/blame/{ref}/{path}` — Implement muse blame UI page with per-note authorship visualization
- [ ] **A-02**: `GET /{owner}/{repo}/milestones` — Milestone list page with progress bars and issue linking
- [ ] **A-03**: `GET /{owner}/{repo}/milestones/{number}` — Milestone detail page with filtered issue list
- [ ] **A-04**: `GET /{owner}/{repo}/labels` — Label management page (create, edit, delete, colour picker)
- [ ] **A-05**: `GET /{owner}/{repo}/stash` — Stash list page with apply/pop/drop actions
- [ ] **A-06**: `GET /{owner}/{repo}/forks` — Fork network visualization page
- [ ] **A-07**: `GET /{owner}/{repo}/settings` — Repo settings page (visibility, description, tags, danger zone)
- [ ] **A-08**: `GET /{owner}/{repo}/webhooks` — Webhook management UI (list, create, test, delete)
- [ ] **A-09**: `GET /{owner}/{repo}/similarity/{base}...{head}` — Musical similarity score page
- [ ] **A-10**: `GET /{owner}/{repo}/emotion-diff/{base}...{head}` — Emotional delta visualization page
- [ ] **A-11**: `GET /{owner}/{repo}/analysis/{ref}/harmony` — Full harmonic analysis sub-page (Roman numerals, cadences)
- [ ] **A-12**: `GET /{owner}/{repo}/recall` — Semantic memory search over commit history
- [ ] **A-13**: `GET /notifications` — Notification inbox page with group/filter/mark-read
- [ ] **A-14**: `GET /users/{username}/followers` — Follower list page
- [ ] **A-15**: `GET /users/{username}/following` — Following list page
- [ ] **A-16**: `GET /topics/{tag}` — Topic browse page (repos grouped by tag)
- [ ] **A-17**: `GET /new` — New repo wizard UI

### GROUP B — Missing API Endpoints

- [ ] **B-01**: `GET/POST/PATCH/DELETE /api/v1/musehub/repos/{id}/milestones/{n}` — Full milestone CRUD API
- [ ] **B-02**: `GET/POST /api/v1/musehub/repos/{id}/labels` + `PATCH /issues/{n}/labels` — Label management API
- [ ] **B-03**: `GET /api/v1/musehub/repos/{id}/analysis/{ref}/harmony` — Harmonic analysis endpoint
- [ ] **B-04**: `GET /api/v1/musehub/repos/{id}/analysis/{ref}/similarity?compare={ref2}` — Similarity score endpoint
- [ ] **B-05**: `GET /api/v1/musehub/repos/{id}/analysis/{ref}/emotion-diff?base={ref}` — Emotion delta endpoint
- [ ] **B-06**: `GET /api/v1/musehub/repos/{id}/analysis/{ref}/recall?q={query}` — Semantic recall endpoint
- [ ] **B-07**: `GET/PATCH /api/v1/musehub/repos/{id}/settings` — Repo settings read/write
- [ ] **B-08**: `DELETE /api/v1/musehub/repos/{id}` — Repo deletion with cascade
- [ ] **B-09**: `POST /api/v1/musehub/repos/{id}/transfer` — Transfer repo ownership
- [ ] **B-10**: `GET/POST/DELETE /api/v1/musehub/repos/{id}/collaborators` — Collaborator management
- [ ] **B-11**: `GET /api/v1/musehub/repos/{id}/blame/{ref}` — Blame output as JSON for agents
- [ ] **B-12**: `GET/POST/DELETE /api/v1/musehub/repos/{id}/stash` + pop endpoint — Stash API
- [ ] **B-13**: `GET /api/v1/musehub/users/{username}/activity` — Public activity feed for a user
- [ ] **B-14**: `GET /api/v1/musehub/topics/{tag}/repos` — Repos by topic tag

### GROUP C — Per-Page UI Enhancements

- [ ] **C-01**: Explore page — Add filter sidebar (genre, key, tempo range, license, instrument)
- [ ] **C-02**: Explore page — Inline audio preview on repo card hover
- [ ] **C-03**: Repo landing — Contributor avatar grid with commit counts
- [ ] **C-04**: Repo landing — Activity contribution heatmap graph
- [ ] **C-05**: Repo landing — Instrument breakdown bar chart
- [ ] **C-06**: Repo landing — Clone URL widget with copy button
- [ ] **C-07**: Repo landing — README markdown rendering
- [ ] **C-08**: Commits list — Filter by author, date range, tag annotation
- [ ] **C-09**: Commits list — Per-commit instrument change badges + tempo/key metadata chips
- [ ] **C-10**: Commits list — "Compare" multi-select mode for picking two commits to diff
- [ ] **C-11**: Commit detail — Inline audio player (render_preview for the commit)
- [ ] **C-12**: Commit detail — Reactions and comments thread on commit
- [ ] **C-13**: PR list — Filter by assignee, label, milestone; sort options
- [ ] **C-14**: PR detail — Approval status per reviewer; review request workflow
- [ ] **C-15**: PR detail — Audio A/B inline player (base vs head)
- [ ] **C-16**: PR detail — Merge options: merge commit / squash / rebase selector
- [ ] **C-17**: Issue list — Filter by label, milestone, assignee; bulk actions
- [ ] **C-18**: Issue detail — Reactions on body and comments; threaded replies
- [ ] **C-19**: Issue detail — Timeline view (events + comments interspersed)
- [ ] **C-20**: Release detail — Embedded audio player + download stats
- [ ] **C-21**: Releases — RSS feed link + subscribe to releases button
- [ ] **C-22**: Analysis dashboard — Historical trend charts over commit history
- [ ] **C-23**: Analysis dashboard — "Analysis delta since parent commit" indicators
- [ ] **C-24**: User profile — Contribution heatmap graph (GitHub-style)
- [ ] **C-25**: User profile — Achievement badges system
- [ ] **C-26**: Insights — Full analytics suite (commit frequency, PR merge rate, fork growth)

### GROUP D — Muse Command UI Exposure (Missing)

- [ ] **D-01**: `muse amend` — UI button on commit detail page: "Amend last commit"
- [ ] **D-02**: `muse cherry-pick` — UI button on commit detail: "Apply to branch…"
- [ ] **D-03**: `muse revert` — UI button on commit detail: "Revert this commit"
- [ ] **D-04**: `muse rebase -i` — Interactive rebase UI (drag-to-reorder commits)
- [ ] **D-05**: `muse stash` — Stash panel in sidebar: push, list, apply, drop
- [ ] **D-06**: `muse bisect` — Bisect wizard UI: "Find the bad commit" binary search workflow
- [ ] **D-07**: `muse transpose` — UI control on commit/branch: "Transpose to key…"
- [ ] **D-08**: `muse tempo-scale` — UI control: "Time-stretch by factor…"
- [ ] **D-09**: `muse humanize` — UI toggle on commit: "Apply humanization"
- [ ] **D-10**: `muse validate` — UI badge on commit: "MIDI structure valid ✓"
- [ ] **D-11**: `muse import` — Repo UI: "Import MIDI file" → creates commit
- [ ] **D-12**: `muse describe` — Standalone page at `/{owner}/{repo}/describe/{ref}` with AI prose summary
- [ ] **D-13**: `muse recall` — Search UI: "Search musical memory" (semantic search over history)
- [ ] **D-14**: `muse resolve` — Merge conflict resolution UI with ours/theirs/manual options
- [ ] **D-15**: `muse worktree` — Worktree list UI: see all active worktrees for a repo
- [ ] **D-16**: `muse similarity` — Show similarity score on PR detail page (head vs base)
- [ ] **D-17**: `muse emotion-diff` — Show emotional delta on PR detail page inline

### GROUP E — Seed Data

- [ ] **E-01**: Implement muse_objects / muse_snapshots / muse_commits / muse_tags seed data (all currently zero)
- [ ] **E-02**: Implement muse_variations / muse_phrases / muse_note_changes seed data (all currently zero)
- [ ] **E-03**: Create 12 artist/user profiles with bio, avatar, pinned repos (Bach, Chopin, Joplin, MacLeod, gabriel, sofia, marcus, yuki, aaliya, chen, fatou, pierre)
- [ ] **E-04**: Seed 12 repos across genres (well-tempered-clavier, nocturnes, maple-leaf-rag, etc.) with 50+ commits each
- [ ] **E-05**: Seed all muse_tags variants (emotion:*, stage:*, key:*, tempo:*, ref:*, genre:*)
- [ ] **E-06**: Seed milestones + milestone-linked issues for each repo (3-5 milestones per repo)
- [ ] **E-07**: Seed musehub_issue_comments (5+ per issue, with threading)
- [ ] **E-08**: Seed musehub_pr_comments (review comments with track/region/note targeting)
- [ ] **E-09**: Seed musehub_milestones + link to issues
- [ ] **E-10**: Seed musehub_webhooks + musehub_webhook_deliveries (success and failure cases)
- [ ] **E-11**: Seed musehub_render_jobs (pending, done, failed states)
- [ ] **E-12**: Seed musehub_events (push, PR, issue, release, star, fork, session events)
- [ ] **E-13**: Implement "Bach Remix War" narrative scenario (fork, PR, conflict, resolution)
- [ ] **E-14**: Implement "Chopin Meets Coltrane" narrative scenario (cross-genre 3-way merge)
- [ ] **E-15**: Implement "Ragtime EDM Collab" narrative scenario (multi-user session + release)
- [ ] **E-16**: Implement "Community Collab Chaos" narrative scenario (10 users, open PRs, debates)
- [ ] **E-17**: Implement "Goldberg Milestone" narrative scenario (milestone completion tracking)
- [ ] **E-18**: Seed CC MIDI content — download and embed real MIDI data from piano-midi.de for Bach/Chopin/Joplin pieces
- [ ] **E-19**: Seed full social graph — stars, watches, follows, forks, reactions (emojis including 🎵 🎹 🔥 👏)
- [ ] **E-20**: Seed notifications for all users (10-20 unread each, all types)

### GROUP F — Agent & Machine Readability

- [ ] **F-01**: All HTML pages must have `Accept: application/json` equivalent JSON endpoint — audit and add any missing
- [ ] **F-02**: Add `Link: rel=alternate type=application/json` headers on all UI pages
- [ ] **F-03**: oEmbed endpoint — return rich metadata (title, author, thumbnail URL, audio URL, duration, BPM, key)
- [ ] **F-04**: Open Graph + Twitter card meta tags on repo/commit/release/profile pages
- [ ] **F-05**: `GET /musehub/ui/{owner}/{repo}/analysis/{ref}` → machine-readable `analysis.json` schema at same path with `Accept: application/json`
- [ ] **F-06**: RSS/Atom feeds for: releases, commits, issues opened (per repo)
- [ ] **F-07**: `robots.txt` + `sitemap.xml` generation for public repos
- [ ] **F-08**: JSON-LD structured data (`schema.org/MusicComposition`) on repo landing pages
- [ ] **F-09**: Pagination via `Link: rel=next/prev` headers on all list endpoints (RFC 8288)
- [ ] **F-10**: Cursor-based pagination option alongside offset (better for agents with large datasets)

---

*Generated: 2026-03-01 | Version: 1.0 | Next review: after E-series issues are implemented*
