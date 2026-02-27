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

echo "This will remove the systemd unit (the one that runs 'docker compose up -d' on boot)."
echo "Your containers will stop if you run 'systemctl stop ' or after a reboot the stack will no longer start automatically."
echo "To stop the stack now, run: docker compose down (from the project root)."
echo ""
read -p "Remove the systemd unit? [y/N]: " confirm

if [[ ! $confirm =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""

if [ -f "/etc/systemd/system/maestro.service" ]; then
    echo "🗑️  Removing maestro..."

    if systemctl is-active --quiet  2>/dev/null; then
        echo "   Stopping..."
        systemctl stop 
    fi

    if systemctl is-enabled --quiet  2>/dev/null; then
        echo "   Disabling..."
        systemctl disable 
    fi

    rm -f "/etc/systemd/system/maestro.service"
    echo "   ✓ removed"
else
    echo "⏭️  maestro.service not installed, skipping"
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
