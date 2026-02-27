#!/bin/bash
# =============================================================================
# Stori Maestro - AWS Instance Setup Script
# =============================================================================
# 
# This script sets up a fresh Ubuntu 22.04+ AWS instance with:
# - Docker and Docker Compose
# - Stori Maestro backend
# - Nginx reverse proxy with Let's Encrypt SSL
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/cgcardona/maestro/main/scripts/deploy/setup-instance.sh | sudo bash -s -- [options]
#
# Or after cloning:
#   sudo ./deploy/setup-instance.sh [options]
#
# Options:
#   --domain DOMAIN    Domain name (required for SSL; e.g. maestro.example.com)
#   --email EMAIL      Email for Let's Encrypt (required for SSL; e.g. admin@example.com)
#   --branch BRANCH    Git branch to deploy (default: main)
#   --skip-ssl         Skip SSL setup (for testing; uses localhost as domain)
#   --no-clone         Use existing code in current dir (for rsync/scp deploy; run from project root)
#
# =============================================================================

set -e

# Default values (no hardcoded domain/email; pass --domain and --email for SSL)
DOMAIN=""
EMAIL=""
BRANCH="main"
SKIP_SSL=false
NO_CLONE=false
REPO_URL="https://github.com/cgcardona/maestro.git"
INSTALL_DIR="/opt/stori"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --domain)
            DOMAIN="$2"
            shift 2
            ;;
        --email)
            EMAIL="$2"
            shift 2
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --skip-ssl)
            SKIP_SSL=true
            shift
            ;;
        --no-clone)
            NO_CLONE=true
            INSTALL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# When --no-clone, INSTALL_DIR was set above; otherwise ensure default
if [ "$NO_CLONE" = true ]; then
    log_info "No-clone mode: using project at $INSTALL_DIR"
fi

# Require --domain and --email when SSL is enabled
if [ "$SKIP_SSL" = false ]; then
    if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
        log_error "For SSL you must provide --domain and --email (e.g. --domain maestro.example.com --email admin@example.com)"
        exit 1
    fi
else
    # Skip-SSL mode: use localhost if domain not set
    [ -z "$DOMAIN" ] && DOMAIN="localhost"
fi

echo "=============================================="
echo "  Stori Maestro - Instance Setup"
echo "=============================================="
echo "  Domain:    $DOMAIN"
echo "  Email:     $EMAIL"
echo "  Branch:    $BRANCH"
echo "  Install:   $INSTALL_DIR"
echo "  SSL:       $([ "$SKIP_SSL" = true ] && echo 'Skipped' || echo 'Enabled')"
echo "  No-clone:  $NO_CLONE"
echo "=============================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root (sudo)"
    exit 1
fi

# =============================================================================
# 1. System Updates and Dependencies
# =============================================================================
log_info "Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

log_info "Installing dependencies..."
apt-get install -y -qq \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    jq \
    rsync

# =============================================================================
# 2. Install Docker
# =============================================================================
if ! command -v docker &> /dev/null; then
    log_info "Installing Docker..."
    
    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    
    # Add the repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # Install Docker
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    # Start Docker
    systemctl enable docker
    systemctl start docker
    
    log_info "Docker installed successfully"
else
    log_info "Docker already installed"
fi

# =============================================================================
# 3. Clone/Update Repository (skip when --no-clone)
# =============================================================================
if [ "$NO_CLONE" = true ]; then
    if [ ! -f "$INSTALL_DIR/docker-compose.yml" ] || [ ! -d "$INSTALL_DIR/deploy" ]; then
        log_error "No-clone mode: $INSTALL_DIR does not look like the project root (missing docker-compose.yml or deploy/). Run from project root."
        exit 1
    fi
    log_info "Using existing code at $INSTALL_DIR"
    cd "$INSTALL_DIR"
elif [ -d "$INSTALL_DIR" ]; then
    log_info "Updating existing installation..."
    cd "$INSTALL_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
else
    log_info "Cloning repository..."
    git clone -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# =============================================================================
# 4. Create Environment File
# =============================================================================
log_info "Setting up environment..."

if [ ! -f .env ]; then
    cat > .env << EOF
# Stori Maestro Configuration
# Generated by setup-instance.sh on $(date)

# Domain
STORI_DOMAIN=$DOMAIN

# API Settings
STORI_DEBUG=false
STORI_HOST=0.0.0.0
STORI_PORT=10001

# CORS - Update with your frontend domains
STORI_CORS_ORIGINS=["https://$DOMAIN", "stori://"]

# JWT / access token secret (app reads STORI_ACCESS_TOKEN_SECRET)
STORI_ACCESS_TOKEN_SECRET=$(openssl rand -hex 32)

# PostgreSQL (maestro and docker-compose use this)
STORI_DB_PASSWORD=$(openssl rand -hex 16)

