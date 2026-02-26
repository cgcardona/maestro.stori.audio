# Stori Tour de Force

End-to-end integration harness that stress-tests and proves the integrated Maestro + Storpheus + MUSE system.

## What It Does

A single command that:

1. **Fetches** random prompts from the backend with JWT auth
2. **Composes** music through Maestro (COMPOSING mode → tracks/regions/MIDI)
3. **Captures** every log, contract payload, and performance metric
4. **Analyzes** MIDI quality (pitch entropy, rhythmic density, garbage detection)
5. **Commits** to MUSE version control
6. **Branches & merges** across multiple edit cycles
7. **Reports** with interactive HTML, plots, and MUSE graph visualization

## Quick Start (Docker — the only supported way)

The TDF runs **inside the `maestro` Docker container**. Never run it on the host.

### Prerequisites

1. Both containers built and running:

```bash
docker compose build maestro storpheus
docker compose up -d
```

2. JWT token set in `.env.tourdeforce` (copy from example if needed):

```bash
cp .env.tourdeforce.example .env.tourdeforce
# Edit .env.tourdeforce and paste your JWT
```

### Run the harness

All endpoint defaults are configured for the Docker environment — no URL overrides needed.

```bash
source .env.tourdeforce

docker compose exec -e STORI_JWT="$STORI_JWT" maestro \
  python -m stori_tourdeforce run --runs 5 --seed 1337 -v
```

### Retrieve artifacts to the host

Artifacts are written to the `/data` volume inside the container. To copy them out:

```bash
docker cp maestro-stori-app:/data/tdf ./artifacts
```

### Generate a report from existing artifacts

```bash
docker compose exec maestro \
  python -m stori_tourdeforce report --in /data/tdf
```

### Replay a specific run

```bash
docker compose exec maestro \
  python -m stori_tourdeforce replay --run-id r_000042 --in /data/tdf
```

## Networking Inside Docker

The TDF runs inside the `maestro` container. All endpoint defaults use Docker Compose service names, resolved via the `maestro-stori-net` network:

| Service | Default URL |
|---------|-------------|
| Maestro API | `http://maestro:10001` |
| Prompt endpoint | `http://maestro:10001/api/v1/prompts/random` |
| Storpheus | `http://storpheus:10002` |
| MUSE API | `http://maestro:10001/api/v1/muse` |

These are **not** the same as host-machine URLs. From your Mac, Maestro is at `http://localhost:10001` (or `http://maestro.local:10001` if you add `127.0.0.1 maestro.local` to `/etc/hosts`). Inside Docker, service names like `maestro` and `storpheus` are resolved by Docker DNS — `localhost` only reaches the current container.

## Output Directory

