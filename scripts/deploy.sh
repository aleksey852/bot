#!/bin/bash
# Buster Vibe Bot - Deploy Script v2.2 (with workers + timeouts fix)
# Usage: sudo bash scripts/deploy.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
err() { echo -e "${RED}[!]${NC} $1"; exit 1; }

# Check root
[[ $EUID -ne 0 ]] && err "Run as root: sudo bash scripts/deploy.sh"

PROJECT_DIR="/opt/buster-vibe-bot"
SERVICE_USER="buster"

log "=== Buster Vibe Bot Deploy v2.2 ==="

# 1. System packages
log "Installing system packages..."
apt-get update
apt-get install -y python3 python3-pip python3-venv postgresql postgresql-contrib redis-server nginx certbot python3-certbot-nginx

# 1.5 Server Optimization (for 1GB RAM)
log "Optimizing server for 1GB RAM..."

# Swap
if [ ! -f /swapfile ]; then
    log "Creating 2GB Swap..."
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Sysctl
log "Tuning sysctl..."
cat > /etc/sysctl.d/99-buster-optimization.conf << EOF
vm.swappiness=10
vm.overcommit_memory=1
EOF
sysctl -p /etc/sysctl.d/99-buster-optimization.conf

# PostgreSQL Tuning
log "Tuning PostgreSQL..."
PG_CONF=$(find /etc/postgresql -name postgresql.conf | head -n 1)
if [ -n "$PG_CONF" ]; then
    # 128MB shared buffers (1/8 of RAM is conservative but safe for 1GB)
    sed -i "s/#shared_buffers = 128MB/shared_buffers = 128MB/" "$PG_CONF"
    sed -i "s/shared_buffers = .*/shared_buffers = 128MB/" "$PG_CONF"
    # Reduce max connections to save memory
    sed -i "s/#max_connections = 100/max_connections = 50/" "$PG_CONF"
    sed -i "s/max_connections = .*/max_connections = 50/" "$PG_CONF"
    systemctl restart postgresql
fi

# Redis Tuning
log "Tuning Redis..."
REDIS_CONF="/etc/redis/redis.conf"
if [ -f "$REDIS_CONF" ]; then
    # Limit Redis to 256MB
    sed -i "s/# maxmemory <bytes>/maxmemory 256mb/" "$REDIS_CONF"
    sed -i "s/# maxmemory-policy noeviction/maxmemory-policy allkeys-lru/" "$REDIS_CONF"
    systemctl restart redis-server
fi

# 2. Create service user
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$SERVICE_USER"
    log "Created user: $SERVICE_USER"
fi

# 3. Copy project
log "Setting up project..."
mkdir -p "$PROJECT_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

