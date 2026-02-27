# Security audit

**Environment:** Production/staging (set your own domain via `DOMAIN`, CORS, Nginx).  
**Stack:** FastAPI, Nginx, Docker Compose, Ubuntu, JWT, UUID-based asset access  
**Scope:** Defensive, production-grade review.

---

## Summary

The stack is in good shape when properly configured: SSH hardening, fail2ban, UFW, non-root containers, TLS and security headers, JWT with revocation, asset access gated by UUID + rate limits. No critical misconfigurations. Items below are targeted fixes before production.

**Key recommendations (done where noted):** Add CPU/memory limits in docker-compose; do not use `get_user_id_from_token` for auth (use claims from `require_valid_token` only); fail closed on token-revocation DB failure (503); no default DB password in prod; stricter nginx rate limit for auth paths; CORS not wildcard in prod.

---

## Secrets and .env

- **Never commit `.env`.** It is gitignored; ensure it has never been committed (`git log -p -- .env`). If it ever was, rotate all secrets (OpenRouter, JWT, DB, AWS).
- **Prevention:** Keep `.env` only on the server or local dev machine; `chmod 600 .env`. Before open-sourcing or sharing the repo, run a secret scan locally (e.g. `gitleaks detect --source .` or your preferred tool). **When CI is enabled:** add a secret-scan step and a dependency audit (e.g. `pip-audit` or `safety`) to the pipeline.

## Nginx SSL (production vs dev)

- Production TLS uses Let's Encrypt (certbot). The files in `deploy/nginx/ssl/` are for the nginx default_server (reject unknown hosts with 444) only; do not use them for production TLS.

## Before go-live checklist

- [ ] SSH: second session open when hardening; backup of `sshd_config`.
- [ ] UFW: only 22, 80, 443; `ufw status` verified.
- [ ] `.env`: only on server; `chmod 600`; no wildcard CORS.
- [ ] `DEBUG=false`; `ACCESS_TOKEN_SECRET` and `DB_PASSWORD` set and strong.
- [ ] TLS valid; HTTP→HTTPS; HSTS present.
- [ ] Containers non-root; DB/Qdrant not exposed to internet. Qdrant is internal only in the default Compose setup; if you expose it, add authentication.
- [ ] Backups and rate limits in effect; admin tokens only via controlled script.

Full audit (infrastructure, Docker, Nginx, FastAPI, JWT, assets, secrets, DB, DDoS, logging, codebase) was consolidated into this summary and checklist.

---

## Muse Hub CLI token storage

The Muse CLI reads your Hub auth token from `.muse/config.toml` under `[auth] token`.

**Storage rules:**

- `.muse/config.toml` **must** be added to `.gitignore` (and `.museignore` if applicable) — it holds your token and should never be committed to version control.
- The token is read from disk only when a Hub request is made; it is never cached in memory between CLI invocations.
- The raw token value is **never written to any log** — log lines use `"Bearer ***"` as a placeholder. Verify with `--log-level debug` if needed.
- `muse remote -v` and similar commands must mask the token in any output.

**MVP limitations:**

- No token rotation or automatic refresh is implemented. When a token expires, obtain a new one via `POST /auth/token` and update `config.toml` manually.
- Revocation is handled server-side; the CLI has no revocation cache.

**Example `.gitignore` entry:**

```
.muse/config.toml
```

---

## JWT token boundary validation

**File:** `app/auth/tokens.py` — `validate_access_code`

The JWT payload returned by `jwt.decode()` is typed `dict[str, Any]` by the underlying library.  Rather than coercing fields with `str(payload["sub"])` or `int(payload["exp"])` — which silently accepts malformed tokens — the boundary validator uses explicit `isinstance` checks and raises `AccessCodeError` on any type mismatch:

```python
raw_iat = payload.get("iat", 0)
raw_exp = payload.get("exp", 0)
if not isinstance(raw_iat, int) or not isinstance(raw_exp, int):
    raise AccessCodeError("Malformed token: iat/exp must be integers")
```

**Why this matters:** A coercion like `int(payload["exp"])` succeeds on `"1234"` (string) and raises only on truly uncoercible values — giving a false sense of validation.  The `isinstance` check rejects *any* non-integer `exp`, making the boundary contract explicit and exhaustive.

**Pattern:** This boundary-validation approach (extract → `isinstance` check → raise named error → assign to typed `TypedDict`) is the canonical way to cross from untyped external library output into the typed internal domain.  Apply the same pattern wherever raw HTTP payloads, database rows, or third-party library returns first enter the codebase.
