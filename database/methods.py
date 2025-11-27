"""
Database methods - Simplified with reduced duplication
"""
import json
import asyncio
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from database.db import get_connection
import config

logger = logging.getLogger(__name__)

# Stats cache
_stats_cache = {}
_stats_cache_time = 0.0
_stats_lock = asyncio.Lock()


def escape_like(text: Optional[str]) -> str:
    if not text: return ""
    return str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# === Users ===

async def add_user(telegram_id: int, username: str, full_name: str, phone: str) -> int:
    async with get_connection() as db:
        existing = await db.fetchval("SELECT id FROM users WHERE telegram_id = $1", telegram_id)
        if existing:
            return existing
        return await db.fetchval("""
            INSERT INTO users (telegram_id, username, full_name, phone)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (telegram_id) DO NOTHING RETURNING id
        """, telegram_id, username, full_name, phone)


async def get_user(telegram_id: int) -> Optional[Dict]:
    async with get_connection() as db:
        return await db.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)


async def get_user_by_id(user_id: int) -> Optional[Dict]:
    async with get_connection() as db:
        return await db.fetchrow("SELECT * FROM users WHERE id = $1", user_id)


async def get_user_by_username(username: str) -> Optional[Dict]:
    async with get_connection() as db:
        return await db.fetchrow("SELECT * FROM users WHERE username ILIKE $1", username.lstrip('@'))


async def get_user_by_phone(phone: str) -> Optional[Dict]:
    async with get_connection() as db:
        clean = ''.join(filter(str.isdigit, phone))
        return await db.fetchrow("SELECT * FROM users WHERE phone LIKE $1", f"%{escape_like(clean)}%")


async def get_user_with_stats(telegram_id: int) -> Optional[Dict]:
    async with get_connection() as db:
        user = await db.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
        if not user:
            return None
        stats = await db.fetchrow("""
            SELECT COUNT(*) as total_receipts,
                   COUNT(CASE WHEN status = 'valid' THEN 1 END) as valid_receipts
            FROM receipts WHERE user_id = $1
        """, user['id'])
        return {**user, **stats}


async def update_username(telegram_id: int, username: str):
    async with get_connection() as db:
        await db.execute("UPDATE users SET username = $1 WHERE telegram_id = $2", username, telegram_id)


async def get_total_users_count() -> int:
    async with get_connection() as db:
        return await db.fetchval("SELECT COUNT(*) FROM users")


async def get_all_user_ids() -> List[int]:
    async with get_connection() as db:
        rows = await db.fetch("SELECT telegram_id FROM users WHERE is_blocked = FALSE")
        return [r['telegram_id'] for r in rows]


async def get_user_ids_paginated(last_id: int = 0, limit: int = 1000) -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT id, telegram_id FROM users 
            WHERE is_blocked = FALSE AND id > $1
            ORDER BY id LIMIT $2
        """, last_id, limit)


async def get_users_paginated(page: int = 1, per_page: int = 50) -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT u.*, COUNT(r.id) as receipt_count
            FROM users u LEFT JOIN receipts r ON u.id = r.user_id AND r.status = 'valid'
            GROUP BY u.id ORDER BY u.registered_at DESC
            LIMIT $1 OFFSET $2
        """, per_page, (page - 1) * per_page)


# === Receipts ===

async def add_receipt(user_id: int, status: str, data: Dict, **kwargs) -> int:
    fields = ['user_id', 'status', 'data']
    values = [user_id, status, json.dumps(data)]
    placeholders = ['$1', '$2', '$3']
    
    for i, key in enumerate(['fiscal_drive_number', 'fiscal_document_number', 
                             'fiscal_sign', 'total_sum', 'product_name', 'raw_qr'], 4):
        if key in kwargs:
            fields.append(key)
            values.append(kwargs[key])
            placeholders.append(f'${i}')
    
    async with get_connection() as db:
        return await db.fetchval(f"""
            INSERT INTO receipts ({', '.join(fields)})
            VALUES ({', '.join(placeholders)}) RETURNING id
        """, *values)


async def is_receipt_exists(fn: str, fd: str, fp: str) -> bool:
    async with get_connection() as db:
        count = await db.fetchval("""
            SELECT COUNT(*) FROM receipts 
            WHERE fiscal_drive_number = $1 AND fiscal_document_number = $2 AND fiscal_sign = $3
        """, fn, fd, fp)
        return count > 0


