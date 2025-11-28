#!/bin/bash
# Universal Update Script for Buster Vibe Bot
# Usage: sudo bash scripts/update.sh

set -e

PROJECT_DIR="/opt/buster-vibe-bot"
SERVICE_USER="buster"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# Check root
if [[ $EUID -ne 0 ]]; then
   err "This script must be run as root: sudo bash scripts/update.sh"
fi

echo "=== Updating Buster Vibe Bot ==="

# 1. Update Code
log "Updating code..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

# If running from within the project dir (git repo), pull changes
if [ -d "$PROJECT_DIR/.git" ]; then
    log "Pulling latest changes from git..."
    cd "$PROJECT_DIR"
    # Fix ownership to allow git pull as root (safe in this context)
    git config --global --add safe.directory "$PROJECT_DIR"
    git pull origin main || warn "Git pull failed, continuing with local files..."
    cd - > /dev/null
elif [ -d "$SOURCE_DIR/.git" ]; then
    # If source is a git repo but target isn't (or we are deploying from a separate dir)
    log "Syncing files from $SOURCE_DIR to $PROJECT_DIR..."
    rsync -av --exclude 'venv' --exclude '.git' --exclude '__pycache__' --exclude '.env' "$SOURCE_DIR/" "$PROJECT_DIR/"
else
    # Fallback: just rsync
    log "Syncing files from $SOURCE_DIR to $PROJECT_DIR..."
    rsync -av --exclude 'venv' --exclude '.git' --exclude '__pycache__' --exclude '.env' "$SOURCE_DIR/" "$PROJECT_DIR/"
fi

# 2. Permissions
log "Setting permissions..."
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR"
chmod +x "$PROJECT_DIR/scripts/"*.sh

# 3. Dependencies
log "Updating dependencies..."
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    sudo -u "$SERVICE_USER" "$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
fi

# 4. Migrations (Texts)
log "Running migrations..."
if [ -f "$PROJECT_DIR/scripts/migrate_texts.py" ]; then
    log "Migrating texts..."
    cd "$PROJECT_DIR"
    sudo -u "$SERVICE_USER" "$PROJECT_DIR/venv/bin/python" scripts/migrate_texts.py || warn "Text migration failed"
    cd - > /dev/null
fi

# 5. Server Optimization Check
if [ ! -f /swapfile ] && [ $(free -m | awk '/^Mem:/{print $2}') -lt 2000 ]; then
    warn "Low memory detected and no swapfile found."
    warn "Run 'sudo bash scripts/deploy.sh' to apply server optimizations."
fi

# 6. Restart Services
log "Restarting services..."
systemctl restart buster_bot
systemctl restart buster_admin

# 7. Check Status
sleep 3
systemctl is-active --quiet buster_bot && log "✅ Bot is running" || warn "⚠️ Bot may have issues"
systemctl is-active --quiet buster_admin && log "✅ Admin panel is running" || warn "⚠️ Admin panel may have issues"

log "=== Update Complete! ==="
