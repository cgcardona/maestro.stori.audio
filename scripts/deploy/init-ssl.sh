#!/bin/bash
# Initialize SSL certificates for Stori Maestro
#
# Usage: sudo ./scripts/deploy/init-ssl.sh <domain> <email>
#   domain  Your public domain (e.g. maestro.example.com)
#   email  Email for Let's Encrypt (e.g. admin@example.com)
# Run from project root.
#
# This script:
# 1. Generates a temporary self-signed cert for nginx to start
# 2. Starts nginx to serve ACME challenges
# 3. Requests Let's Encrypt certificate
# 4. Restarts nginx with the real certificate

set -e

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Usage: $0 <domain> <email>"
    echo "  e.g. $0 maestro.example.com admin@example.com"
    exit 1
fi

echo "================================================"
echo "Stori Maestro SSL Initialization"
echo "Domain: $DOMAIN"
echo "Email: $EMAIL"
echo "================================================"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo ./scripts/deploy/init-ssl.sh)"
    exit 1
fi

# Create directories
mkdir -p deploy/nginx/ssl

# Generate temporary self-signed certificate for nginx to start
echo "Generating temporary self-signed certificate..."
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout deploy/nginx/ssl/default.key \
    -out deploy/nginx/ssl/default.crt \
    -subj "/CN=localhost" 2>/dev/null

# Create a temporary nginx config without SSL for initial ACME challenge
echo "Creating temporary nginx config..."
cat > deploy/nginx/conf.d/temp-acme.conf << EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 200 'Stori Maestro - SSL setup in progress...';
        add_header Content-Type text/plain;
    }
}
EOF

# Remove the full SSL config temporarily
if [ -f deploy/nginx/conf.d/maestro.conf ]; then
    mv deploy/nginx/conf.d/maestro.conf deploy/nginx/conf.d/maestro.conf.bak
fi

# Start nginx and certbot containers
echo "Starting nginx for ACME challenge..."
docker compose up -d nginx

# Wait for nginx to start
sleep 5

# Request certificate (override service entrypoint so we run certonly, not renew)
echo "Requesting Let's Encrypt certificate..."
docker compose run --rm --entrypoint certbot certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN"

# Restore the full SSL config
echo "Restoring SSL nginx config..."
rm deploy/nginx/conf.d/temp-acme.conf
if [ -f deploy/nginx/conf.d/maestro.conf.bak ]; then
    mv deploy/nginx/conf.d/maestro.conf.bak deploy/nginx/conf.d/maestro.conf
fi

# Restart nginx with SSL config
echo "Restarting nginx with SSL..."
docker compose restart nginx

echo "================================================"
echo "SSL setup complete!"
echo "Your site is now available at https://$DOMAIN"
echo "================================================"
