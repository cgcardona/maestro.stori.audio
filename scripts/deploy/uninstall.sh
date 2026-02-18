#!/bin/bash
set -e

# Maestro Stori Audio - Remove systemd unit (Docker Compose on boot)

echo "==================================="
echo "Maestro Stori Audio - Uninstall"
echo "==================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run with sudo: sudo ./uninstall.sh"
    exit 1
fi

echo "This will remove the maestro-stori systemd unit (the one that runs 'docker compose up -d' on boot)."
echo "Your containers will stop if you run 'systemctl stop maestro-stori' or after a reboot the stack will no longer start automatically."
echo "To stop the stack now, run: docker compose down (from the project root)."
echo ""
read -p "Remove the systemd unit? [y/N]: " confirm

if [[ ! $confirm =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""

if [ -f "/etc/systemd/system/maestro-stori.service" ]; then
    echo "🗑️  Removing maestro-stori..."

    if systemctl is-active --quiet maestro-stori 2>/dev/null; then
        echo "   Stopping..."
        systemctl stop maestro-stori
    fi

    if systemctl is-enabled --quiet maestro-stori 2>/dev/null; then
        echo "   Disabling..."
        systemctl disable maestro-stori
    fi

    rm -f "/etc/systemd/system/maestro-stori.service"
    echo "   ✓ maestro-stori removed"
else
    echo "⏭️  maestro-stori.service not installed, skipping"
fi

echo ""
echo "🔄 Reloading systemd daemon..."
systemctl daemon-reload
echo "   ✓ Daemon reloaded"
echo ""
echo "==================================="
echo "Uninstall complete."
echo "==================================="
echo ""
