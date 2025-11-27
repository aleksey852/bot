#!/bin/bash
# Quick fix for 502 errors - updates systemd and nginx without full code update
# Usage: sudo bash scripts/fix_502.sh

set -e

PROJECT_DIR="/opt/buster-vibe-bot"
SERVICE_USER="buster"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

[[ $EUID -ne 0 ]] && { echo -e "${RED}Run as root: sudo bash scripts/fix_502.sh${NC}"; exit 1; }

echo "=== Fixing 502 errors ==="

# 1. Stop services
log "Stopping services..."
systemctl stop buster_admin || true
sleep 2

# 2. Update .env - reduce pool size to prevent connection exhaustion
log "Optimizing database pool settings..."
if grep -q "DB_POOL_MIN=5" "$PROJECT_DIR/.env"; then
    sed -i 's/DB_POOL_MIN=5/DB_POOL_MIN=2/' "$PROJECT_DIR/.env"
    sed -i 's/DB_POOL_MAX=20/DB_POOL_MAX=10/' "$PROJECT_DIR/.env"
    log "✅ Reduced DB pool size (was 5-20, now 2-10)"
fi

# 3. Update systemd - add workers
log "Updating systemd service (adding workers)..."
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

# 4. Update nginx timeouts
log "Updating nginx timeouts..."
NGINX_CONF="/etc/nginx/sites-available/buster"

if [[ -f "$NGINX_CONF" ]]; then
    # Backup original
    cp "$NGINX_CONF" "${NGINX_CONF}.bak"
    
    # Check if timeouts exist
    if ! grep -q "proxy_read_timeout" "$NGINX_CONF"; then
        # Create new config with timeouts
        cat > "$NGINX_CONF" << 'NGINX_EOF'
server {
    listen 80;
    server_name _;
    
    client_max_body_size 10M;
    
    # Timeouts - prevent 502 on slow operations
    proxy_connect_timeout 30s;
    proxy_send_timeout 120s;
    proxy_read_timeout 120s;
    send_timeout 120s;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Buffering settings
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
    }
}
NGINX_EOF
        
        # Restore server_name from backup
        OLD_SERVER=$(grep "server_name" "${NGINX_CONF}.bak" | head -1 | sed 's/.*server_name\s*\(.*\);/\1/')
        if [[ -n "$OLD_SERVER" && "$OLD_SERVER" != "_" ]]; then
            sed -i "s/server_name _;/server_name $OLD_SERVER;/" "$NGINX_CONF"
        fi
        
        nginx -t && systemctl reload nginx
        log "✅ Nginx timeouts configured"
    else
        log "Nginx timeouts already configured"
    fi
else
    warn "Nginx config not found at $NGINX_CONF"
fi

# 5. Restart services
log "Starting services..."
systemctl start buster_admin

sleep 3

# 6. Verify
echo ""
if systemctl is-active --quiet buster_admin; then
    log "✅ Admin panel is running"
    
    # Test endpoint
    if command -v curl &> /dev/null; then
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://127.0.0.1:8000/login 2>/dev/null || echo "000")
        if [[ "$HTTP_CODE" == "200" ]]; then
            log "✅ Admin panel responds OK (HTTP $HTTP_CODE)"
        else
            warn "⚠️ Admin panel returned HTTP $HTTP_CODE"
        fi
    fi
else
    warn "⚠️ Admin panel may have issues"
fi

echo ""
log "=== Fix Complete ==="
echo ""
echo "Test the admin panel now. If still 502:"
echo "1. Check logs: sudo journalctl -u buster_admin -f"
echo "2. Check PostgreSQL: sudo -u postgres psql -c 'SELECT count(*) FROM pg_stat_activity;'"
echo "3. Check nginx: sudo tail -f /var/log/nginx/error.log"
