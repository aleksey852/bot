#!/bin/bash
# Database Backup Script for Buster Vibe Bot
# Usage: sudo bash scripts/backup.sh

set -e

BACKUP_DIR="/var/backups/buster-vibe-bot"
PROJECT_DIR="/opt/buster-vibe-bot"
RETENTION_DAYS=14
MIN_FREE_SPACE_MB=500  # Minimum 500MB free space required

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# Check if running as root (optional, but recommended for system backups)
# [[ $EUID -ne 0 ]] && warn "Not running as root. Ensure you have write permissions to BACKUP_DIR."

# Allow overriding BACKUP_DIR from first argument
if [ -n "$1" ]; then
    BACKUP_DIR="$1"
fi

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Check if backup directory is writable
if [ ! -w "$BACKUP_DIR" ]; then
    err "Backup directory $BACKUP_DIR is not writable!"
fi

# Check free disk space
BACKUP_PARTITION=$(df "$BACKUP_DIR" | tail -1 | awk '{print $6}')
FREE_SPACE_MB=$(df -m "$BACKUP_DIR" | tail -1 | awk '{print $4}')

log "=== Database Backup Started ==="
log "Backup directory: $BACKUP_DIR"
log "Free space: ${FREE_SPACE_MB}MB"

if [ "$FREE_SPACE_MB" -lt "$MIN_FREE_SPACE_MB" ]; then
    err "Insufficient disk space! Free: ${FREE_SPACE_MB}MB, Required: ${MIN_FREE_SPACE_MB}MB"
fi

# Timestamp for backup
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/backup_${TIMESTAMP}.sql"

# Get database credentials from .env
if [ -f "$PROJECT_DIR/.env" ]; then
    source "$PROJECT_DIR/.env"
else
    warn ".env file not found, using defaults"
    DATABASE_URL="postgresql://buster:password@localhost:5432/buster_bot"
fi

# Extract DB connection details from DATABASE_URL
DB_NAME=$(echo $DATABASE_URL | sed -n 's/.*\/\([^?]*\).*/\1/p')
DB_USER=$(echo $DATABASE_URL | sed -n 's/.*:\/\/\([^:]*\):.*/\1/p')
DB_HOST=$(echo $DATABASE_URL | sed -n 's/.*@\([^:]*\):.*/\1/p')
DB_PORT=$(echo $DATABASE_URL | sed -n 's/.*:\([0-9]*\)\/.*/\1/p')

if [ -z "$DB_NAME" ]; then
    DB_NAME="buster_bot"
fi
if [ -z "$DB_HOST" ]; then
    DB_HOST="localhost"
fi
if [ -z "$DB_PORT" ]; then
    DB_PORT="5432"
fi

log "Database: $DB_NAME @ $DB_HOST:$DB_PORT"

# Perform backup
PGPASSWORD=$(echo $DATABASE_URL | sed -n 's/.*:\/\/[^:]*:\([^@]*\)@.*/\1/p') \
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    --format=plain \
    --no-owner \
    --no-acl \
    > "$BACKUP_FILE" 2>&1 || err "Backup failed!"

# Compress backup
gzip "$BACKUP_FILE"
BACKUP_FILE="${BACKUP_FILE}.gz"

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
log "✅ Backup created: $BACKUP_FILE"
log "   Size: $BACKUP_SIZE"

# Backup .env file (contains important config)
ENV_BACKUP="$BACKUP_DIR/env_${TIMESTAMP}.txt"
if [ -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env" "$ENV_BACKUP"
    chmod 600 "$ENV_BACKUP"
    log "✅ .env backed up"
fi

# Clean old backups (older than RETENTION_DAYS)
log "Cleaning backups older than ${RETENTION_DAYS} days..."
DELETED_COUNT=0

# Delete old SQL backups
for file in $(find "$BACKUP_DIR" -name "backup_*.sql.gz" -mtime +${RETENTION_DAYS}); do
    rm -f "$file"
    DELETED_COUNT=$((DELETED_COUNT + 1))
done

# Delete old .env backups
for file in $(find "$BACKUP_DIR" -name "env_*.txt" -mtime +${RETENTION_DAYS}); do
    rm -f "$file"
    DELETED_COUNT=$((DELETED_COUNT + 1))
done

if [ $DELETED_COUNT -gt 0 ]; then
    log "✅ Deleted $DELETED_COUNT old backup(s)"
fi

BACKUP_COUNT=$(find "$BACKUP_DIR" -name "backup_*.sql.gz" | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)

echo ""
log "=== Backup Complete ==="
log "Total backups: $BACKUP_COUNT (${RETENTION_DAYS}-day retention)"
log "Total size: $TOTAL_SIZE"
log "Free space remaining: $(df -h "$BACKUP_DIR" | tail -1 | awk '{print $4}')"
echo ""
log "To restore from this backup:"
log "  sudo systemctl stop buster_bot buster_admin"
log "  gunzip -c $BACKUP_FILE | sudo -u postgres psql $DB_NAME"
log "  sudo systemctl start buster_bot buster_admin"

