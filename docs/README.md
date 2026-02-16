# Composer Stori Audio – Documentation

**Stori — the infinite music machine.** Use these docs in order: Read only what you need for your next step. Start with [setup.md](setup.md), then [integrate.md](integrate.md). Script paths are from **repo root**.

**Environments:** local, **stage.stori.audio**, **composer.stori.audio**.

---

## 1. [setup.md](setup.md)

Local run, cloud deploy, config (env), deploy (systemd, S3), new instance, self-hosted, AWS credentials. One place to get the stack running.

---

## 2. [integrate.md](integrate.md)

API base URL, auth (JWT), access codes, frontend (Swift, assets), MCP (Cursor/Claude, WebSocket). Everything needed to connect an app or an agent.

---

## 3. [api.md](api.md)

API and MCP tools in one place: compose stream (SSE), event types, variable refs, models (OpenRouter), and the full MCP tool reference (all 41 tools with parameters and routing). Use with Stori app, Cursor/Claude, or HTTP MCP. Programmatic list: `GET /api/v1/mcp/tools` or `app/mcp/tools.py`.

---

## 4. [architecture.md](architecture.md)

One backend, two entry points (Stori app + MCP). Request flow (intent → REASONING / EDITING / COMPOSING). Intent engine. Music generation (Orpheus required).

---

## 5. [testing.md](testing.md)

Run tests, intent-based QA, quick prompts.

---

## 6. [assets.md](assets.md)

Drum kits and soundfonts; upload to S3.

---

## 7. Specs & Variation Protocol

| Doc | Description |
|-----|-------------|
| [muse-variation-spec.md](muse-variation-spec.md) | Muse / Variation: end-to-end UX + technical contract. |
| [protocol/variation_api_v1.md](protocol/variation_api_v1.md) | **Variation API v1:** wire contract, endpoints, SSE events, error codes. |
| [protocol/TERMINOLOGY.md](protocol/TERMINOLOGY.md) | Canonical vocabulary for Muse/Variations (normative). |
| [neural-midi-roadmap.md](neural-midi-roadmap.md) | Neural MIDI generation roadmap and status. |

---

## 8. [security.md](security.md)

Security audit summary, go-live checklist, and service exposure (Qdrant, DB, nginx SSL).
