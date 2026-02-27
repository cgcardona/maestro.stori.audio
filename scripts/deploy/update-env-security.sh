#!/bin/bash
# Safely update .env with security settings (non-destructive)
#
# ENV file location (pick first that is set):
#   STORI_ENV_FILE  - explicit path to .env
#   INSTALL_DIR     - project root (script will use $INSTALL_DIR/.env)
#   Otherwise      - script dir's project root (../../ from scripts/deploy/)

set -e

if [ -n "$STORI_ENV_FILE" ]; then
    ENV_FILE="$STORI_ENV_FILE"
elif [ -n "$INSTALL_DIR" ]; then
    ENV_FILE="$INSTALL_DIR/.env"
else
    ENV_FILE="$(cd "$(dirname "$0")/../.." && pwd)/.env"
fi

echo "=========================================="
echo "Update .env with Security Settings"
echo "=========================================="
echo ""

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ ERROR: $ENV_FILE not found"
    echo "   Set STORI_ENV_FILE or INSTALL_DIR to point at your project root, or run from repo."
    exit 1
fi

echo "Current .env location: $ENV_FILE"
echo ""

# Backup existing .env
cp "$ENV_FILE" "$ENV_FILE.backup.$(date +%Y%m%d_%H%M%S)"
echo "✅ Backed up existing .env"

# Check if CORS is already configured
if grep -q "^STORI_CORS_ORIGINS=" "$ENV_FILE" 2>/dev/null; then
    echo "ℹ️  STORI_CORS_ORIGINS already set in .env"
    current_cors=$(grep "^STORI_CORS_ORIGINS=" "$ENV_FILE")
    echo "   Current value: $current_cors"
    
    if [[ "$current_cors" == *"*"* ]]; then
        echo ""
        echo "⚠️  WARNING: CORS is set to wildcard (*)"
        echo "   This is a SECURITY RISK in production!"
        echo ""
        echo "   Recommended value:"
        echo "   STORI_CORS_ORIGINS=https://your-domain.com,stori://"
        echo ""
        read -p "   Update now? (yes/no): " update_cors
        if [ "$update_cors" = "yes" ]; then
            read -p "   Enter CORS origins (comma-separated): " new_cors
            sed -i "s|^STORI_CORS_ORIGINS=.*|STORI_CORS_ORIGINS=$new_cors|" "$ENV_FILE"
            echo "   ✅ CORS updated"
        fi
    else
        echo "   ✅ CORS properly configured (not wildcard)"
    fi
else
    # CORS not set - add it
    echo ""
    echo "ℹ️  STORI_CORS_ORIGINS not found in .env"
    read -p "   Add CORS configuration? (yes/no): " add_cors
    if [ "$add_cors" = "yes" ]; then
        cat >> "$ENV_FILE" << 'EOF'

# =============================================================================
# SECURITY: CORS Configuration (Added by update-env-security.sh)
# =============================================================================
# NEVER use "*" in production! Specify exact origins.
# Comma-separated list of allowed origins
STORI_CORS_ORIGINS=https://your-domain.com,stori://
EOF
        echo "   ✅ CORS configuration added"
    fi
fi

echo ""
echo "=========================================="
echo "✅ .env update complete"
echo "=========================================="
echo ""
echo "Review changes:"
echo "  diff $ENV_FILE.backup.* $ENV_FILE"
echo ""
echo "⚠️  You must restart services for changes to take effect:"
echo "  cd $(dirname "$ENV_FILE") && docker compose restart"
echo ""
