# Testing

Run tests, intent-based QA, and quick prompts in one place.

---

## Run tests

We run tests **in Docker** so everyone (Mac, Linux, CI) uses the same environment. That keeps results consistent and makes debugging contributor issues straightforward.

> **Always rebuild before testing.** The maestro container copies source code at build time — it does **not** mount a live volume. Any local edits to `app/` or `tests/` are invisible inside the container until you rebuild. Forgetting this is the #1 cause of "my tests aren't collected" or "the fix didn't take effect." **Rebuild first, always.**
>
> ```bash
> docker compose build maestro && docker compose up -d
> ```

**Type checking (mypy):** Config is in `pyproject.toml` under `[tool.mypy]`. Run against the app and tests from the container (with code and config mounted so you see current code):

```bash
docker compose run --no-deps -v "$(pwd)/app:/app/app:ro" -v "$(pwd)/tests:/app/tests:ro" -v "$(pwd)/pyproject.toml:/app/pyproject.toml:ro" maestro sh -c "cd /app && python -m mypy -p app && python -m mypy -p tests"
```

- Subsets: `python -m mypy -p app.models -p app.auth` (single package: `-p app.config`).
- CI runs `mypy -p app` and `mypy -p tests` before pytest.

**Plain tests (rebuild + run):**
```bash
docker compose build maestro && docker compose up -d
docker compose exec maestro pytest tests/ -v
```

**Tests with coverage (canonical command):**

```bash
docker compose build maestro && docker compose up -d
docker compose exec maestro sh -c "export COVERAGE_FILE=/tmp/.coverage && python -m coverage run -m pytest tests/ -v && python -m coverage report --fail-under=80 --show-missing"
```

- The coverage threshold is **80%** (`--fail-under=80`). The single source of truth is `pyproject.toml` → `[tool.coverage.report]` → `fail_under`. Use the same value when running locally or in CI.
- If you see `No module named coverage`, rebuild: `docker compose build maestro` then `docker compose up -d`.
- If you see `unable to open database file` for `.coverage`, the container user can't write to `/app`; the command uses `COVERAGE_FILE=/tmp/.coverage` so the data file lives in `/tmp`.

When CI is enabled, run the same coverage command so the build fails if coverage drops below the threshold in `pyproject.toml`. Also add secret scanning (e.g. Gitleaks) and optionally a dependency audit (e.g. `pip-audit`); see [security.md](security.md).

**Coverage target:** We aim for 80% over time. The report shows where to add tests: biggest gaps are often `maestro_handlers`, `executor`, `llm_client`, route handlers (maestro, conversations, MCP), and optional backends (Orpheus, HuggingFace, renderers). Raising coverage lets you bump `fail_under` in `pyproject.toml`.

---

## Orpheus Music Service tests

The Orpheus service (`orpheus-music/`) has its own test suite that runs independently inside its container. Like the maestro container, the orpheus container copies source at build time — rebuild before testing.

**Rebuild + run:**
```bash
docker compose build orpheus && docker compose up -d orpheus
docker compose exec orpheus sh -c "pytest test_*.py -v"
```

Tests cover: GM instrument resolution (`test_gm_resolution.py` — role → GM program → TMIDIX name, 56 tests), MIDI pipeline (`test_midi_pipeline.py` — seed MIDI, parse, tool call generation, fuzzy cache, 77 tests), API contracts (`test_endpoints.py` — all endpoints, 13 tests), cache system (LRU + TTL), generation policy (intent-to-controls mapping, token budget allocation), quality metrics, and job queue (submit/cancel/dedupe/cleanup).

---

## Run all tests (both services)

Rebuild both containers and run both test suites sequentially:

```bash
docker compose build maestro orpheus && docker compose up -d
docker compose exec maestro pytest tests/ -v
docker compose exec orpheus sh -c "pytest test_*.py -v"
```

---

## Intent-based testing

The backend routes prompts by intent; each intent has an allowed tool set. Use the table below for happy-path checks.

**Single-tool (force one):** CREATE_PROJECT, SET_TEMPO, SET_KEY, ADD_TRACK, SET_VOLUME, SET_PAN, MUTE_TRACK, SOLO_TRACK, PLAY, STOP, DELETE_REGION, MOVE_REGION, DUPLICATE_REGION, CLEAR_NOTES, etc.

| Example prompt | Expected tool(s) |
|----------------|------------------|
| "Create a new project 'Test' at 120 BPM" | `stori_create_project` |
| "Set tempo to 95 BPM" | `stori_set_tempo` |
| "Add a drum track" | `stori_add_midi_track` |
| "Mute the drums" | `stori_mute_track` |
| "Play" / "Stop" | `stori_play` / `stori_stop` |

**Multi-tool (e.g. GENERATE_MUSIC):** Track → region → generate → add notes.

| Example prompt | Expected (order) |
|----------------|------------------|
| "Generate a boom bap drum pattern, 4 bars at 95 BPM" | `stori_add_midi_track`, `stori_add_midi_region`, `stori_generate_drums`, `stori_add_notes` |
| "Add reverb to the piano track" | `stori_add_insert_effect` |

---

