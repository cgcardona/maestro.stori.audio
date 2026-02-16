# Scripts

- **deploy/** – Deployment and ops: `install.sh`, `setup-instance.sh`, `init-ssl.sh`, `setup-s3-assets.sh`, `backup-database.sh`, `restore-database.sh`, `deploy-production.sh`, `setup-firewall.sh`, `setup-fail2ban.sh`, `uninstall.sh`, etc. Run from **project root**. See [docs/guides/setup.md](../docs/guides/setup.md).
- **e2e/** – Manual E2E/QA scripts against a running composer (happy path, stress, edge cases, Muse variation). Set `TOKEN` and `BASE_URL` or pass token as argument where documented.
- **reset_database.sh** – Reset DB (SQLite or Postgres) and run migrations. Run from project root. Prompts for confirmation.