async def get_user_receipts(user_id: int, limit: int = 10, offset: int = 0) -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT * FROM receipts WHERE user_id = $1
            ORDER BY created_at DESC LIMIT $2 OFFSET $3
        """, user_id, limit, offset)


async def get_user_receipts_count(user_id: int) -> int:
    async with get_connection() as db:
        return await db.fetchval(
            "SELECT COUNT(*) FROM receipts WHERE user_id = $1 AND status = 'valid'", user_id)


async def get_all_receipts_paginated(page: int = 1, per_page: int = 50) -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT r.*, u.full_name, u.username FROM receipts r
            JOIN users u ON r.user_id = u.id
            ORDER BY r.created_at DESC LIMIT $1 OFFSET $2
        """, per_page, (page - 1) * per_page)


async def get_total_receipts_count() -> int:
    async with get_connection() as db:
        return await db.fetchval("SELECT COUNT(*) FROM receipts")


# === Campaigns ===

async def add_campaign(type: str, content: Dict, scheduled_for: Optional[datetime] = None) -> int:
    async with get_connection() as db:
        return await db.fetchval("""
            INSERT INTO campaigns (type, content, scheduled_for)
            VALUES ($1, $2, $3) RETURNING id
        """, type, json.dumps(content), scheduled_for)


async def get_pending_campaigns() -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT * FROM campaigns 
            WHERE is_completed = FALSE AND (scheduled_for IS NULL OR scheduled_for <= NOW())
            ORDER BY created_at
        """)


async def mark_campaign_completed(campaign_id: int, sent: int = 0, failed: int = 0):
    async with get_connection() as db:
        await db.execute("""
            UPDATE campaigns 
            SET is_completed = TRUE, completed_at = NOW(), sent_count = $1, failed_count = $2
            WHERE id = $3
        """, sent, failed, campaign_id)


async def get_campaign(campaign_id: int) -> Optional[Dict]:
    async with get_connection() as db:
        c = await db.fetchrow("SELECT * FROM campaigns WHERE id = $1", campaign_id)
        if c and isinstance(c.get('content'), str):
            c = dict(c)
            c['content'] = json.loads(c['content'])
        return c


# === Winners & Raffle ===

async def get_participants_count() -> int:
    async with get_connection() as db:
        return await db.fetchval("SELECT COUNT(DISTINCT user_id) FROM receipts WHERE status = 'valid'")


async def get_participants_with_ids() -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT DISTINCT u.id as user_id, u.telegram_id, u.full_name, u.username
            FROM users u JOIN receipts r ON u.id = r.user_id WHERE r.status = 'valid'
        """)


async def save_winners_atomic(campaign_id: int, winners_data: List[Dict]) -> int:
    """Save winners with advisory lock to prevent race conditions"""
    async with get_connection() as db:
        lock = await db.fetchval("SELECT pg_try_advisory_lock($1)", campaign_id)
        if not lock:
            return 0
        try:
            existing = await db.fetchval("SELECT COUNT(*) FROM winners WHERE campaign_id = $1", campaign_id)
            if existing > 0:
                return 0
            
            count = 0
            async with db.conn.transaction():
                for w in winners_data:
                    await db.execute("""
                        INSERT INTO winners (campaign_id, user_id, telegram_id, prize_name)
                        VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING
                    """, campaign_id, w['user_id'], w['telegram_id'], w['prize_name'])
                    count += 1
            return count
        finally:
            await db.execute("SELECT pg_advisory_unlock($1)", campaign_id)


async def get_unnotified_winners(campaign_id: int) -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT w.*, c.content FROM winners w
            JOIN campaigns c ON w.campaign_id = c.id
            WHERE w.notified = FALSE AND w.campaign_id = $1
        """, campaign_id)


async def mark_winner_notified(winner_id: int):
    async with get_connection() as db:
        await db.execute("UPDATE winners SET notified = TRUE, notified_at = NOW() WHERE id = $1", winner_id)


async def get_campaign_winners(campaign_id: int) -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT w.*, u.full_name, u.username FROM winners w
            JOIN users u ON w.user_id = u.id WHERE w.campaign_id = $1
        """, campaign_id)