## Quick test prompts

Use these for smoke tests or demos:

- create a boom bap track at 85 bpm
- make a chill lo-fi beat at 75 bpm in A minor
- create a trap beat at 140 bpm
- add a reverb to the drums
- make the bass louder
- create a 4-bar drum loop at 95 bpm

Add your own for smoke tests and demos.

---

## Test coverage and gaps

- **Covered well:** Config, health, auth, DB, intent classification, pipeline, executor, RAG, MCP, tool validation, conversations, variation, assets, plan schemas, critic, groove engine, budget integration, **maestro_handlers**, **maestro_ui** (placeholders, chips, cards, template lookup, budget status + state derivation + camelCase serialization), **sse_utils**, **planner** (ExecutionPlan, build_execution_plan, preview_plan), **macro_engine**, **expansion**, **chord_utils**, **entity_context**, **contract lineage protocol** (`test_protocol_proof.py` — 15 proofs covering hash determinism, advisory-field exclusion, lineage chain, tamper detection, single-section lockdown, RuntimeContext immutability, field audit, and execution attestation). Plus **API contract tests** (root, health, health/full, auth 401) and **orchestrate stream** tests (REASONING and COMPOSING-with-empty-plan with mocks).
- **Supercharge checklist — done:** Gap coverage (handlers, sse_utils, planner, macro, expansion, chord_utils, entity_context); API contract tests for key public/protected routes; E2E-style orchestrate tests (mocked intent/LLM).
- **Supercharge checklist — remaining:**
  1. ~~**CI coverage threshold**~~ — **Done.** GitHub Actions runs tests with coverage; `pyproject.toml` sets `fail_under` (currently 80%).
  2. ~~**More API contract tests**~~ — **Done.** `tests/test_api_contracts.py` covers all `/api/v1/*` routes: public (/, health, models), protected 401 (maestro, validate-token, conversations, variation, users/me, MCP), and with-auth shape (conversations CRUD, users/register & me, variation/propose, assets with X-Device-ID, MCP tools/info/call). Error contract (401 detail, 422 detail) included.
  3. **Property-based tests (Hypothesis)** — Add for Pydantic models and serialization in `app/core/` and `app/models/` to catch edge cases.
  4. **Pytest markers for slow/integration** — Mark slow or integration tests (e.g. `@pytest.mark.slow`) and run CI with `-m "not slow"` by default so CI stays fast.
  5. **Skipped tests** — Revisit when stable: intent rules (test_intent.py, test_intent_classification.py), live HuggingFace (test_text2midi_duration_mapping.py, test_huggingface_live.py); either fix expectations or gate with env (e.g. `RUN_LIVE_TESTS=1`).

---

## Architectural boundary checks

Automated guardrails prevent architectural regression across the Maestro/Muse boundary.

**Boundary check script (`scripts/check_boundaries.py`):**

```bash
docker compose exec maestro python scripts/check_boundaries.py
```

Uses AST parsing to enforce 8 import and access rules. Fails with a non-zero exit code if any violation is found. Checks:

1. `app/services/variation/**` must not import `state_store` or `entity_registry`
2. `compute_variation_from_context` must have no `store` parameter and no store imports
3. `apply_variation_phrases` must not import or reference `get_or_create_store` or access `store.registry`
4. Modules above `maestro_composing/` must not import Muse models directly
5. `VariationContext` must not contain a `store` field
6. `muse_repository` must not import `StateStore` or executor modules
7. `compute_variation_from_context` must not import executor modules

Run locally or add to CI. See `docs/architecture/boundary_rules.md` for the full rule set.

**Boundary seal tests (`tests/test_boundary_seal.py`):**

Unit tests that enforce the same contracts at the pytest level:

- `TestMuseComputeBoundary` — `compute_variation_from_context` signature has no `store` param; variation service files have no forbidden imports; no lazy imports of StateStore in the function body.
- `TestVariationContextDataOnly` — `VariationContext` has no `store` field; uses `SnapshotBundle` for `base` and `proposed` fields.
- `TestApplyVariationBoundary` — `apply_variation_phrases` never calls `get_or_create_store` (mock side_effect); no `get_or_create_store` or `store.registry` references in `apply.py` source.
- `TestMuseRepositoryBoundary` — `muse_repository` does not import `StateStore`, executor, or `VariationService`.
- `TestGoldenShapes` — Locks the schema of `UpdatedRegionPayload`, `_ToolCallOutcome`, Orpheus normalization output, `SnapshotBundle`, and snapshot capture output.

**Muse persistence tests (`tests/test_muse_persistence.py`):**

Tests for the persistent variation storage layer:

- `test_variation_roundtrip` — Persist a variation, reload, assert all fields (phrases, note changes, tags, controller changes) match.
- `test_variation_status_lifecycle` — Ready -> committed transition.
- `test_variation_discard` — Ready -> discarded transition.
- `test_region_metadata_roundtrip` — Region metadata stored on phrases is retrievable.
- `test_commit_replay_from_db` — Simulate memory loss: persist, reload, verify commit-ready data matches original.
- `test_muse_repository_boundary` — AST check that `muse_repository` respects import boundaries.
