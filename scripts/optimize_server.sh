#!/bin/bash
# Server Optimization Script
# Usage: sudo bash scripts/optimize_server.sh [RAM_GB]
# Example: sudo bash scripts/optimize_server.sh 4

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# Check root
[[ $EUID -ne 0 ]] && err "Run as root: sudo bash scripts/optimize_server.sh [RAM_GB]"

# Get RAM size (default: auto-detect or use parameter)
if [ -n "$1" ]; then
    RAM_GB=$1
    log "Using specified RAM: ${RAM_GB}GB"
else
    # Auto-detect RAM in GB
    RAM_GB=$(free -g | awk '/^Mem:/{print $2}')
    if [ "$RAM_GB" -eq 0 ]; then
        RAM_GB=1
    fi
    log "Auto-detected RAM: ${RAM_GB}GB"
fi

log "=== Server Optimization for ${RAM_GB}GB RAM ==="

# 1. Swap Configuration
log "Configuring swap..."
if [ ! -f /swapfile ]; then
    # Create swap (2x RAM or 4GB max for small servers)
    SWAP_SIZE=$((RAM_GB * 2))
    if [ $SWAP_SIZE -gt 4 ]; then
        SWAP_SIZE=4
    fi
    
    log "Creating ${SWAP_SIZE}GB swap file..."
    fallocate -l ${SWAP_SIZE}G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=$((SWAP_SIZE * 1024))
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    
    if ! grep -q "/swapfile" /etc/fstab; then
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
    log "✅ Swap created: ${SWAP_SIZE}GB"
else
    log "✅ Swap already exists"
fi

# 2. System tunables
log "Tuning sysctl..."
cat > /etc/sysctl.d/99-buster-optimization.conf << EOF
# Prefer RAM over swap (10 = use RAM when possible)
vm.swappiness=10

# Allow Redis to allocate memory
vm.overcommit_memory=1

# TCP optimization for web applications
net.core.somaxconn=1024
EOF
sysctl -p /etc/sysctl.d/99-buster-optimization.conf > /dev/null
log "✅ Sysctl tuned"

# 3. PostgreSQL Optimization
log "Optimizing PostgreSQL..."
PG_CONF=$(find /etc/postgresql -name postgresql.conf | head -n 1)

if [ -n "$PG_CONF" ]; then
    # Backup config
    cp "$PG_CONF" "${PG_CONF}.backup.$(date +%Y%m%d_%H%M%S)"
    
    # Calculate optimal settings based on RAM
    # shared_buffers = 25% of RAM (but max 8GB for small DBs)
    SHARED_BUFFERS=$((RAM_GB * 256))
    if [ $SHARED_BUFFERS -gt 2048 ]; then
        SHARED_BUFFERS=2048
    fi
    
    # effective_cache_size = 50% of RAM
    EFFECTIVE_CACHE=$((RAM_GB * 512))
    
    # maintenance_work_mem = 5% of RAM
    MAINTENANCE_MEM=$((RAM_GB * 51))
    if [ $MAINTENANCE_MEM -gt 512 ]; then
        MAINTENANCE_MEM=512
    fi
    
    # work_mem = RAM / (max_connections * 2)
    MAX_CONN=50
    WORK_MEM=$((RAM_GB * 1024 / (MAX_CONN * 2)))
    if [ $WORK_MEM -lt 4 ]; then
        WORK_MEM=4
    fi
    
    # Apply settings
    sed -i "s/^#*shared_buffers = .*/shared_buffers = ${SHARED_BUFFERS}MB/" "$PG_CONF"
    sed -i "s/^#*effective_cache_size = .*/effective_cache_size = ${EFFECTIVE_CACHE}MB/" "$PG_CONF"
    sed -i "s/^#*maintenance_work_mem = .*/maintenance_work_mem = ${MAINTENANCE_MEM}MB/" "$PG_CONF"
    sed -i "s/^#*work_mem = .*/work_mem = ${WORK_MEM}MB/" "$PG_CONF"
    sed -i "s/^#*max_connections = .*/max_connections = $MAX_CONN/" "$PG_CONF"
    
    log "✅ PostgreSQL optimized:"
    log "   shared_buffers: ${SHARED_BUFFERS}MB"
    log "   effective_cache_size: ${EFFECTIVE_CACHE}MB"
    log "   work_mem: ${WORK_MEM}MB"
    log "   max_connections: $MAX_CONN"
    
    systemctl restart postgresql
    log "✅ PostgreSQL restarted"
else
    warn "PostgreSQL config not found, skipping"
fi

# 4. Redis Optimization
log "Optimizing Redis..."
REDIS_CONF="/etc/redis/redis.conf"

if [ -f "$REDIS_CONF" ]; then
    # Backup config
    cp "$REDIS_CONF" "${REDIS_CONF}.backup.$(date +%Y%m%d_%H%M%S)"
    
    # Redis maxmemory = 10% of RAM (or 256MB minimum)
    REDIS_MEM=$((RAM_GB * 102))
    if [ $REDIS_MEM -lt 256 ]; then
        REDIS_MEM=256
    fi
    
    # Update or add maxmemory settings
    if grep -q "^maxmemory " "$REDIS_CONF"; then
        sed -i "s/^maxmemory .*/maxmemory ${REDIS_MEM}mb/" "$REDIS_CONF"
    else
        sed -i "s/^# maxmemory <bytes>/maxmemory ${REDIS_MEM}mb/" "$REDIS_CONF"
    fi
    
    if grep -q "^maxmemory-policy " "$REDIS_CONF"; then
        sed -i "s/^maxmemory-policy .*/maxmemory-policy allkeys-lru/" "$REDIS_CONF"
    else
        sed -i "s/^# maxmemory-policy noeviction/maxmemory-policy allkeys-lru/" "$REDIS_CONF"
    fi
    
    log "✅ Redis optimized: maxmemory ${REDIS_MEM}MB"
    systemctl restart redis-server
    log "✅ Redis restarted"
else
    warn "Redis config not found, skipping"
fi

# 5. Update bot .env settings (optional)
PROJECT_DIR="/opt/buster-vibe-bot"
if [ -f "$PROJECT_DIR/.env" ]; then
    log "Updating bot configuration..."
    
    # Calculate pool sizes based on RAM
    POOL_MIN=5
    POOL_MAX=$((RAM_GB * 10))
    if [ $POOL_MAX -lt 10 ]; then
        POOL_MAX=10
    fi
    if [ $POOL_MAX -gt 50 ]; then
        POOL_MAX=50
    fi
    
    # Update DB pool settings
    sed -i "s/^DB_POOL_MIN=.*/DB_POOL_MIN=$POOL_MIN/" "$PROJECT_DIR/.env"
    sed -i "s/^DB_POOL_MAX=.*/DB_POOL_MAX=$POOL_MAX/" "$PROJECT_DIR/.env"
    
    log "✅ Bot config updated: DB pool ${POOL_MIN}-${POOL_MAX}"
fi

# 6. Restart bot services
log "Restarting bot services..."
systemctl restart buster_bot buster_admin
sleep 3

# Status check
log "Checking services..."
systemctl is-active --quiet buster_bot && log "✅ Bot is running" || warn "⚠️ Bot may have issues"
systemctl is-active --quiet buster_admin && log "✅ Admin panel is running" || warn "⚠️ Admin panel may have issues"

echo ""
log "=== Optimization Complete! ==="
echo ""
log "Server optimized for ${RAM_GB}GB RAM"
log "All data preserved ✅"
echo ""
log "Configuration backups saved with timestamp"
log "Check status: sudo systemctl status buster_bot buster_admin"
