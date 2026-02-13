#!/bin/bash
# Setup fail2ban for SSH brute force protection
#
# Usage: sudo ./setup-fail2ban.sh [--email YOUR_EMAIL]
#   --email  Email for fail2ban alerts (default: admin@example.com placeholder; set for production)

set -e

# Default placeholder; pass --email for production alerts
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
echo "Stori fail2ban Setup"
echo "=========================================="
echo ""

if [ "$EUID" -ne 0 ]; then 
    echo "ERROR: Must run as root"
    exit 1
fi

echo "1. Installing fail2ban..."
apt-get update -qq
apt-get install -y fail2ban

echo ""
echo "2. Creating jail configuration..."

# Create local jail config (ALERT_EMAIL is expanded)
cat > /etc/fail2ban/jail.local <<EOF
[DEFAULT]
# Ban for 1 hour after 5 failed attempts in 10 minutes
bantime = 3600
findtime = 600
maxretry = 5
destemail = ${ALERT_EMAIL}
sender = ${ALERT_EMAIL}

[sshd]
enabled = true
port = 22
logpath = /var/log/auth.log
maxretry = 5

[nginx-http-auth]
enabled = true
port = 80,443
logpath = /var/log/nginx/error.log

[nginx-limit-req]
enabled = true
port = 80,443
logpath = /var/log/nginx/error.log
maxretry = 10
EOF

echo ""
echo "3. Starting fail2ban..."
systemctl enable fail2ban
systemctl restart fail2ban

echo ""
echo "✅ fail2ban configured successfully!"
if [ "$ALERT_EMAIL" = "admin@example.com" ]; then
    echo ""
    echo "Note: Alert email is the default placeholder. For production, re-run with: $0 --email your@email.com"
fi
echo ""
echo "Status:"
fail2ban-client status

echo ""
echo "Monitor with:"
echo "  sudo fail2ban-client status sshd"
echo "  sudo tail -f /var/log/fail2ban.log"
echo ""
