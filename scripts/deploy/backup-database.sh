#!/bin/bash
# =============================================================================
# Maestro - Database Backup Script
# =============================================================================
#
# This script backs up the PostgreSQL database to:
# 1. Local compressed file with timestamp
# 2. Docker volume for persistence
# 3. Optional: S3 bucket (if AWS CLI configured)
#
# Usage:
#   ./deploy/backup-database.sh
#
# Cron example (daily at 2 AM):
#   0 2 * * * /path/to/backup-database.sh >> /var/log/maestro-backup.log 2>&1
#
# =============================================================================

set -e

# Run from project root (where docker-compose.yml lives)
cd "$(cd "$(dirname "$0")/../.." && pwd)"

# Configuration
BACKUP_DIR="/var/backups/maestro"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="stori_backup_${TIMESTAMP}.sql.gz"
S3_BUCKET="${STORI_BACKUP_S3_BUCKET:-}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# =============================================================================
# Create backup directory
# =============================================================================
log_info "Creating backup directory..."
mkdir -p "$BACKUP_DIR"

# =============================================================================
# Backup PostgreSQL database
# =============================================================================
log_info "Backing up PostgreSQL database..."

if ! docker compose ps postgres | grep -q "Up"; then
    log_error "PostgreSQL container is not running!"
    exit 1
fi

# Perform backup
if docker compose exec -T postgres pg_dump -U stori -d stori | gzip > "$BACKUP_DIR/$BACKUP_FILE"; then
    BACKUP_SIZE=$(du -h "$BACKUP_DIR/$BACKUP_FILE" | cut -f1)
    log_info "Backup created successfully: $BACKUP_FILE ($BACKUP_SIZE)"
else
    log_error "Backup failed!"
    exit 1
fi

# =============================================================================
# Copy to Docker volume for additional redundancy
# =============================================================================
log_info "Copying backup to Docker volume..."
docker run --rm \
    -v maestro-postgres-data:/pgdata \
    -v "$BACKUP_DIR":/backup \
    alpine \
    cp "/backup/$BACKUP_FILE" /pgdata/ 2>/dev/null || \
    log_warn "Could not copy to Docker volume (not critical)"

# =============================================================================
# Upload to S3 (optional)
# =============================================================================
if [ -n "$S3_BUCKET" ]; then
    if command -v aws &> /dev/null; then
        log_info "Uploading to S3: s3://$S3_BUCKET/backups/$BACKUP_FILE"
        if aws s3 cp "$BACKUP_DIR/$BACKUP_FILE" "s3://$S3_BUCKET/backups/$BACKUP_FILE"; then
            log_info "S3 upload successful"
        else
            log_warn "S3 upload failed (backup still saved locally)"
        fi
    else
        log_warn "AWS CLI not installed, skipping S3 upload"
    fi
else
    log_info "S3 backup disabled (set STORI_BACKUP_S3_BUCKET to enable)"
fi

# =============================================================================
# Clean up old backups
# =============================================================================
log_info "Cleaning up backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -name "stori_backup_*.sql.gz" -type f -mtime +$RETENTION_DAYS -delete

BACKUP_COUNT=$(find "$BACKUP_DIR" -name "stori_backup_*.sql.gz" | wc -l)
log_info "Backup complete. Total backups: $BACKUP_COUNT"

# =============================================================================
# Create backup manifest
# =============================================================================
cat > "$BACKUP_DIR/latest.txt" << EOF
timestamp=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
file=$BACKUP_FILE
size=$BACKUP_SIZE
EOF

log_info "Backup manifest updated"