# LLM Provider (openrouter, openai, or local)
STORI_LLM_PROVIDER=openrouter

# OpenRouter API Key - ADD YOUR KEY (or copy from existing server .env)
# STORI_OPENROUTER_API_KEY=your_key_here

# Default model
STORI_LLM_MODEL=anthropic/claude-sonnet-4

# Budget Settings (in cents)
STORI_DEFAULT_BUDGET_CENTS=500
EOF

    log_warn "Created .env file - please update with your API keys!"
    log_warn "Edit: $INSTALL_DIR/.env"
else
    log_info "Using existing .env file"
fi

# =============================================================================
# 5. Generate Default SSL Certificate
# =============================================================================
log_info "Generating self-signed certificate for nginx startup..."
mkdir -p deploy/nginx/ssl

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout deploy/nginx/ssl/default.key \
    -out deploy/nginx/ssl/default.crt \
    -subj "/CN=localhost" 2>/dev/null

# =============================================================================
# 6. Build and Start Services
# =============================================================================
log_info "Building Docker images..."
docker compose build

if [ "$SKIP_SSL" = true ]; then
    # Start without SSL (for testing)
    log_info "Starting services (without SSL)..."
    
    # Create a simple HTTP-only nginx config
    cat > deploy/nginx/conf.d/maestro-stori-http.conf << EOF
upstream maestro_api {
    server maestro:10001;
}

server {
    listen 80;
    server_name $DOMAIN localhost;

    location / {
        proxy_pass http://maestro_api;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # SSE/WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
EOF
    
    # Remove SSL config
    rm -f deploy/nginx/conf.d/maestro-stori.conf deploy/nginx/conf.d/default.conf
    
    docker compose up -d
else
    # Start with SSL - ensure nginx config uses the requested domain before init-ssl
    log_info "Configuring nginx for domain: $DOMAIN..."
    if [ -f deploy/nginx/conf.d/maestro-stori.conf ]; then
        sed -i.bak "s/__DOMAIN__/$DOMAIN/g" deploy/nginx/conf.d/maestro-stori.conf
    fi
    log_info "Starting services with SSL..."
    chmod +x scripts/deploy/init-ssl.sh
    ./scripts/deploy/init-ssl.sh "$DOMAIN" "$EMAIL"
    
    docker compose up -d
fi

# =============================================================================
# 7. Wait and Verify
# =============================================================================
log_info "Waiting for services to start..."
sleep 10

# Check health (via nginx on 80 or 443)
if [ "$SKIP_SSL" = true ]; then
    HEALTH_URL="http://localhost/api/v1/health"
else
    HEALTH_URL="https://localhost/api/v1/health"
fi
if curl -sk "$HEALTH_URL" | jq -e '.status == "ok"' > /dev/null 2>&1; then
    log_info "Maestro API is healthy!"
else
    log_warn "Maestro API may not be ready yet. Check logs with: docker compose logs"
fi

# =============================================================================
# 8. Create Systemd Service for Docker Compose
# =============================================================================
log_info "Creating systemd service..."

SERVICE_FILE="$INSTALL_DIR/deploy/systemd/maestro-stori.service"
if [ -f "$SERVICE_FILE" ]; then
    sed "s|/opt/maestro|$INSTALL_DIR|g" "$SERVICE_FILE" > /etc/systemd/system/maestro-stori.service
    chmod 644 /etc/systemd/system/maestro-stori.service
else
    log_error "Service file not found: $SERVICE_FILE"
    exit 1
fi

systemctl daemon-reload
systemctl enable maestro-stori

# =============================================================================
# Complete!
# =============================================================================
echo ""
echo "=============================================="
echo "  Stori Maestro Setup Complete!"
echo "=============================================="
echo ""

if [ "$SKIP_SSL" = true ]; then
    echo "  HTTP:  http://$DOMAIN"
    echo "  Local: http://localhost:10001"
else
    echo "  HTTPS: https://$DOMAIN"
    echo "  Local: http://localhost:10001"
fi

echo ""
echo "  API Docs:  https://$DOMAIN/docs"
echo "  Health:    https://$DOMAIN/api/v1/health"
echo ""
echo "  Useful commands:"
echo "    cd $INSTALL_DIR"
echo "    docker compose logs -f        # View logs"
echo "    docker compose restart        # Restart services"
echo "    docker compose down           # Stop services"
echo "    docker compose up -d --build  # Rebuild and restart"
echo ""
echo "  Generate access token:"
echo "    docker compose exec maestro python scripts/generate_access_code.py --user-id UUID --hours 24"
echo ""
log_warn "Don't forget to:"
log_warn "  1. Add your OpenRouter API key (and any other 3rd-party keys) to .env"
log_warn "  2. Point $DOMAIN DNS to this server's IP"
log_warn "  3. If using S3 assets, add AWS_* and STORI_AWS_* vars to .env"
echo ""
