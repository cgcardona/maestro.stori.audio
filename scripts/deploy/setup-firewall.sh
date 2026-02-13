#!/bin/bash
# Production Firewall Setup for Stori
# Implements minimal attack surface - only SSH, HTTP, HTTPS

set -e

echo "=========================================="
echo "Stori Production Firewall Setup"
echo "=========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

echo "1. Installing UFW (if not installed)..."
apt-get update -qq
apt-get install -y ufw

echo ""
echo "2. Configuring firewall rules..."

# Default policies: deny incoming, allow outgoing
ufw --force default deny incoming
ufw --force default allow outgoing

# Allow SSH (with rate limiting)
echo "   - SSH (port 22) with rate limiting"
ufw limit 22/tcp comment 'SSH with rate limiting'

# Allow HTTP (for Let's Encrypt challenges)
echo "   - HTTP (port 80) for SSL challenges"
ufw allow 80/tcp comment 'HTTP - SSL challenges'

# Allow HTTPS
echo "   - HTTPS (port 443) for API traffic"
ufw allow 443/tcp comment 'HTTPS - API'

# Composer (10001) stays internal to Docker; do not expose to the internet (use nginx only)

# Block all other ports
echo "   - Blocking all other inbound ports"

echo ""
echo "3. Enabling firewall..."
ufw --force enable

echo ""
echo "=========================================="
echo "Firewall Status:"
echo "=========================================="
ufw status verbose

echo ""
echo "✅ Firewall configured successfully!"
echo ""
echo "Active rules:"
echo "  - SSH (22/tcp) - rate limited"
echo "  - HTTP (80/tcp) - for SSL only"
echo "  - HTTPS (443/tcp) - API traffic (composer behind nginx)"
echo "  - All other ports: BLOCKED"
echo ""
echo "⚠️  Important:"
echo "  - Make sure you can SSH before logging out!"
echo "  - If locked out, use cloud console to disable firewall:"
echo "    sudo ufw disable"
echo ""
