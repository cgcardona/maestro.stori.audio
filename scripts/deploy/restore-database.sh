#!/bin/bash
# =============================================================================
# Maestro - Database Restore Script
# =============================================================================
#
# This script restores the PostgreSQL database from a backup file.
#
# Usage:
#   ./deploy/restore-database.sh [backup_file]
#
# If no backup file is specified, lists available backups.
#
# =============================================================================

set -e

# Run from project root (where docker-compose.yml lives)
cd "$(cd "$(dirname "$0")/../.." && pwd)"

BACKUP_DIR="/var/backups/maestro"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# =============================================================================
# List available backups if no file specified
# =============================================================================
if [ $# -eq 0 ]; then
    log_info "Available backups in $BACKUP_DIR:"
    echo ""
    ls -lh "$BACKUP_DIR"/stori_backup_*.sql.gz 2>/dev/null || echo "No backups found"
    echo ""
    log_info "Usage: $0 <backup_file>"
    exit 0
fi

BACKUP_FILE="$1"

# Check if file exists
if [ ! -f "$BACKUP_FILE" ] && [ ! -f "$BACKUP_DIR/$BACKUP_FILE" ]; then
    log_error "Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Use full path if relative path given
if [ ! -f "$BACKUP_FILE" ]; then
    BACKUP_FILE="$BACKUP_DIR/$BACKUP_FILE"
fi

log_warn "This will REPLACE the current database with the backup!"
log_warn "Backup file: $BACKUP_FILE"
echo ""
read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    log_info "Restore cancelled"
    exit 0
fi

# =============================================================================
# Backup current database before restore
# =============================================================================
log_info "Creating safety backup of current database..."
SAFETY_BACKUP="$BACKUP_DIR/pre_restore_$(date +%Y%m%d_%H%M%S).sql.gz"
docker compose exec -T postgres pg_dump -U stori -d stori | gzip > "$SAFETY_BACKUP"
log_info "Safety backup created: $SAFETY_BACKUP"

# =============================================================================
# Restore database
# =============================================================================
log_info "Restoring database..."

# Stop the maestro service to prevent connections
log_info "Stopping maestro service..."
docker compose stop maestro

# Drop and recreate database
log_info "Dropping and recreating database..."
docker compose exec -T postgres psql -U stori -d postgres << EOF
DROP DATABASE IF EXISTS stori;
CREATE DATABASE stori;
EOF

# Restore from backup
log_info "Restoring from backup file..."
gunzip < "$BACKUP_FILE" | docker compose exec -T postgres psql -U stori -d stori

# Restart maestro service
log_info "Restarting maestro service..."
docker compose start maestro

log_info "Database restore complete!"
log_info "Safety backup saved at: $SAFETY_BACKUP"