if [[ -f "$SOURCE_DIR/main.py" ]]; then
    cp -r "$SOURCE_DIR"/* "$PROJECT_DIR/"
fi

# 4. Get config from user
echo ""
read -p "Bot Token: " BOT_TOKEN
read -p "Admin Telegram IDs (comma-separated): " ADMIN_IDS
read -p "ProverkaCheka Token: " API_TOKEN
read -p "Domain (or press Enter for IP only): " DOMAIN
read -p "Promo Name [Buster Vibe]: " PROMO_NAME
PROMO_NAME=${PROMO_NAME:-Buster Vibe}

# Generate passwords
DB_PASS=$(openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 32)
ADMIN_PASS=$(openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 32)
SECRET_KEY=$(openssl rand -base64 48 | tr -dc 'a-zA-Z0-9' | head -c 48)

# 5. Setup PostgreSQL
log "Setting up PostgreSQL..."
cd /tmp
sudo -u postgres psql -c "CREATE USER buster WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
sudo -u postgres psql -c "ALTER USER buster WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -c "CREATE DATABASE buster_bot OWNER buster;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE buster_bot TO buster;"
cd - > /dev/null

# 6. Create .env
log "Creating .env..."
cat > "$PROJECT_DIR/.env" << EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
DATABASE_URL=postgresql://buster:$DB_PASS@127.0.0.1:5432/buster_bot
REDIS_URL=redis://localhost:6379/0
PROVERKA_CHEKA_TOKEN=$API_TOKEN
PROMO_NAME=$PROMO_NAME
PROMO_START_DATE=$(date +%Y-%m-%d)
PROMO_END_DATE=$(date -d "+90 days" +%Y-%m-%d)
PROMO_PRIZES=iPhone 15,AirPods,Сертификаты 5000₽
TARGET_KEYWORDS=чипсы,buster,vibe
SUPPORT_EMAIL=support@example.com
SUPPORT_TELEGRAM=@support
ADMIN_PANEL_USER=admin
ADMIN_PANEL_PASSWORD=$ADMIN_PASS
ADMIN_SECRET_KEY=$SECRET_KEY
TIMEZONE=Europe/Moscow
LOG_LEVEL=INFO
SCHEDULER_INTERVAL=30
MESSAGE_DELAY_SECONDS=0.05
BROADCAST_BATCH_SIZE=20
DB_POOL_MIN=2
DB_POOL_MAX=10
STATS_CACHE_TTL=60
RECEIPTS_RATE_LIMIT=50
RECEIPTS_DAILY_LIMIT=200
METRICS_ENABLED=true
METRICS_PORT=9090
EOF
chmod 600 "$PROJECT_DIR/.env"

# 7. Python venv
log "Setting up Python environment..."
python3 -m venv "$PROJECT_DIR/venv"
"$PROJECT_DIR/venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# 8. Set ownership
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR"

# 9. Create systemd services (v2.2: workers + timeout)
log "Creating systemd services..."

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
# FIXED: 2 workers prevent single request from blocking entire app
# --timeout-graceful-shutdown gives time for long operations
ExecStart=$PROJECT_DIR/venv/bin/uvicorn admin_panel.app:app --host 127.0.0.1 --port 8000 --workers 2 --timeout-keep-alive 120
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable buster_bot buster_admin
systemctl restart buster_bot buster_admin

# 10. Setup Nginx (if domain provided) - WITH TIMEOUTS
if [[ -n "$DOMAIN" ]]; then
    log "Setting up Nginx for $DOMAIN..."
    cat > /etc/nginx/sites-available/buster << EOF
server {
    listen 80;
    server_name $DOMAIN;
    
    client_max_body_size 10M;
    
    # Timeouts - prevent 502 on slow operations
    proxy_connect_timeout 30s;
    proxy_send_timeout 120s;
    proxy_read_timeout 120s;
    send_timeout 120s;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # WebSocket support (for future)
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
    ln -sf /etc/nginx/sites-available/buster /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx
    
    log "Getting SSL certificate..."
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" || true
fi

# 11. Save credentials
cat > /root/.buster_credentials << EOF
=== Buster Vibe Bot Credentials ===
Database: buster_bot
Database User: buster
Database Password: $DB_PASS

Admin Panel URL: $([ -n "$DOMAIN" ] && echo "https://$DOMAIN" || echo "http://$(hostname -I | awk '{print $1}'):8000")
Admin Panel Login: admin
Admin Panel Password: $ADMIN_PASS
Secret Key: $SECRET_KEY

=== Commands ===
Bot Status:    sudo systemctl status buster_bot
Bot Logs:      sudo journalctl -u buster_bot -f
Bot Restart:   sudo systemctl restart buster_bot

Panel Status:  sudo systemctl status buster_admin
Panel Logs:    sudo journalctl -u buster_admin -f
Panel Restart: sudo systemctl restart buster_admin

Database:      sudo -u postgres psql buster_bot

=== Config ===
.env file:     $PROJECT_DIR/.env
Project dir:   $PROJECT_DIR
EOF
chmod 600 /root/.buster_credentials

# Wait for services to start
sleep 3

# Check status
log "Checking services..."
systemctl is-active --quiet buster_bot && log "✅ Bot is running" || err "❌ Bot failed to start. Check: sudo journalctl -u buster_bot -n 50"
systemctl is-active --quiet buster_admin && log "✅ Admin panel is running" || err "❌ Admin panel failed to start. Check: sudo journalctl -u buster_admin -n 50"

# Done!
echo ""
log "=== Installation Complete! ==="
echo ""
echo -e "${CYAN}Credentials saved to:${NC} /root/.buster_credentials"
echo ""
cat /root/.buster_credentials
echo ""
log "🚀 Bot is running with PostgreSQL NOTIFY/LISTEN!"
