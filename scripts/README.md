# Scripts

## deploy/

Deployment and ops scripts. Run from **project root**.

| Script | Purpose |
|--------|---------|
| `install.sh` | First-time server setup |
| `setup-instance.sh` | EC2 instance configuration |
| `init-ssl.sh` | Initialize Let's Encrypt SSL |
| `init-nginx-ssl-local.sh` | Local nginx + SSL setup |
| `setup-s3-assets.sh` | S3 bucket and IAM setup |
| `setup-firewall.sh` | UFW firewall rules |
| `setup-fail2ban.sh` | fail2ban brute-force protection |
| `harden-production.sh` | Production hardening checklist |
| `update-env-security.sh` | Rotate secrets in production .env |
| `scan-security.sh` | Security audit scan |
| `deploy-production.sh` | Pull latest and restart services |
| `backup-database.sh` | Dump Postgres to S3 |
| `restore-database.sh` | Restore Postgres from S3 |
| `s3-sync-to-instance-region.sh` | Sync S3 assets across regions |
| `s3-delete-old-bucket.sh` | Clean up obsolete S3 buckets |
| `add-putobject-policy.sh` | Add S3 PutObject policy to IAM role |
| `uninstall.sh` | Full teardown |
| `migrate-sqlite-to-postgres.py` | One-time data migration (SQLite → Postgres) |

See [docs/guides/setup.md](../docs/guides/setup.md) for full deployment guide.

## e2e/

Manual E2E and QA scripts against a running Maestro/Storpheus stack. Set `TOKEN` and `BASE_URL` or pass the token as an argument where documented.

| Script | Purpose |
|--------|---------|
| `test_happy_path.sh` | Core composition happy path |
| `test_two_prompt_flow.py` | Two-prompt conversation flow |
| `test_muse_variation_e2e.sh` | Muse variation end-to-end |
| `test_edge_cases.sh` | Edge case coverage |
| `test_stress.sh` | HTTP-level load/stress test |
| `mvp_happy_path.py` | Full Maestro→Storpheus MVP flow with artifact download |
| `stress_test.py` | Deep Storpheus stress test (genres × presets × intent vectors) |

### mvp_happy_path.py

Streams a neo-soul composition prompt, captures SSE events, downloads audio/MIDI artifacts, and concatenates sections into a final `song.mp3`.

```bash
docker compose exec -e JWT="<token>" maestro python scripts/e2e/mvp_happy_path.py
```

Output lands in `/data/mvp_output/` inside the container. Copy to host:

```bash
docker compose cp maestro:/data/mvp_output /tmp/song && open /tmp/song/
```

### stress_test.py

Comprehensive throughput and latency sweep against the Storpheus service.

```bash
# Quick smoke test (1 request per genre)
docker compose exec storpheus python scripts/e2e/stress_test.py --quick

# Standard sweep (genres × bar counts × presets)
docker compose exec storpheus python scripts/e2e/stress_test.py

# Full matrix
docker compose exec storpheus python scripts/e2e/stress_test.py --full

# Concurrency scaling
docker compose exec storpheus python scripts/e2e/stress_test.py --concurrency
```

Results are written to `stress_results_{timestamp}.json` (gitignored).

## Root-level utilities

| Script | Purpose |
|--------|---------|
| `reset_database.sh` | Reset DB and run Alembic migrations. Prompts for confirmation. |
| `ec2_bootstrap.sh` | Bootstrap a fresh EC2 instance (installs Docker, clones repo). |
| `check_boundaries.py` | AST-based architectural guardrail — enforces 17 import boundary rules. |
| `ingest_docs.py` | Ingest Markdown docs into Qdrant for RAG. |
| `generate_access_code.py` | Generate a signed access token for a user. |
| `generate_test_token.py` | Generate a short-lived JWT for local testing. |
| `analyze_midi.py` | Analyze a MIDI file and print note statistics. |
| `batch_analyze.py` | Batch-analyze MIDI files across a directory. |
| `upload_assets_to_s3.py` | Upload drum kits and soundfonts to S3. |
| `upload_placeholder_kits.py` | Upload placeholder kit manifests to S3. |
| `download_reference_midi.py` | Download reference MIDI files for analysis. |

### check_boundaries.py

```bash
docker compose exec maestro python scripts/check_boundaries.py
```

See [docs/architecture/boundary_rules.md](../docs/architecture/boundary_rules.md).

### generate_test_token.py

```bash
docker compose exec maestro python scripts/generate_test_token.py <JWT_SECRET>
```
