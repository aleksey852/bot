"""Rate limiter using Redis"""
import logging
from datetime import datetime
from typing import Tuple
import config

logger = logging.getLogger(__name__)
_redis = None


async def init_rate_limiter():
    global _redis
    import redis.asyncio as redis
    pool = redis.ConnectionPool.from_url(config.REDIS_URL, decode_responses=True, max_connections=20)
    _redis = redis.Redis(connection_pool=pool)
    await _redis.ping()
    logger.info("Rate limiter initialized")


async def close_rate_limiter():
    global _redis
    if _redis:
        await _redis.close()
        _redis = None


async def check_rate_limit(user_id: int) -> Tuple[bool, str]:
    """Check if user can upload more receipts"""
    if not _redis:
        return True, ""
    
    try:
        now = datetime.now()
        hour_key = f"receipts:h:{user_id}:{now.strftime('%Y%m%d%H')}"
        day_key = f"receipts:d:{user_id}:{now.strftime('%Y%m%d')}"
        
        hour_count = int(await _redis.get(hour_key) or 0)
        day_count = int(await _redis.get(day_key) or 0)
        
        if hour_count >= config.RECEIPTS_RATE_LIMIT:
            return False, f"Лимит: {config.RECEIPTS_RATE_LIMIT} чеков/час. Подождите немного."
        if day_count >= config.RECEIPTS_DAILY_LIMIT:
            return False, f"Лимит: {config.RECEIPTS_DAILY_LIMIT} чеков/день. Возвращайтесь завтра!"
        return True, ""
    except Exception as e:
        logger.error(f"Rate limit error: {e}")
        return True, ""


async def increment_rate_limit(user_id: int):
    if not _redis:
        return
    try:
        now = datetime.now()
        hour_key = f"receipts:h:{user_id}:{now.strftime('%Y%m%d%H')}"
        day_key = f"receipts:d:{user_id}:{now.strftime('%Y%m%d')}"
        
        pipe = _redis.pipeline()
        pipe.incr(hour_key)
        pipe.expire(hour_key, 3600)
        pipe.incr(day_key)
        pipe.expire(day_key, 86400)
        await pipe.execute()
    except Exception as e:
        logger.error(f"Rate increment error: {e}")