The `maestro` container runs as the `stori` user. The working directory `/app` is owned by root, so **you cannot write to `/app/artifacts/`** (you'll get `PermissionError`).

| Path | Writable | Persists across restarts | Use for |
|------|----------|--------------------------|---------|
| `/data/` | Yes | Yes (Docker volume `maestro-stori-data`) | TDF artifacts you want to keep |
| `/tmp/` | Yes | No (tmpfs, lost on restart) | Throwaway / debugging runs |

The default output path is `/data/tdf_<timestamp>`. No `--out` flag needed unless you want a specific name.

## CLI Reference

### `run` — Execute Tour de Force

| Flag | Default | Description |
|------|---------|-------------|
| `--jwt-env` | `STORI_JWT` | Env var containing JWT |
| `--prompt-endpoint` | `http://maestro:10001/api/v1/prompts/random` | Prompt fetch URL |
| `--maestro` | `http://maestro:10001/api/v1/maestro/stream` | Maestro stream URL |
| `--storpheus` | `http://storpheus:10002` | Storpheus base URL |
| `--muse-url` | `http://maestro:10001/api/v1/muse` | MUSE API base URL |
| `--runs` | `10` | Number of runs |
| `--seed` | `1337` | Random seed |
| `--concurrency` | `4` | Max parallel runs |
| `--out` | `/data/tdf_<timestamp>` | Output directory (persistent Docker volume) |
| `--quality-preset` | `balanced` | `fast` / `balanced` / `quality` |
| `--maestro-timeout` | `180` | Stream timeout (seconds) |
| `--storpheus-timeout` | `180` | Job timeout (seconds) |
| `--global-timeout` | `300` | Per-run timeout (seconds) |
| `-v` / `--verbose` | off | Verbose logging |

### `report` — Generate Report

```bash
docker compose exec maestro \
  python -m stori_tourdeforce report --in /data/tdf
```

### `replay` — Replay a Run

```bash
docker compose exec maestro \
  python -m stori_tourdeforce replay --run-id r_000042 --in /data/tdf
```

## Running Unit Tests

```bash
docker compose exec maestro pytest tests/test_tourdeforce/ -v
```

## Artifact Directory

```
/data/tdf/
  manifest.json          # Run summary + config
  config.json            # Full configuration
  runs.jsonl             # Per-run summaries
  events.jsonl           # Unified event stream
  metrics.jsonl          # Performance metrics
  tourdeforce.db         # SQLite analytics DB
  logs/
    client.log           # JSON-formatted client logs
  payloads/
    prompt_fetch/        # Raw prompt responses
    maestro_requests/    # Maestro request payloads
    maestro_sse/         # Raw + parsed SSE streams
    storpheus_requests/  # Storpheus generation requests
    storpheus_responses/ # Storpheus job results
  midi/
    run_r_000001/
      midi_summary.json  # MIDI quality metrics
  muse/
    graph.json           # MUSE commit DAG
    graph.txt            # ASCII graph
    graph_viz.json       # Nodes/edges for visualization
  report/
    report.html          # Hero HTML report
    report.md            # Markdown report
    plots/               # Static plot images
```

## Event Envelope

All events in `events.jsonl` follow a unified schema:

```json
{
  "ts": "2026-02-24T17:30:00.123Z",
  "run_id": "r_000042",
  "scenario": "compose->commit->edit->branch->merge",
  "component": "maestro|storpheus|muse|prompt_service|client",
  "event_type": "http_request|sse_event|tool_call|midi_metric|muse_commit|...",
  "trace_id": "t_abc123",
  "span_id": "s_def456",
  "parent_span_id": "s_parent",
  "severity": "INFO",
  "tags": {},
  "data": {}
}
```

## MIDI Quality Metrics

The analyzer computes:

- **Structure**: note count, track count, duration, tempo, polyphony
- **Musical plausibility**: pitch entropy, velocity distribution, rhythmic density
- **Harmonic coherence**: pitch class entropy, chord change rate
- **Humanization**: timing deviation from grid, velocity variance
- **Garbage checks**: zero-length notes, extreme pitches, note spam detection
- **Composite score**: 0-100 quality rating

## Determinism

- Prompt selection uses `stable_hash(sorted_ids, seed) % N`
- Seed propagated into Maestro and Storpheus where supported
- MUSE commits are content-addressed
- Full payload hashing (SHA-256) for reproducibility audit

## How to Extend

### Add a new scenario

Edit `stori_tourdeforce/scenarios.py` — define `EditStep` and `MergeStep` instances.

### Add a new metric

Add to `MidiMetrics` in `models.py`, compute in `analyzers/midi.py`, include in `_compute_quality_score`.

### Add a new client

Follow the pattern in `clients/` — accept config + collectors, emit events, persist payloads.

## MUSE VCS Permutation Coverage

The harness exercises **every** MUSE VCS primitive across four scenario types:

| Operation | Scenario(s) |
|-----------|-------------|
| commit (save + set HEAD) | All |
| branch (variation with parent) | Standard, Checkout Stress, Conflict Only |
| clean merge (disjoint regions) | Standard, Checkout Stress |
| conflict merge (overlapping notes → 409) | Standard, Conflict Only |
| checkout (force) | Standard, Checkout Stress, Conflict Only |
| checkout (non-force) | Checkout Stress |
| drift detection | Standard, Checkout Stress, Conflict Only |
| force recovery after drift | Standard, Checkout Stress, Conflict Only |
| graph export (ASCII + JSON) | All |

See `SCENARIOS.md` for detailed flow diagrams.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Name or service not known` | Endpoint URLs pointing at an external domain | Defaults are already correct for Docker; check you haven't overridden them |
| `PermissionError: 'artifacts'` | Writing to `/app/` which is root-owned | Don't pass `--out` (defaults to `/data/`), or use `--out /tmp/tdf` |
| `STORI_JWT not found` | JWT not passed into container | `source .env.tourdeforce && docker compose exec -e STORI_JWT="$STORI_JWT" maestro ...` |
| Storpheus timeout | HF Space cold start | Increase `--storpheus-timeout 300` and `--global-timeout 600` |
| Empty MIDI / 0 notes | HF Space down or rate-limited | Check `docker compose logs storpheus` and HF Space status |

## Dependencies

- `httpx` — async HTTP client (already in maestro container)
- `mido` — MIDI file parsing (already in maestro container)
- `matplotlib` — plots (optional, degrades gracefully)
