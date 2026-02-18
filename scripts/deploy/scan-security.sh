#!/bin/bash
# Security scanning for Docker containers
# Uses Trivy for vulnerability scanning.
# If Trivy is not installed, this script installs it via the official install script
# (no deprecated apt-key; uses https://github.com/aquasecurity/trivy/contrib/install.sh).

set -e

echo "=========================================="
echo "Stori Security Scanning"
echo "=========================================="
echo ""

# Install Trivy if not present (self-contained; official method, no apt-key)
TRIVY_CMD="trivy"
if ! command -v trivy &> /dev/null; then
    echo "1. Installing Trivy..."
    INSTALL_SCRIPT="https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh"
    INSTALL_DIR="${TRIVY_INSTALL_DIR:-/usr/local/bin}"
    if curl -sfL "$INSTALL_SCRIPT" | sh -s -- -b "$INSTALL_DIR" 2>/dev/null; then
        TRIVY_CMD="$INSTALL_DIR/trivy"
        echo "   Installed to $TRIVY_CMD ✓"
    elif curl -sfL "$INSTALL_SCRIPT" | sudo sh -s -- -b /usr/local/bin 2>/dev/null; then
        TRIVY_CMD="trivy"
        echo "   Installed to /usr/local/bin ✓"
    else
        TMPBIN=$(mktemp -d)
        trap "rm -rf $TMPBIN" EXIT
        if curl -sfL "$INSTALL_SCRIPT" | sh -s -- -b "$TMPBIN" 2>/dev/null; then
            TRIVY_CMD="$TMPBIN/trivy"
            echo "   Installed to $TMPBIN (temporary) ✓"
        else
            echo "   Install failed. Install manually: https://github.com/aquasecurity/trivy#installation"
            exit 1
        fi
    fi
else
    echo "1. Trivy already installed ✓"
fi

echo ""
echo "2. Scanning maestro container..."
$TRIVY_CMD image maestrostoriaudio-maestro --severity HIGH,CRITICAL

echo ""
echo "3. Scanning orpheus container..."
$TRIVY_CMD image maestrostoriaudio-orpheus --severity HIGH,CRITICAL

echo ""
echo "4. Scanning postgres container..."
$TRIVY_CMD image postgres:16-alpine --severity HIGH,CRITICAL

echo ""
echo "5. Scanning nginx container..."
$TRIVY_CMD image nginx:alpine --severity HIGH,CRITICAL

echo ""
echo "=========================================="
echo "✅ Security scan complete"
echo "=========================================="
echo ""
echo "Review findings above and update base images or dependencies as needed."
echo ""
echo "Run this scan:"
echo "  - Before each production deployment"
echo "  - Weekly via cron"
echo "  - After any dependency updates"
echo ""
