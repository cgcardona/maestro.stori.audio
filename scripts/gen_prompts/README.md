# Prompt-as-Code — Agent Prompt Generator

> **All commands run inside the maestro Docker container. Never on the host.**

The 12 agent prompt files (`.cursor/roles/*.md`, `.cursor/PARALLEL_*.md`) are
**generated** — never hand-edited. One config file (`config.yaml`) drives
everything: repo slug, phase label order, active codebase, GitHub label
definitions, and every value that varies between pipeline runs.

## Quick Start

```bash
# 1. Edit config or a template, then regenerate:
docker compose exec maestro python3 /app/scripts/gen_prompts/generate.py

# 2. Review diffs:
git diff .cursor/

# 3. Commit generated files:
git add .cursor/ scripts/gen_prompts/sync_labels.sh
git commit -m "chore: regenerate prompts"

# 4. Sync GitHub labels (only needed when labels section changed):
bash scripts/gen_prompts/sync_labels.sh
```

> **Why Docker?**
> The generator writes directly to `.cursor/` inside the container, which is
> bind-mounted from the host (`docker-compose.override.yml`). This guarantees
> the same Python version (3.11), the same Jinja2 version, and the same file
> permissions as the running pipeline. Running on the host bypasses all of
> that and risks subtle divergence.

## Directory Layout

```
scripts/gen_prompts/
  config.yaml                ← edit this to reconfigure a run
  generate.py                ← run this to regenerate .cursor/ files
  sync_labels.sh             ← auto-generated; run once to sync GitHub labels
  COGNITIVE_ARCHITECTURE_SPEC.md  ← spec for the cognitive architecture mixer
  cognitive_archetypes/      ← YAML component library for agent cognition
    personality/
    archetype/
    skill_domain/
    decision_framework/
  templates/
    roles/
      cto.md.j2
      engineering-manager.md.j2
      qa-manager.md.j2
      pr-reviewer.md.j2
      python-developer.md.j2
      coordinator.md.j2
      database-architect.md.j2
      muse-specialist.md.j2
    PARALLEL_BUGS_TO_ISSUES.md.j2
    PARALLEL_CONDUCTOR.md.j2
    PARALLEL_ISSUE_TO_PR.md.j2
    PARALLEL_PR_REVIEW.md.j2
```

## Config Variables

| Key | Purpose |
|-----|---------|
| `repo.gh_slug` | GitHub `org/repo` slug — used in `gh` CLI calls everywhere |
| `repo.name` | Repo name — used in worktree subfolder names |
| `pipeline.claim_label` | Label agents add to claim an issue (`agent:wip`) |
| `pipeline.max_pool_size` | Max concurrent leaf agents per VP run |
| `pipeline.phases` | **Ordered** list of phase labels — CTO iterates this strictly |
| `codebases.active` | Which codebase is being worked on right now |
| `codebases.<name>.container` | Docker container that runs mypy/tests for that codebase |
| `codebases.<name>.mypy` | Full mypy command for that codebase |
| `codebases.<name>.test_dir` | Directory containing tests |
| `codebases.<name>.label_prefix` | Issue label prefix (e.g. `agentception/`) |
| `labels.*` | Full label definitions — drives `sync_labels.sh` generation |

## Template Syntax

Templates are standard Jinja2 with one customisation: comment delimiters are
`{## ... ##}` instead of the default `{# ... #}`. This avoids conflicts with
shell array-length syntax (`${#ARRAY[@]}`) used throughout the prompt files.

Shell variables (`$HOME`, `$REPO`, `$WTNAME`, `${BATCH_ID:-none}`) are left
as-is — Jinja2 never touches bare `$` variables.

| Template variable | Expands to |
|-------------------|-----------|
| `{{ gh_repo }}` | `cgcardona/maestro` |
| `{{ claim_label }}` | `agent:wip` |
| `{{ phases_shell }}` | Shell `for label in phase-0 phase-1 ...; do` block |
| `{{ active_label_prefix }}` | `agentception/` |
| `{{ active_mypy }}` | Full mypy command for the active codebase |
| `{{ active_test_dir }}` | Test directory for the active codebase |
| `{{ active_container }}` | Docker container for the active codebase |

## Switching Projects

To move the pipeline from `agentception/*` work to `maestro/*` work:

```yaml
# config.yaml
pipeline:
  phases:
    - "maestro/0-foundation"
    - "maestro/1-features"
    # ...

codebases:
  active: "maestro"   # ← only change needed here
```

Then run the generator and commit. All 12 prompt files update atomically.

## Label Sync

`generate.py` always regenerates `sync_labels.sh`. Run it after any label
change to push definitions to GitHub:

```bash
bash scripts/gen_prompts/sync_labels.sh
```

The script is idempotent: it creates labels that don't exist and updates color
and description for labels that do.

## Adding a New Phase

1. Add the phase name to `pipeline.phases` in `config.yaml` (in order).
2. Add a matching entry to `labels.phases` with color and description.
3. Run the generator: `docker compose exec maestro python3 /app/scripts/gen_prompts/generate.py`
4. Run `bash scripts/gen_prompts/sync_labels.sh` to create the GitHub label.
5. Create GitHub issues with the new label.

## Cognitive Architecture Mixer

See `COGNITIVE_ARCHITECTURE_SPEC.md` for the full design. In short: the
`cognitive_archetypes/` directory holds YAML component definitions
(personality, archetype, skill domain, decision framework, etc.). The
engineering-manager selects and injects components per task, creating a
custom cognitive architecture for each leaf agent.

## Dependencies

`jinja2` and `pyyaml` are already installed in the `maestro` container
(transitive deps of FastAPI). No new pip packages needed.
