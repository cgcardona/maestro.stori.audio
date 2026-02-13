# Testing

Run tests, intent-based QA, and quick prompts in one place.

---

## Run tests

We run tests **in Docker** so everyone (Mac, Linux, CI) uses the same environment. That keeps results consistent and makes debugging contributor issues straightforward.

**Plain tests:**
```bash
docker compose up -d
docker compose exec composer pytest tests/ -v
```

**Tests with coverage (canonical command):** Run from repo root with the stack up. You must **rebuild** the image first if the container doesn’t have `coverage`:

```bash
docker compose build composer && docker compose up -d
docker compose exec composer sh -c "export COVERAGE_FILE=/tmp/.coverage && python -m coverage run -m pytest tests/ -v && python -m coverage report --fail-under=64 --show-missing"
```

- The coverage threshold is **64%** (`--fail-under=64`). The single source of truth is `pyproject.toml` → `[tool.coverage.report]` → `fail_under`. Use the same value when running locally or in CI.
- If you see `No module named coverage`, rebuild: `docker compose build composer` then `docker compose up -d`.
- If you see `unable to open database file` for `.coverage`, the container user can’t write to `/app`; the command uses `COVERAGE_FILE=/tmp/.coverage` so the data file lives in `/tmp`.

When CI is enabled, run the same coverage command so the build fails if coverage drops below the threshold in `pyproject.toml`. Also add secret scanning (e.g. Gitleaks) and optionally a dependency audit (e.g. `pip-audit`); see `docs/security.md`.

**Coverage target:** We aim for 80% over time. The report shows where to add tests: biggest gaps are often `compose_handlers`, `executor`, `llm_client`, route handlers (compose, conversations, MCP), and optional backends (Orpheus, HuggingFace, renderers). Raising coverage lets you bump `fail_under` in `pyproject.toml`.

---

## Intent-based testing

The backend routes prompts by intent; each intent has an allowed tool set. Use the table below for happy-path checks.

**Single-tool (force one):** CREATE_PROJECT, SET_TEMPO, SET_KEY, ADD_TRACK, SET_VOLUME, SET_PAN, MUTE_TRACK, SOLO_TRACK, PLAY, STOP, DELETE_REGION, MOVE_REGION, DUPLICATE_REGION, CLEAR_NOTES, etc.

| Example prompt | Expected tool(s) |
|----------------|------------------|
| "Create a new project 'Test' at 120 BPM" | `stori_create_project` |
| "Set tempo to 95 BPM" | `stori_set_tempo` |
| "Add a drum track" | `stori_add_track` |
| "Mute the drums" | `stori_mute_track` |
| "Play" / "Stop" | `stori_play` / `stori_stop` |

**Multi-tool (e.g. GENERATE_MUSIC):** Track → region → generate → add notes.

| Example prompt | Expected (order) |
|----------------|------------------|
| "Generate a boom bap drum pattern, 4 bars at 95 BPM" | `stori_add_track`, `stori_add_region`, `stori_generate_drums`, `stori_add_notes` |
| "Add reverb to the piano track" | `stori_add_effect` |

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

- **Covered well:** Config, health, auth, DB, intent classification, pipeline, executor, RAG, MCP, tool validation, conversations, variation, assets, plan schemas, critic, groove engine, budget integration, **compose_handlers**, **sse_utils**, **planner** (ExecutionPlan, build_execution_plan, preview_plan), **macro_engine**, **expansion**, **chord_utils**, **entity_context**. Plus **API contract tests** (root, health, health/full, auth 401) and **orchestrate stream** tests (REASONING and COMPOSING-with-empty-plan with mocks).
- **Supercharge checklist — done:** Gap coverage (handlers, sse_utils, planner, macro, expansion, chord_utils, entity_context); API contract tests for key public/protected routes; E2E-style orchestrate tests (mocked intent/LLM).
- **Supercharge checklist — remaining:**
  1. ~~**CI coverage threshold**~~ — **Done.** GitHub Actions runs tests with coverage; `pyproject.toml` sets `fail_under` (currently 64%; target 80%).
  2. ~~**More API contract tests**~~ — **Done.** `tests/test_api_contracts.py` covers all `/api/v1/*` routes: public (/, health, models), protected 401 (compose, validate-token, conversations, variation, users/me, MCP), and with-auth shape (conversations CRUD, users/register & me, variation/propose, assets with X-Device-ID, MCP tools/info/call). Error contract (401 detail, 422 detail) included.
  3. **Property-based tests (Hypothesis)** — Add for Pydantic models and serialization in `app/core/` and `app/models/` to catch edge cases.
  4. **Pytest markers for slow/integration** — Mark slow or integration tests (e.g. `@pytest.mark.slow`) and run CI with `-m "not slow"` by default so CI stays fast.
  5. **Skipped tests** — Revisit when stable: intent rules (test_intent.py, test_intent_classification.py), live HuggingFace (test_text2midi_duration_mapping.py, test_huggingface_live.py); either fix expectations or gate with env (e.g. `RUN_LIVE_TESTS=1`).
