#!/bin/bash
# Script to update Buster Vibe Bot code and restart services
# Usage: sudo bash scripts/update.sh

set -e

PROJECT_DIR="/opt/buster-vibe-bot"
SERVICE_USER="buster"

# Check root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root: sudo bash scripts/update.sh"
   exit 1
fi

echo "=== Updating Buster Vibe Bot ==="

# Get directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

echo "Copying files from $SOURCE_DIR to $PROJECT_DIR..."
# Copy all files, excluding venv and .git
rsync -av --exclude 'venv' --exclude '.git' --exclude '__pycache__' "$SOURCE_DIR/" "$PROJECT_DIR/"

echo "Setting permissions..."
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR"
# Ensure scripts are executable
chmod +x "$PROJECT_DIR/scripts/"*.sh

echo "Installing/updating dependencies..."
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

echo "Restarting services..."
systemctl restart buster_bot
systemctl restart buster_admin

echo "=== Update Complete! ==="
systemctl status buster_bot --no-pager | head -n 5
systemctl status buster_admin --no-pager | head -n 5
