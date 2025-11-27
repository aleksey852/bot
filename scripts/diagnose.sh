#!/bin/bash
# Diagnostic script to find cause of 502 errors
# Usage: sudo bash scripts/diagnose.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PROJECT_DIR="/opt/buster-vibe-bot"

echo -e "${CYAN}=== Buster Bot 502 Diagnostics ===${NC}"
echo ""

# 1. Check services status
echo -e "${CYAN}[1] Service Status${NC}"
echo "Bot service:"
systemctl is-active buster_bot && echo -e "${GREEN}✓ Running${NC}" || echo -e "${RED}✗ Not running${NC}"
echo "Admin service:"
systemctl is-active buster_admin && echo -e "${GREEN}✓ Running${NC}" || echo -e "${RED}✗ Not running${NC}"
echo "PostgreSQL:"
systemctl is-active postgresql && echo -e "${GREEN}✓ Running${NC}" || echo -e "${RED}✗ Not running${NC}"
echo "Redis:"
systemctl is-active redis-server && echo -e "${GREEN}✓ Running${NC}" || echo -e "${RED}✗ Not running${NC}"
echo "Nginx:"
systemctl is-active nginx && echo -e "${GREEN}✓ Running${NC}" || echo -e "${RED}✗ Not running${NC}"
echo ""

# 2. Check uvicorn workers
echo -e "${CYAN}[2] Uvicorn Workers${NC}"
UVICORN_CMD=$(systemctl show buster_admin --property=ExecStart | grep -o 'uvicorn.*')
if echo "$UVICORN_CMD" | grep -q "workers"; then
    echo -e "${GREEN}✓ Multiple workers configured${NC}"
else
    echo -e "${RED}✗ Single worker mode - THIS CAUSES 502!${NC}"
    echo "  Fix: Add --workers 2 to uvicorn command in systemd"
fi
echo "Current command: $UVICORN_CMD"
echo ""

# 3. Check nginx timeouts
echo -e "${CYAN}[3] Nginx Timeouts${NC}"
if [[ -f /etc/nginx/sites-available/buster ]]; then
    if grep -q "proxy_read_timeout" /etc/nginx/sites-available/buster; then
        TIMEOUT=$(grep "proxy_read_timeout" /etc/nginx/sites-available/buster | head -1)
        echo -e "${GREEN}✓ proxy_read_timeout configured${NC}: $TIMEOUT"
    else
        echo -e "${RED}✗ No proxy_read_timeout - default is 60s, may cause 502${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Nginx config not found${NC}"
fi
echo ""

# 4. PostgreSQL connections
echo -e "${CYAN}[4] PostgreSQL Connections${NC}"
PG_MAX=$(sudo -u postgres psql -t -c "SHOW max_connections;" 2>/dev/null | tr -d ' ')
PG_USED=$(sudo -u postgres psql -t -c "SELECT count(*) FROM pg_stat_activity;" 2>/dev/null | tr -d ' ')
echo "Max connections: $PG_MAX"
echo "Used connections: $PG_USED"
if [[ "$PG_USED" -gt "$((PG_MAX * 80 / 100))" ]]; then
    echo -e "${RED}✗ Connection pool nearly exhausted!${NC}"
else
    echo -e "${GREEN}✓ Connections OK${NC}"
fi

# Show connection details
echo ""
echo "Connections by application:"
sudo -u postgres psql -c "SELECT application_name, state, count(*) FROM pg_stat_activity GROUP BY application_name, state ORDER BY count DESC;" 2>/dev/null || echo "  (Could not query)"
echo ""

