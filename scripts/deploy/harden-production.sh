#!/bin/bash
# Complete production security hardening for Stori
# Run this script once on initial deployment.
#
# Usage: sudo ./harden-production.sh [--email YOUR_EMAIL]
#   --email  Email for fail2ban and AIDE reports (default: admin@example.com; set for production)

set -e

ALERT_EMAIL="admin@example.com"
while [[ $# -gt 0 ]]; do
    case $1 in
        --email)
            ALERT_EMAIL="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--email YOUR_EMAIL]"
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "Stori Production Security Hardening"
echo "=========================================="
echo ""

if [ "$EUID" -ne 0 ]; then 
    echo "ERROR: Must run as root"
    exit 1
fi

# Confirm before proceeding
read -p "This will harden security and may lock you out if misconfigured. Continue? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo "1. Installing security packages..."
apt-get update -qq
apt-get install -y \
    ufw \
    fail2ban \
    unattended-upgrades \
    apt-listchanges

echo ""
echo "2. Configuring automatic security updates..."
cat > /etc/apt/apt.conf.d/50unattended-upgrades <<'EOF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF

systemctl enable unattended-upgrades
systemctl restart unattended-upgrades

echo ""
echo "3. Setting up firewall..."
bash "$(dirname "$0")/setup-firewall.sh"

echo ""
echo "4. Setting up fail2ban..."
bash "$(dirname "$0")/setup-fail2ban.sh" --email "$ALERT_EMAIL"

echo ""
echo "5. Hardening SSH..."
# Backup original
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup

# Harden SSH config
sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config

# Add if not present
grep -q "^MaxAuthTries" /etc/ssh/sshd_config || echo "MaxAuthTries 3" >> /etc/ssh/sshd_config
grep -q "^ClientAliveInterval" /etc/ssh/sshd_config || echo "ClientAliveInterval 300" >> /etc/ssh/sshd_config
grep -q "^ClientAliveCountMax" /etc/ssh/sshd_config || echo "ClientAliveCountMax 2" >> /etc/ssh/sshd_config

systemctl restart sshd

echo ""
echo "6. Setting up automated backups..."
# Use existing backup script
BACKUP_SCRIPT="$(cd "$(dirname "$0")" && pwd)/backup-database.sh"

if [ -f "$BACKUP_SCRIPT" ]; then
    chmod +x "$BACKUP_SCRIPT"
    # Add to crontab (daily at 2 AM) if not already there
    if ! crontab -l 2>/dev/null | grep -q "backup-database.sh"; then
        (crontab -l 2>/dev/null; echo "0 2 * * * $BACKUP_SCRIPT >> /var/log/maestro-stori-backup.log 2>&1") | crontab -
        echo "   ✅ Daily backup cron job added (2 AM)"
    else
        echo "   ✅ Backup cron job already exists"
    fi
else
    echo "   ⚠️  Backup script not found at $BACKUP_SCRIPT"
fi

echo ""
echo "7. Setting kernel security parameters..."
cat > /etc/sysctl.d/99-maestro-stori-security.conf <<'EOF'
# Prevent SYN flood attacks
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_synack_retries = 2

# Ignore ICMP redirects
net.ipv4.conf.all.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0

# Ignore source routed packets
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0

# Enable IP spoofing protection
net.ipv4.conf.all.rp_filter = 1

# Log martian packets
net.ipv4.conf.all.log_martians = 1

# Disable IPv6 if not needed (comment out if you need IPv6)
# net.ipv6.conf.all.disable_ipv6 = 1
# net.ipv6.conf.default.disable_ipv6 = 1

# Protect against tcp time-wait assassination hazards
net.ipv4.tcp_rfc1337 = 1
EOF

sysctl -p /etc/sysctl.d/99-maestro-stori-security.conf

echo ""
echo "8. Setting up file integrity monitoring..."
# Install AIDE
apt-get install -y aide
aideinit
mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db

# Add weekly integrity check
(crontab -l 2>/dev/null | grep -v aide.check; echo "0 3 * * 0 /usr/bin/aide --check | mail -s 'AIDE Report' $ALERT_EMAIL") | crontab -

echo ""
echo "=========================================="
echo "✅ Production Security Hardening Complete!"
echo "=========================================="
echo ""
echo "Summary:"
echo "  - Firewall: Active (SSH, HTTP, HTTPS only)"
echo "  - fail2ban: Monitoring SSH, nginx"
echo "  - SSH: Hardened (no root, no password)"
echo "  - Auto updates: Enabled (security only)"
echo "  - Backups: Daily at 2 AM"
echo "  - Integrity monitoring: Weekly"
echo "  - Kernel: Hardened parameters"
echo ""
echo "⚠️  IMPORTANT:"
echo "  1. Verify you can still SSH (open new terminal, don't close this one!)"
echo "  2. Set STORI_CORS_ORIGINS in .env (remove wildcard)"
echo "  3. Test all endpoints after redeploying containers"
echo "  4. Set up monitoring alerts"
echo ""
echo "Next steps:"
echo "  - Review: tail -f /var/log/fail2ban.log"
echo "  - Monitor: docker logs -f maestro-stori-app"
echo "  - Backups: ls -lh /var/backups/stori"
echo ""
