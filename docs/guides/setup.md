# Setup & run

One place for: local run, cloud deploy, config, and day-to-day ops. All paths and commands are from **repo root** unless noted.

---

## Quick start (Docker)

```bash
cp .env.example .env
# Edit .env: STORI_OPENROUTER_API_KEY, STORI_DB_PASSWORD (required; e.g. openssl rand -hex 16 for local), STORI_ACCESS_TOKEN_SECRET (openssl rand -hex 32).
docker compose up -d
```

Maestro: `http://localhost:10001`. Tests: rebuild first (container copies code at build time, no live mount), then run:
```bash
docker compose build maestro && docker compose up -d
docker compose exec maestro pytest tests/ -v
```

---

## Local (macOS)

- Use **Docker Desktop**; same stack as production.
- **Stori macOS app:** Maestro publishes port 10001 to the host (`127.0.0.1:10001`). Set `ApiBaseURL = http://localhost:10001` in the app; health, validate-token, register, maestro/stream, and the MCP WebSocket (`ws://localhost:10001/api/v1/mcp/daw?token=...`) are then reachable from the Mac.
- Copy `.env` from server if you have one. To hit the API via nginx: set `NGINX_CONF_DIR=conf.d-local` in `.env`, then `docker compose up -d`; use **`http://localhost`** (nginx proxies to maestro). Direct to Maestro: **`http://localhost:10001`** (no nginx needed for the Stori app).
- Port 5432 in use: `brew services stop postgresql@15` or change Postgres port in `docker-compose.yml`.
- Logs: `docker compose logs -f maestro`. Restart: `docker compose restart maestro`.

---

## Cloud (Ubuntu + Docker)

