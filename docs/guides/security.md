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
