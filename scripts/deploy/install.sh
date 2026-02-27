#!/bin/bash
set -e

# Maestro Stori Audio - Install systemd unit that runs "docker compose up -d" on boot
# The app runs inside Docker; this unit only starts the Compose stack at boot.

echo "==================================="
echo "Maestro Stori Audio (Docker) - Install"
echo "==================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run with sudo: sudo ./install.sh"
    exit 1
fi

# Detect project root (script lives in scripts/deploy/)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"

echo "Project root: $PROJECT_ROOT"
echo ""

SERVICE_FILE="$PROJECT_ROOT/deploy/systemd/maestro-stori.service"
if [ ! -f "$SERVICE_FILE" ]; then
    echo "❌ Service file not found: $SERVICE_FILE"
    exit 1
fi

echo "📦 Installing maestro-stori (Docker Compose on boot)..."
sed "s|/opt/maestro|$PROJECT_ROOT|g" \
    "$SERVICE_FILE" > "/etc/systemd/system/maestro-stori.service"
chmod 644 "/etc/systemd/system/maestro-stori.service"
echo "   ✓ maestro-stori.service installed"
echo ""

echo "🔄 Reloading systemd daemon..."
systemctl daemon-reload
echo "   ✓ Daemon reloaded"
echo ""

read -p "Enable maestro-stori to start Docker Compose on boot? [Y/n]: " enable_choice
enable_choice=${enable_choice:-Y}

if [[ $enable_choice =~ ^[Yy]$ ]]; then
    systemctl enable maestro-stori
    echo "   ✓ maestro-stori enabled"
    echo ""
fi

read -p "Start the stack now (docker compose up -d)? [Y/n]: " start_choice
start_choice=${start_choice:-Y}

if [[ $start_choice =~ ^[Yy]$ ]]; then
    systemctl start maestro-stori
    echo "   ✓ Stack started"
    echo ""
fi

echo "==================================="
echo "Done!"
echo "==================================="
echo ""
echo "  Status:   sudo systemctl status maestro-stori"
echo "  Logs:     docker compose logs -f    (from $PROJECT_ROOT)"
echo "  Restart:  docker compose restart     (from $PROJECT_ROOT)"
echo "  Stop:     sudo systemctl stop maestro-stori"
echo ""
