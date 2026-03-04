# Data Migration Strategy — ac_* Tables

**Decision: DISCARD**
**Status: Approved — recorded in PR closing issue #972**
**Author: AgentCeption engineering team**
**Date: 2026-03-04**

---

## Context

The Maestro PostgreSQL instance hosts two AgentCeption-owned tables:

| Table | Purpose |
|-------|---------|
| `ac_initiative_phases` | Phase dependency graph per initiative (DAG of phase labels and their `depends_on` relationships, declared at plan-creation time) |
| `ac_agent_runs` | Agent task lifecycle history (which agents ran which branches, status, attempt number, timing) |

When AgentCeption moves to its own dedicated Postgres instance (issue #965), these tables become stranded in the Maestro DB. This document records the decision about what happens to the existing rows.

---

## Options Evaluated

### Option A — Migrate

Use `pg_dump` to extract the two tables from Maestro's Postgres and `pg_restore` them into AgentCeption's new Postgres instance.

```bash
# Example — not the chosen path
pg_dump \
  --table=ac_initiative_phases \
  --table=ac_agent_runs \
  --format=custom \
  --no-owner \
  -f ac_tables_backup.dump \
  "$MAESTRO_DATABASE_URL"

pg_restore \
  --no-owner \
  --no-privileges \
  -d "$AGENTCEPTION_DATABASE_URL" \
  ac_tables_backup.dump
```

**Pros:** Preserves task run history for post-mortem analysis.
**Cons:** One-time manual operation; cross-service data transfer introduces migration risk; data has low ongoing value once AgentCeption is live on its own DB.

### Option B — Discard (chosen)

Accept data loss. Treat the existing rows as ephemeral operational metadata.

**Pros:** Zero migration complexity. No cross-service data transfer. Clean slate on the new DB.
**Cons:** Loss of historical task run records from the monorepo phase.

---

## Decision Rationale

**We choose Discard.**

1. **`ac_initiative_phases` is re-created automatically.** On the next Phase 1B planning run, AgentCeption rebuilds the phase dependency graph from the current `PlanSpec`. No row in this table is irreplaceable.

2. **`ac_agent_runs` is development-phase metadata.** The rows record which agents ran which branches during the monorepo phase. This is useful for debugging in development but has no production-critical function — no billing data, no user-facing content, no audit or compliance requirement.

3. **Historical analysis is better served by git.** Every agent action that matters is recorded in GitHub (commits, PRs, issue comments with agent fingerprints). The database records are a secondary index, not the source of truth.

4. **Migration cost exceeds value.** A `pg_dump | pg_restore` across two Postgres instances for non-critical data is disproportionate. The engineering time is better spent on the AgentCeption extraction itself (issue #965).

---

## Cleanup Steps

After AgentCeption's new Postgres is running and verified healthy (tracked in issue #966):

```sql
-- Run in the MAESTRO Postgres instance only.
-- AgentCeption has been fully migrated to its own DB at this point.
DROP TABLE IF EXISTS ac_initiative_phases CASCADE;
DROP TABLE IF EXISTS ac_agent_runs CASCADE;
-- Also drop remaining ac_* tables if no longer needed in Maestro:
DROP TABLE IF EXISTS ac_agent_events CASCADE;
DROP TABLE IF EXISTS ac_agent_messages CASCADE;
DROP TABLE IF EXISTS ac_pipeline_snapshots CASCADE;
DROP TABLE IF EXISTS ac_pull_requests CASCADE;
DROP TABLE IF EXISTS ac_issues CASCADE;
DROP TABLE IF EXISTS ac_role_versions CASCADE;
DROP TABLE IF EXISTS ac_waves CASCADE;
```

> **Important:** Run this only after confirming AgentCeption is operational on its own Postgres. Do not run it speculatively. Coordinate with issue #966 (cleanup runbook).

The corresponding Alembic migration files in `agentception/alembic/versions/` are annotated with `DEPRECATED` comments — see below.

---

## Alembic Annotation

The following migration files in `agentception/alembic/versions/` are annotated with:

```
# DEPRECATED: These tables are owned by cgcardona/agentception.
# Once AgentCeption runs on its own Postgres instance (issue #965), run
# the DROP TABLE cleanup in docs/migration.md (issue #966) and remove
# these migration files from the Maestro repo.
```

Files annotated:
- `0001_ac_initial_schema.py` — creates `ac_waves`, `ac_agent_runs`, `ac_issues`, `ac_pull_requests`, `ac_agent_messages`, `ac_role_versions`, `ac_pipeline_snapshots`
- `0003_ac_initiative_phases.py` — creates `ac_initiative_phases`

---

## Unblocks

This decision unblocks:

- **Issue #965** (AgentCeption database independence) — the migration strategy is now decided; the extraction can proceed.
- **Issue #966** (post-migration cleanup runbook) — the DROP TABLE SQL above is the cleanup runbook.
