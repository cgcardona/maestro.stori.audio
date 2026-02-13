#!/usr/bin/env bash
# Generate self-signed certs for nginx default server (local dev).
# Nginx requires these to start; production uses Let's Encrypt via certbot.
# Run from repo root.

set -e
SSL_DIR="$(cd "$(dirname "$0")/../.." && pwd)/deploy/nginx/ssl"
mkdir -p "$SSL_DIR"
cd "$SSL_DIR"

if [ -f default.crt ] && [ -f default.key ]; then
  echo "deploy/nginx/ssl/default.crt and default.key already exist."
  exit 0
fi

openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout default.key -out default.crt \
  -subj "/CN=localhost"
chmod 644 default.crt default.key
echo "Created default.crt and default.key in deploy/nginx/ssl/"