**API keys:** OpenRouter (https://openrouter.ai/keys), HuggingFace Pro for Orpheus. Put keys in `.env` on the server.

**Deploy:** On Ubuntu: clone or rsync repo, `cp .env.example .env`, edit `.env`, then `docker compose up -d`. Production uses the same `docker-compose.yml` with a production `.env` (see **Configuration** below for required vars). Start on boot: systemd unit from `scripts/deploy/install.sh` (runs `docker compose up -d`). See **New instance** below for a fresh EC2.

---

## Configuration (env)

| Area | Key vars |
|------|----------|
| **Domain / CORS** | `STORI_DOMAIN`; `STORI_CORS_ORIGINS` (JSON array, required—no default; set exact origins in production) |
| **Auth** | `STORI_ACCESS_TOKEN_SECRET` — `openssl rand -hex 32`; required for protected endpoints |
| **LLM** | `STORI_LLM_PROVIDER=openrouter`, `STORI_OPENROUTER_API_KEY`, `STORI_LLM_MODEL` (supported: `anthropic/claude-sonnet-4.6` · `anthropic/claude-opus-4.6` — no other models) |
| **DB** | `STORI_DB_PASSWORD` or `STORI_DATABASE_URL`. Reset: see **Reset database (Postgres)** below. |
| **Music** | `STORI_ORPHEUS_BASE_URL` (default `http://localhost:10002`), `STORI_HF_API_KEY`, `STORI_ORPHEUS_MAX_CONCURRENT` (default `2` — max parallel submit+poll cycles), `STORI_ORPHEUS_TIMEOUT` (default `180`s — fallback max read timeout), `STORI_ORPHEUS_POLL_TIMEOUT` (default `30`s — long-poll timeout per `/jobs/{id}/wait` request), `STORI_ORPHEUS_POLL_MAX_ATTEMPTS` (default `10` — max polls before giving up, ~5 min total), `STORI_ORPHEUS_CB_THRESHOLD` (default `3` — consecutive failures before circuit breaker trips), `STORI_ORPHEUS_CB_COOLDOWN` (default `60`s — probe interval after trip), `STORI_ORPHEUS_REQUIRED` (default `true` — abort composition if Orpheus is unreachable). **Docker:** `docker-compose.yml` overrides to `http://orpheus:10002` so the maestro container can reach Orpheus. See **HuggingFace token (Orpheus)** below. |
| **Agent watchdogs** | `STORI_SECTION_CHILD_TIMEOUT` (default `300`s), `STORI_INSTRUMENT_AGENT_TIMEOUT` (default `600`s), `STORI_BASS_SIGNAL_WAIT_TIMEOUT` (default `240`s). Prevents orphaned subagents. See [architecture.md](../reference/architecture.md#agent-safety-nets). |
| **S3** | `STORI_AWS_S3_ASSET_BUCKET`, `STORI_AWS_REGION`, plus AWS keys for presigned URLs |

Local: `NGINX_CONF_DIR=conf.d-local`. Full list: `.env.example`.

### HuggingFace token (Orpheus)

Orpheus (when backed by a Hugging Face Gradio Space) needs a Hugging Face API token so the Space can attribute GPU usage to your account. Maestro reads `STORI_HF_API_KEY` and sends it as `Authorization: Bearer <token>` on every request to Orpheus; the Orpheus service forwards it to the Space.

- **Set the token:** In `.env`, set `STORI_HF_API_KEY` to your token (no quotes). With Docker Compose, the maestro service loads `.env`, so restart the stack after changing it.
- **Verify it’s sent:** With `STORI_DEBUG=true`, Maestro logs at debug level whether an HF token is present for each Orpheus request (value is never logged).
- **“GPU quota (0s left)” after a new token:** If you see this right after switching to a new token, (1) confirm the new token is in `.env` and the maestro container was restarted; (2) try a token with **Write** scope—read-only tokens may not receive GPU quota for Space API calls. Create the token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).
- **Revoked tokens:** If HF revokes a token (e.g. after it was exposed), replace it with a new token and update `STORI_HF_API_KEY`; no code change is required.

---

## Reset database (Postgres)

**All data is deleted.** Use when you want a clean schema (e.g. no users yet, or after a bad migration). From repo root:

1. **Stop the app** so it releases DB connections:
   ```bash
   docker compose stop maestro
   ```
2. **Drop and recreate the database:**
   ```bash
   docker compose exec -T postgres psql -U stori -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'stori' AND pid <> pg_backend_pid();"
   docker compose exec -T postgres psql -U stori -d postgres -c "DROP DATABASE IF EXISTS stori;"
   docker compose exec -T postgres psql -U stori -d postgres -c "CREATE DATABASE stori;"
   ```
3. **Run migrations** in a one-off container (so the app does not create tables on startup first). Migrations require `STORI_DATABASE_URL` or `STORI_DB_PASSWORD` (with Docker Postgres); see `.env.example`.
   ```bash
   docker compose run --rm maestro alembic upgrade head
   ```
4. **Start the app:**
   ```bash
   docker compose up -d maestro
   ```

For SQLite: delete the DB file (e.g. `/data/stori.db` in the container or your local path), then run `alembic upgrade head` before or after starting the app.

---

## Deploy (day-to-day)

- **Restart:** `ssh stage.stori.audio 'cd ~/maestro.stori.audio && docker compose restart maestro'`
- **Sync code:** `rsync -avz --exclude '.git' --exclude '__pycache__' --exclude '.env' ./ stage.stori.audio:~/maestro.stori.audio/`
- **Recreate (pick up .env):** `docker compose up -d --force-recreate maestro`
- **S3 assets:** Run `scripts/deploy/setup-s3-assets.sh` with AWS credentials; add printed vars to `.env`. Upload assets via `scripts/upload_assets_to_s3.py` (e.g. inside container).
- **Uninstall:** `docker compose down`; disable systemd: `sudo systemctl disable maestro-stori && sudo systemctl stop maestro-stori`.

---

## New instance (e.g. stage.stori.audio)

1. **EC2:** Ubuntu 22.04, t3.medium, SSH + HTTP + HTTPS; note public IP.
2. **DNS:** A record for your domain → instance IP; wait for propagation.
3. **Code:** rsync repo (exclude `.git`, `.env`) to `ubuntu@IP:~/maestro.stori.audio/`.
4. **On server:** `cd ~/maestro.stori.audio`, `cp .env.example .env`, edit (OpenRouter, DB password, domain, CORS). Run `sudo ./scripts/deploy/setup-instance.sh --domain stage.stori.audio` (or your domain) for Docker, nginx, SSL, systemd.
5. **CORS / security:** Run `bash scripts/deploy/update-env-security.sh` so CORS is not wildcard.

---

## AWS credentials

For S3 asset setup: create IAM user + access key in AWS Console (or use existing key). Run `scripts/deploy/setup-s3-assets.sh` with `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`. Script can create a limited `stori-assets-app` user; put the **printed** env vars into server `.env`.
