# Maestro Stori Audio – Documentation

**Stori — the infinite music machine.** Use these docs in order: read only what you need. Start with [guides/setup.md](guides/setup.md), then [guides/integrate.md](guides/integrate.md). Script paths are from **repo root**.

**Environments:** local, **stage.stori.audio**, **maestro.stori.audio**.

---

## Docs structure

| Directory | Purpose |
|-----------|---------|
| **[guides/](guides/)** | How-to: setup, integrate, testing, assets, security. |
| **[reference/](reference/)** | API and architecture reference. |
| **[architecture/](architecture/)** | Deep architecture docs: Muse VCS, boundary rules, system evolution analysis. |
| **[protocol/](protocol/)** | Normative specs: Muse/Variation terminology, wire contract, end-to-end spec. |
| **[contracts/](contracts/)** | Service boundary contracts: Stori ↔ Maestro (backend truth) and Maestro ↔ Orpheus (boundary audit). |
| **[roadmaps/](roadmaps/)** | Roadmaps and future work (e.g. neural MIDI). |

Links in this index are relative from `docs/` so they work from repo root or from within `docs/`. When adding new docs, put them in the appropriate directory and link with paths relative to the linking file (e.g. from `guides/` use `../reference/api.md` for reference docs).

---

## 1. [guides/setup.md](guides/setup.md)

Local run, cloud deploy, config (env), deploy (systemd, S3), new instance, self-hosted, AWS credentials. One place to get the stack running.

---

## 2. [guides/integrate.md](guides/integrate.md)

API base URL, auth (JWT), access codes, frontend (Swift, assets), MCP (Cursor/Claude, WebSocket). Everything needed to connect an app or an agent.

---

## 3. [reference/api.md](reference/api.md)

API and MCP tools in one place: maestro stream (SSE), all event types (`state`, `plan`, `planStepUpdate`, `toolStart`, `toolCall`, `toolError`, `reasoning`, `budgetUpdate`, `complete`, and composing events), request body fields (`humanizeProfile`, `qualityPreset`, `swing`), variable refs, models (OpenRouter), the **Maestro Default UI endpoints** (placeholders, prompt chips, prompt cards, template lookup, budget status), and the full MCP tool reference (all 41 tools with parameters and routing). Use with Stori app, Cursor/Claude, or HTTP MCP. Programmatic list: `GET /api/v1/mcp/tools` or `app/mcp/tools.py`.

---

## 4. [reference/architecture.md](reference/architecture.md)

One backend, two entry points (Stori app + MCP). Request flow (intent → REASONING / EDITING / COMPOSING). Structured plan events (EDITING). Intent engine. Music generation (Orpheus required).

---

## 5. [guides/testing.md](guides/testing.md)

Run tests, intent-based QA, quick prompts.

---

## 6. [guides/assets.md](guides/assets.md)

Drum kits and soundfonts; upload to S3.

---

## 7. [guides/fe-project-state-sync.md](guides/fe-project-state-sync.md)

Frontend integration guide for project state serialization: how to build the `project` snapshot sent on every compose request, capture server-assigned entity IDs from `toolCall` events, and handle the `plan` / `planStepUpdate` display events. Includes the critical round-trip example for sequential composition (e.g. `Position: after intro`).

---

## 8. Specs & Variation protocol

| Doc | Description |
|-----|-------------|
| [protocol/stori-prompt-spec.md](protocol/stori-prompt-spec.md) | **Stori Structured Prompt:** prompt format for expert-level control. |
| [protocol/muse-variation-spec.md](protocol/muse-variation-spec.md) | Muse / Variation: end-to-end UX + technical contract. |
| [protocol/variation-api.md](protocol/variation-api.md) | **Variation API:** wire contract, endpoints, SSE events, error codes. |
| [protocol/terminology.md](protocol/terminology.md) | Canonical vocabulary for Muse/Variations (normative). |
| [roadmaps/neural-midi-roadmap.md](roadmaps/neural-midi-roadmap.md) | Neural MIDI generation roadmap and status. |

---

## 9. Muse VCS — Musical Version Control

| Doc | Description |
|-----|-------------|
| [architecture/muse-vcs.md](architecture/muse-vcs.md) | **Canonical Muse VCS reference:** module map, HTTP API, VCS primitives, boundary rules. Start here. |
| [architecture/muse_e2e_demo.md](architecture/muse_e2e_demo.md) | E2E tour de force: run the full VCS lifecycle demo and read the expected output. |
| [architecture/boundary_rules.md](architecture/boundary_rules.md) | 17 AST-enforced import boundary rules (variation + Muse VCS). |
| [architecture/maestro-muse-evolution.md](architecture/maestro-muse-evolution.md) | Phase 1 analysis: layered architecture decomposition (historical; see `muse-vcs.md` for current state). |
| [architecture/muse-persistent-history-bridge.md](architecture/muse-persistent-history-bridge.md) | Phase 2 conceptual spec: authority model and abstractions (historical; now implemented). |

---

## 10. [guides/security.md](guides/security.md)

Security audit summary, go-live checklist, and service exposure (Qdrant, DB, nginx SSL).

