#!/bin/bash
# Script to update Buster Vibe Bot code and restart services
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

# Get directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

log "Stopping services..."
systemctl stop buster_admin || true
systemctl stop buster_bot || true
sleep 2

log "Copying files from $SOURCE_DIR to $PROJECT_DIR..."
# Copy all files, excluding venv and .git
rsync -av --exclude 'venv' --exclude '.git' --exclude '__pycache__' --exclude '.env' "$SOURCE_DIR/" "$PROJECT_DIR/"

log "Setting permissions..."
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR"
chmod +x "$PROJECT_DIR/scripts/"*.sh

log "Installing/updating dependencies..."
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# Update systemd services
log "Updating systemd services..."
cat > /etc/systemd/system/buster_bot.service << EOF
[Unit]
Description=Buster Vibe Telegram Bot
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/venv/bin/python main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/buster_admin.service << EOF
[Unit]
Description=Buster Vibe Admin Panel
After=network.target postgresql.service

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
Environment="PYTHONPATH=$PROJECT_DIR"
# 2 workers prevent single request from blocking entire app
ExecStart=$PROJECT_DIR/venv/bin/uvicorn admin_panel.app:app --host 127.0.0.1 --port 8000 --workers 2 --timeout-keep-alive 120
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# Update nginx config if exists
if [[ -f /etc/nginx/sites-available/buster ]]; then
    log "Updating nginx timeouts..."
    # Check if timeouts already configured
    if ! grep -q "proxy_read_timeout" /etc/nginx/sites-available/buster; then
        # Add timeouts after client_max_body_size line
        sed -i '/client_max_body_size/a\    \n    # Timeouts - prevent 502 on slow operations\n    proxy_connect_timeout 30s;\n    proxy_send_timeout 120s;\n    proxy_read_timeout 120s;\n    send_timeout 120s;' /etc/nginx/sites-available/buster
        nginx -t && systemctl reload nginx
        log "✅ Nginx timeouts configured"
    else
        log "Nginx timeouts already configured"
    fi
fi

log "Starting services..."
systemctl start buster_bot
sleep 3
systemctl start buster_admin

log "=== Update Complete! ==="
echo ""
systemctl is-active --quiet buster_bot && log "✅ Bot is running" || warn "⚠️ Bot may have issues"
systemctl is-active --quiet buster_admin && log "✅ Admin panel is running" || warn "⚠️ Admin panel may have issues"
echo ""
log "Check logs: sudo journalctl -u buster_admin -f"
