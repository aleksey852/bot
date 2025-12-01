"""
Database layer with PostgreSQL connection pooling.
Simplified: consolidated table/index creation
+ PostgreSQL NOTIFY trigger for instant campaign notifications
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional, Any, List, Dict
import config

logger = logging.getLogger(__name__)
_pool = None

# Timeout for acquiring connection from pool (seconds)
POOL_ACQUIRE_TIMEOUT = 10.0


async def init_db():
    """Initialize database with tables and indexes"""
    global _pool
    import asyncpg
    
    logger.info("Connecting to PostgreSQL...")
    _pool = await asyncpg.create_pool(
        config.DATABASE_URL,
        min_size=config.DB_POOL_MIN,
        max_size=config.DB_POOL_MAX,
        max_inactive_connection_lifetime=300,
        command_timeout=60,  # Timeout for individual queries
    )
    logger.info(f"PostgreSQL pool initialized (min={config.DB_POOL_MIN}, max={config.DB_POOL_MAX})")
    
    await _create_schema()


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")


@asynccontextmanager
async def get_connection():
    """Get connection from pool with timeout"""
    if not _pool:
        raise RuntimeError("Database pool not initialized")
    
    try:
        # Add timeout to prevent indefinite waiting for connection
        conn = await asyncio.wait_for(
            _pool.acquire(),
            timeout=POOL_ACQUIRE_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error(f"Failed to acquire DB connection within {POOL_ACQUIRE_TIMEOUT}s - pool may be exhausted")
        raise RuntimeError(f"Database connection pool timeout after {POOL_ACQUIRE_TIMEOUT}s")
    
    try:
        yield DBWrapper(conn)
    finally:
        await _pool.release(conn)


class DBWrapper:
    """Consistent interface for asyncpg"""
    def __init__(self, conn):
        self.conn = conn
    
    async def execute(self, query: str, *args):
        return await self.conn.execute(query, *args)
    
    async def fetch(self, query: str, *args) -> List[Dict]:
        return [dict(r) for r in await self.conn.fetch(query, *args)]
    
    async def fetchrow(self, query: str, *args) -> Optional[Dict]:
        row = await self.conn.fetchrow(query, *args)
        return dict(row) if row else None
    
    async def fetchval(self, query: str, *args) -> Any:
        return await self.conn.fetchval(query, *args)


async def _create_schema():
    """Create all tables and indexes with lock to prevent race conditions"""
    async with get_connection() as db:
        # Use advisory lock to prevent race condition when multiple workers start
        lock_acquired = await db.fetchval("SELECT pg_try_advisory_lock(12345)")
        
        if not lock_acquired:
            # Another worker is initializing, wait a bit and return
            logger.info("Schema initialization in progress by another worker, skipping...")
            return
        
        try:
            # Tables
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    registered_at TIMESTAMP DEFAULT NOW(),
                    is_blocked BOOLEAN DEFAULT FALSE
                );
                
                CREATE TABLE IF NOT EXISTS receipts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    fiscal_drive_number TEXT,
                    fiscal_document_number TEXT,
                    fiscal_sign TEXT,
                    raw_qr TEXT,
                    status TEXT NOT NULL,
                    total_sum INTEGER DEFAULT 0,
                    product_name TEXT,
                    tickets INTEGER DEFAULT 1,
                    data JSONB,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(fiscal_drive_number, fiscal_document_number, fiscal_sign)
                );
                
                CREATE TABLE IF NOT EXISTS campaigns (
                    id SERIAL PRIMARY KEY,
                    type TEXT NOT NULL,
                    content JSONB NOT NULL,
                    scheduled_for TIMESTAMP,
                    is_completed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    completed_at TIMESTAMP,
                    sent_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0
                );
                
                CREATE TABLE IF NOT EXISTS winners (
                    id SERIAL PRIMARY KEY,
                    campaign_id INTEGER REFERENCES campaigns(id),
                    user_id INTEGER REFERENCES users(id),
                    telegram_id BIGINT NOT NULL,
                    prize_name TEXT,
                    notified BOOLEAN DEFAULT FALSE,
                    notified_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(campaign_id, user_id)
                );
                
                CREATE TABLE IF NOT EXISTS broadcast_progress (
                    id SERIAL PRIMARY KEY,
                    campaign_id INTEGER UNIQUE REFERENCES campaigns(id),
                    last_user_id INTEGER DEFAULT 0,
                    sent_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT NOW()
                );
                
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                );
                
                CREATE TABLE IF NOT EXISTS messages (
                    key TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """)
            
            # PostgreSQL NOTIFY function and trigger for instant notifications
            # Use DO block to create trigger only if it doesn't exist (avoids ACCESS EXCLUSIVE LOCK)
            await db.execute("""
                -- Function to send notification when new campaign is created
                CREATE OR REPLACE FUNCTION notify_new_campaign() 
                RETURNS TRIGGER AS $$
                BEGIN
                    -- Send notification with campaign ID
                    PERFORM pg_notify('new_campaign', NEW.id::text);
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """)
            
            # Create trigger separately with existence check
            trigger_exists = await db.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_trigger 
                    WHERE tgname = 'campaign_insert_trigger' 
                    AND tgrelid = 'campaigns'::regclass
                )
            """)
            
            if not trigger_exists:
                await db.execute("""
                    CREATE TRIGGER campaign_insert_trigger
                    AFTER INSERT ON campaigns
                    FOR EACH ROW
                    EXECUTE FUNCTION notify_new_campaign();
                """)
            
            logger.info("✅ PostgreSQL NOTIFY trigger installed on campaigns table")
            
            # Indexes - all in one batch, ignore errors for existing
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_id)",
                "CREATE INDEX IF NOT EXISTS idx_receipts_user ON receipts(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status)",
                "CREATE INDEX IF NOT EXISTS idx_receipts_fiscal ON receipts(fiscal_drive_number, fiscal_document_number, fiscal_sign)",
                "CREATE INDEX IF NOT EXISTS idx_receipts_user_created ON receipts(user_id, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_campaigns_pending ON campaigns(is_completed, scheduled_for)",
                "CREATE INDEX IF NOT EXISTS idx_winners_campaign ON winners(campaign_id)",
            ]
            for idx in indexes:
                try:
                    await db.execute(idx)
                except:
                    pass
            
            # Migration: Add tickets column if not exists (for existing databases)
            try:
                await db.execute("""
                    ALTER TABLE receipts ADD COLUMN IF NOT EXISTS tickets INTEGER DEFAULT 1
                """)
                logger.info("✅ Migration: tickets column ensured in receipts table")
            except Exception as e:
                logger.debug(f"Tickets column migration: {e}")
            
            logger.info("Database schema initialized")
        
        finally:
            # Always release the lock
            await db.execute("SELECT pg_advisory_unlock(12345)")