# 5. Check .env pool settings
echo -e "${CYAN}[5] Database Pool Settings${NC}"
if [[ -f "$PROJECT_DIR/.env" ]]; then
    DB_MIN=$(grep "DB_POOL_MIN" "$PROJECT_DIR/.env" | cut -d= -f2)
    DB_MAX=$(grep "DB_POOL_MAX" "$PROJECT_DIR/.env" | cut -d= -f2)
    echo "DB_POOL_MIN=$DB_MIN"
    echo "DB_POOL_MAX=$DB_MAX"
    
    # Bot + Admin = 2 pools
    TOTAL_MAX=$((DB_MAX * 2))
    if [[ "$TOTAL_MAX" -gt 30 ]]; then
        echo -e "${YELLOW}⚠ Total pool size ($TOTAL_MAX) is high for 2 services${NC}"
        echo "  Recommendation: DB_POOL_MIN=2, DB_POOL_MAX=10"
    fi
else
    echo -e "${RED}✗ .env not found${NC}"
fi
echo ""

# 6. Recent errors in logs
echo -e "${CYAN}[6] Recent Admin Panel Errors (last 20 lines)${NC}"
journalctl -u buster_admin --no-pager -n 20 --since "5 minutes ago" 2>/dev/null | grep -iE "(error|exception|timeout|failed|502)" || echo "  No recent errors found"
echo ""

# 7. Nginx error log
echo -e "${CYAN}[7] Recent Nginx Errors${NC}"
tail -20 /var/log/nginx/error.log 2>/dev/null | grep -iE "(502|timeout|upstream)" || echo "  No recent 502/timeout errors"
echo ""

# 8. Test direct connection to uvicorn
echo -e "${CYAN}[8] Direct Uvicorn Test${NC}"
echo "Testing http://127.0.0.1:8000/login ..."
RESPONSE=$(curl -s -o /dev/null -w "%{http_code} %{time_total}s" --max-time 30 http://127.0.0.1:8000/login 2>/dev/null)
HTTP_CODE=$(echo $RESPONSE | cut -d' ' -f1)
TIME=$(echo $RESPONSE | cut -d' ' -f2)

if [[ "$HTTP_CODE" == "200" ]]; then
    echo -e "${GREEN}✓ Direct connection OK (HTTP $HTTP_CODE, ${TIME})${NC}"
elif [[ "$HTTP_CODE" == "000" ]]; then
    echo -e "${RED}✗ Connection failed/timeout - uvicorn not responding${NC}"
else
    echo -e "${YELLOW}⚠ HTTP $HTTP_CODE (${TIME})${NC}"
fi
echo ""

# 9. Memory and CPU
echo -e "${CYAN}[9] System Resources${NC}"
echo "Memory:"
free -h | head -2
echo ""
echo "Top processes:"
ps aux --sort=-%mem | head -6
echo ""

# 10. Summary
echo -e "${CYAN}=== Summary ===${NC}"
echo ""
ISSUES=0

# Check workers
if ! echo "$UVICORN_CMD" | grep -q "workers"; then
    echo -e "${RED}[CRITICAL] Single uvicorn worker - add --workers 2${NC}"
    ISSUES=$((ISSUES + 1))
fi

# Check timeouts
if [[ -f /etc/nginx/sites-available/buster ]] && ! grep -q "proxy_read_timeout" /etc/nginx/sites-available/buster; then
    echo -e "${RED}[CRITICAL] Missing nginx proxy_read_timeout${NC}"
    ISSUES=$((ISSUES + 1))
fi

# Check pool
if [[ -n "$DB_MAX" && "$DB_MAX" -gt 15 ]]; then
    echo -e "${YELLOW}[WARNING] DB pool too large ($DB_MAX), reduce to 10${NC}"
    ISSUES=$((ISSUES + 1))
fi

# Check connections
if [[ -n "$PG_USED" && -n "$PG_MAX" && "$PG_USED" -gt "$((PG_MAX * 70 / 100))" ]]; then
    echo -e "${YELLOW}[WARNING] PostgreSQL connections high ($PG_USED/$PG_MAX)${NC}"
    ISSUES=$((ISSUES + 1))
fi

if [[ $ISSUES -eq 0 ]]; then
    echo -e "${GREEN}No obvious issues found. Check application logs for more details.${NC}"
else
    echo ""
    echo "Run: sudo bash scripts/fix_502.sh to apply fixes"
fi
