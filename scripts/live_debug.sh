#!/bin/bash
# Live monitoring for 502 debug
# Run this, then try to send a message in admin panel

echo "=== Live 502 Debug ==="
echo "Keep this running, then try to send a message in admin panel"
echo "Press Ctrl+C to stop"
echo ""

# Check if files are updated
echo "[Checking file versions]"
echo -n "database/db.py has advisory_lock: "
grep -q "pg_try_advisory_lock" /opt/buster-vibe-bot/database/db.py && echo "YES ✓" || echo "NO ✗ (old version!)"

echo -n "admin_panel/app.py has slow request logging: "
grep -q "log_slow_requests" /opt/buster-vibe-bot/admin_panel/app.py && echo "YES ✓" || echo "NO ✗ (old version!)"

echo ""
echo "[Starting live monitoring - nginx + uvicorn logs]"
echo "=============================================="
echo ""

# Monitor both nginx and uvicorn logs simultaneously
tail -f /var/log/nginx/error.log 2>/dev/null &
NGINX_PID=$!

journalctl -u buster_admin -f --no-pager 2>/dev/null &
JOURNAL_PID=$!

# Cleanup on exit
trap "kill $NGINX_PID $JOURNAL_PID 2>/dev/null; echo ''; echo 'Stopped.'" EXIT

wait