async def get_recent_raffles_with_winners(limit: int = 5) -> List[Dict]:
    async with get_connection() as db:
        campaigns = await db.fetch("""
            SELECT * FROM campaigns WHERE type = 'raffle' AND is_completed = TRUE
            ORDER BY completed_at DESC LIMIT $1
        """, limit)
        if not campaigns:
            return []
        
        campaign_ids = [c['id'] for c in campaigns]
        placeholders = ','.join([f'${i+1}' for i in range(len(campaign_ids))])
        all_winners = await db.fetch(f"""
            SELECT w.*, u.full_name, u.username, u.phone FROM winners w
            JOIN users u ON w.user_id = u.id WHERE w.campaign_id IN ({placeholders})
        """, *campaign_ids)
        
        winners_map = {}
        for w in all_winners:
            winners_map.setdefault(w['campaign_id'], []).append(dict(w))
        
        result = []
        for c in campaigns:
            c = dict(c)
            if isinstance(c.get('content'), str):
                c['content'] = json.loads(c['content'])
            # Extract prize_name from content
            c['prize_name'] = c.get('content', {}).get('prize_name', 'Розыгрыш')
            c['winners'] = winners_map.get(c['id'], [])
            result.append(c)
        return result


async def get_all_winners_for_export() -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT w.*, u.full_name, u.phone, u.username, c.created_at as raffle_date
            FROM winners w JOIN users u ON w.user_id = u.id
            JOIN campaigns c ON w.campaign_id = c.id ORDER BY w.created_at DESC
        """)


async def get_user_wins(user_id: int) -> List[Dict]:
    async with get_connection() as db:
        return await db.fetch("""
            SELECT w.*, c.completed_at FROM winners w
            JOIN campaigns c ON w.campaign_id = c.id
            WHERE w.user_id = $1 ORDER BY w.created_at DESC
        """, user_id)


# === Broadcast Progress ===

async def get_broadcast_progress(campaign_id: int) -> Optional[Dict]:
    async with get_connection() as db:
        return await db.fetchrow("SELECT * FROM broadcast_progress WHERE campaign_id = $1", campaign_id)


async def save_broadcast_progress(campaign_id: int, last_user_id: int, sent: int, failed: int):
    async with get_connection() as db:
        await db.execute("""
            INSERT INTO broadcast_progress (campaign_id, last_user_id, sent_count, failed_count)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (campaign_id) DO UPDATE 
            SET last_user_id = EXCLUDED.last_user_id, sent_count = EXCLUDED.sent_count,
                failed_count = EXCLUDED.failed_count, updated_at = NOW()
        """, campaign_id, last_user_id, sent, failed)


async def delete_broadcast_progress(campaign_id: int):
    async with get_connection() as db:
        await db.execute("DELETE FROM broadcast_progress WHERE campaign_id = $1", campaign_id)


# === Health & Stats ===

async def check_db_health() -> bool:
    try:
        async with get_connection() as db:
            return await db.fetchval("SELECT 1") == 1
    except:
        return False


async def get_stats() -> Dict:
    global _stats_cache, _stats_cache_time
    
    if time.time() - _stats_cache_time < config.STATS_CACHE_TTL and _stats_cache:
        return _stats_cache.copy()
    
    async with _stats_lock:
        if time.time() - _stats_cache_time < config.STATS_CACHE_TTL and _stats_cache:
            return _stats_cache.copy()
        
        async with get_connection() as db:
            now = datetime.utcnow()
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            week_ago = now - timedelta(days=7)
            
            stats = {
                "total_users": await db.fetchval("SELECT COUNT(*) FROM users"),
                "total_receipts": await db.fetchval("SELECT COUNT(*) FROM receipts"),
                "valid_receipts": await db.fetchval("SELECT COUNT(*) FROM receipts WHERE status = 'valid'"),
                "participants": await db.fetchval("SELECT COUNT(DISTINCT user_id) FROM receipts WHERE status = 'valid'"),
                "users_today": await db.fetchval("SELECT COUNT(*) FROM users WHERE registered_at >= $1", today),
                "users_week": await db.fetchval("SELECT COUNT(*) FROM users WHERE registered_at >= $1", week_ago),
                "receipts_today": await db.fetchval("SELECT COUNT(*) FROM receipts WHERE created_at >= $1", today),
                "receipts_week": await db.fetchval("SELECT COUNT(*) FROM receipts WHERE created_at >= $1", week_ago),
                "total_winners": await db.fetchval("SELECT COUNT(*) FROM winners"),
                "total_campaigns": await db.fetchval("SELECT COUNT(*) FROM campaigns"),
                "completed_campaigns": await db.fetchval("SELECT COUNT(*) FROM campaigns WHERE is_completed = TRUE"),
            }
            stats["conversion"] = round((stats["participants"] / stats["total_users"] * 100) if stats["total_users"] else 0, 2)
            
            _stats_cache = stats
            _stats_cache_time = time.time()
            return stats.copy()


async def get_stats_by_days(days: int = 14) -> List[Dict]:
    """Get statistics grouped by day for charts"""
    async with get_connection() as db:
        return await db.fetch("""
            WITH date_series AS (
                SELECT generate_series(
                    CURRENT_DATE - INTERVAL '%s days',
                    CURRENT_DATE,
                    '1 day'::interval
                )::date AS day
            )
            SELECT 
                ds.day,
                COALESCE(u.user_count, 0) as users,
                COALESCE(r.receipt_count, 0) as receipts
            FROM date_series ds
            LEFT JOIN (
                SELECT DATE(registered_at) as day, COUNT(*) as user_count
                FROM users GROUP BY DATE(registered_at)
            ) u ON ds.day = u.day
            LEFT JOIN (
                SELECT DATE(created_at) as day, COUNT(*) as receipt_count
                FROM receipts WHERE status = 'valid' GROUP BY DATE(created_at)
            ) r ON ds.day = r.day
            ORDER BY ds.day
        """ % days)


async def search_users(query: str, limit: int = 20) -> List[Dict]:
    """Search users by name, username, phone or telegram_id"""
    async with get_connection() as db:
        clean_query = query.strip()
        
        # Try exact telegram_id match first
        if clean_query.isdigit():
            user = await db.fetchrow("""
                SELECT u.*, COUNT(r.id) as receipt_count
                FROM users u LEFT JOIN receipts r ON u.id = r.user_id AND r.status = 'valid'
                WHERE u.telegram_id = $1 OR u.id = $1::int
                GROUP BY u.id
            """, int(clean_query))
            if user:
                return [user]
        
        # Search by text
        search_pattern = f"%{escape_like(clean_query)}%"
        return await db.fetch("""
            SELECT u.*, COUNT(r.id) as receipt_count
            FROM users u LEFT JOIN receipts r ON u.id = r.user_id AND r.status = 'valid'
            WHERE u.full_name ILIKE $1 
               OR u.username ILIKE $1 
               OR u.phone LIKE $2
            GROUP BY u.id
            ORDER BY u.registered_at DESC
            LIMIT $3
        """, search_pattern, f"%{escape_like(''.join(filter(str.isdigit, clean_query)))}%", limit)


async def get_user_detail(user_id: int) -> Optional[Dict]:
    """Get detailed user info with all stats"""
    async with get_connection() as db:
        user = await db.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not user:
            return None
        
        user = dict(user)
        
        # Get receipt stats
        receipt_stats = await db.fetchrow("""
            SELECT 
                COUNT(*) as total_receipts,
                COUNT(CASE WHEN status = 'valid' THEN 1 END) as valid_receipts,
                COALESCE(SUM(CASE WHEN status = 'valid' THEN total_sum END), 0) as total_sum
            FROM receipts WHERE user_id = $1
        """, user_id)
        user.update(dict(receipt_stats))
        
        # Get wins
        wins = await db.fetch("""
            SELECT w.*, c.created_at as raffle_date
            FROM winners w JOIN campaigns c ON w.campaign_id = c.id
            WHERE w.user_id = $1 ORDER BY w.created_at DESC
        """, user_id)
        user['wins'] = wins
        
        return user


async def get_user_receipts_detailed(user_id: int, limit: int = 50) -> List[Dict]:
    """Get user receipts with full details"""
    async with get_connection() as db:
        return await db.fetch("""
            SELECT * FROM receipts WHERE user_id = $1
            ORDER BY created_at DESC LIMIT $2
        """, user_id, limit)


async def get_recent_campaigns(limit: int = 20) -> List[Dict]:
    """Get recent campaigns for admin panel"""
    async with get_connection() as db:
        campaigns = await db.fetch("""
            SELECT * FROM campaigns ORDER BY created_at DESC LIMIT $1
        """, limit)
        result = []
        for c in campaigns:
            c = dict(c)
            if isinstance(c.get('content'), str):
                c['content'] = json.loads(c['content'])
            result.append(c)
        return result


async def block_user(user_id: int, blocked: bool = True):
    """Block or unblock user"""
    async with get_connection() as db:
        await db.execute("UPDATE users SET is_blocked = $1 WHERE id = $2", blocked, user_id)
