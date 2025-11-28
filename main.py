"""
Buster Vibe Bot - Simplified Main Entry Point
Features: PostgreSQL, Redis FSM, Rate limiting, Broadcasts, Raffles
+ PostgreSQL NOTIFY/LISTEN for instant campaign execution
"""
import asyncio
import logging
import signal
import sys
import random
import orjson
import asyncpg

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError

import config
from database import (
    init_db, close_db, get_pending_campaigns, mark_campaign_completed,
    get_user_ids_paginated, get_participants_with_ids, save_winners_atomic,
    get_unnotified_winners, mark_winner_notified, get_campaign,
    get_broadcast_progress, save_broadcast_progress, delete_broadcast_progress
)
from utils.api import init_api_client, close_api_client
from utils.rate_limiter import init_rate_limiter, close_rate_limiter
from handlers import user, registration, receipts, admin

logging.basicConfig(level=getattr(logging, config.LOG_LEVEL), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot: Bot = None
shutdown_event = asyncio.Event()
notification_queue = asyncio.Queue()


async def send_message_with_retry(user_id: int, content: dict, max_retries: int = 3) -> bool:
    """Send message with exponential backoff. Supports photo by file_id or path."""
    from aiogram.types import FSInputFile
    
    for attempt in range(max_retries):
        try:
            if "photo" in content:
                # photo is a Telegram file_id
                await bot.send_photo(user_id, content["photo"], caption=content.get("caption"))
            elif "photo_path" in content:
                # photo_path is a local file path
                photo_file = FSInputFile(content["photo_path"])
                await bot.send_photo(user_id, photo_file, caption=content.get("caption"))
            else:
                await bot.send_message(user_id, content.get("text", ""))
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            return False
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (2 ** attempt))
            else:
                logger.debug(f"Failed to send to {user_id}: {e}")
                return False
    return False


async def pg_listener():
    """Listen for campaign notifications from PostgreSQL"""
    logger.info("PostgreSQL Listener started")
    
    try:
        conn = await asyncpg.connect(config.DATABASE_URL)
        
        async def on_notification(connection, pid, channel, payload):
            logger.info(f"📢 Received notification from PostgreSQL: campaign_id={payload}")
            try:
                notification_queue.put_nowait(int(payload))
            except ValueError:
                logger.error(f"Invalid campaign_id in notification: {payload}")
        
        await conn.add_listener('new_campaign', on_notification)
        logger.info("Listening on PostgreSQL channel 'new_campaign'")
        
        # Keep connection alive
        while not shutdown_event.is_set():
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"PostgreSQL Listener error: {e}", exc_info=True)
    finally:
        try:
            await conn.remove_listener('new_campaign', on_notification)
            await conn.close()
        except:
            pass
        logger.info("PostgreSQL Listener stopped")


async def process_campaign(campaign: dict):
    """Process a single campaign"""
    content = campaign['content']
    if isinstance(content, str):
        content = orjson.loads(content)
    
    logger.info(f"⚙️  Processing campaign {campaign['id']} ({campaign['type']})")
    
    if campaign['type'] == "broadcast":
        await execute_broadcast(campaign['id'], content)
    elif campaign['type'] == "raffle":
        await execute_raffle(campaign['id'], content)
    elif campaign['type'] == "single_message":
        await execute_single_message(campaign['id'], content)


async def scheduler():
    """Background scheduler - processes notifications + periodic fallback check"""
    logger.info("Scheduler started")
    
    while not shutdown_event.is_set():
        try:
            # Priority 1: Process notifications from queue (instant)
            while not notification_queue.empty():
                try:
                    campaign_id = notification_queue.get_nowait()
                    logger.info(f"🚀 Processing notified campaign {campaign_id}")
                    
                    campaign = await get_campaign(campaign_id)
                    if campaign and not campaign.get('is_completed'):
                        # Check if scheduled for future
                        scheduled_for = campaign.get('scheduled_for')
                        if scheduled_for and scheduled_for > config.get_now().replace(tzinfo=None):
                            logger.info(f"⏳ Campaign {campaign_id} scheduled for {scheduled_for}, skipping immediate execution")
                            continue
                            
                        await process_campaign(campaign)
                    else:
                        logger.debug(f"Campaign {campaign_id} already completed or not found")
                        
                except Exception as e:
                    logger.error(f"Error processing notification: {e}", exc_info=True)
            
            # Priority 2: Periodic check for any missed campaigns (fallback)
            for campaign in await get_pending_campaigns():
                if shutdown_event.is_set():
                    break
                await process_campaign(campaign)
                
        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)
        
        # Sleep with interrupt capability
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=config.SCHEDULER_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass
    
    logger.info("Scheduler stopped")


async def execute_broadcast(campaign_id: int, content: dict):
    """Execute broadcast with pagination and progress tracking"""
    info = await get_campaign(campaign_id)
    if info and info.get('is_completed'):
        return
    
    progress = await get_broadcast_progress(campaign_id)
    last_user_id = progress['last_user_id'] if progress else 0
    sent = progress['sent_count'] if progress else 0
    failed = progress['failed_count'] if progress else 0
    
    while True:
        if shutdown_event.is_set():
            await save_broadcast_progress(campaign_id, last_user_id, sent, failed)
            return
        
        users = await get_user_ids_paginated(last_id=last_user_id, limit=1000)
        if not users:
            break
        
        for i, u in enumerate(users):
            if shutdown_event.is_set():
                await save_broadcast_progress(campaign_id, last_user_id, sent, failed)
                return
            
            if await send_message_with_retry(u['telegram_id'], content):
                sent += 1
            else:
                failed += 1
            
            last_user_id = u['id']
            
            if (i + 1) % config.BROADCAST_BATCH_SIZE == 0:
                await asyncio.sleep(config.MESSAGE_DELAY_SECONDS * config.BROADCAST_BATCH_SIZE)
            
            if (sent + failed) % 100 == 0:
                await save_broadcast_progress(campaign_id, last_user_id, sent, failed)
    
    await delete_broadcast_progress(campaign_id)
    await mark_campaign_completed(campaign_id, sent, failed)
    logger.info(f"✅ Broadcast {campaign_id} complete: {sent} sent, {failed} failed")


async def execute_single_message(campaign_id: int, content: dict):
    """Send message to a single user"""
    info = await get_campaign(campaign_id)
    if info and info.get('is_completed'):
        return
    
    target_user_id = content.get("target_user_id")
    if not target_user_id:
        await mark_campaign_completed(campaign_id, 0, 1)
        return
    
    # Extract message content (without target_user_id)
    msg_content = {k: v for k, v in content.items() if k != "target_user_id"}
    
    if await send_message_with_retry(target_user_id, msg_content):
        await mark_campaign_completed(campaign_id, 1, 0)
        logger.info(f"✅ Single message {campaign_id} sent to {target_user_id}")
    else:
        await mark_campaign_completed(campaign_id, 0, 1)
        logger.warning(f"❌ Single message {campaign_id} failed to {target_user_id}")


async def execute_raffle(campaign_id: int, content: dict):
    """Execute raffle with winner persistence"""
    info = await get_campaign(campaign_id)
    if info and info.get('is_completed'):
        return
    
    prize_name = content.get("prize", "Приз")
    count = content.get("count", 1)
    win_msg = content.get("win_msg", {})
    lose_msg = content.get("lose_msg", {})
    
    existing_winners = await get_unnotified_winners(campaign_id)
    all_participants = await get_participants_with_ids()
    
    if existing_winners:
        winners_data = existing_winners
        winner_ids = {w['user_id'] for w in winners_data}
    else:
        if not all_participants:
            await mark_campaign_completed(campaign_id, 0, 0)
            return
        
        winners = all_participants if len(all_participants) <= count else random.sample(all_participants, count)
        for w in winners:
            w['prize_name'] = prize_name
        
        saved = await save_winners_atomic(campaign_id, winners)
        if not saved:
            winners_data = await get_unnotified_winners(campaign_id)
        else:
            winners_data = await get_unnotified_winners(campaign_id)
        winner_ids = {w['user_id'] for w in winners_data}
    
    losers = [p for p in all_participants if p['user_id'] not in winner_ids]
    sent, failed = 0, 0
    
    for w in winners_data:
        if shutdown_event.is_set():
            return
        if await send_message_with_retry(w['telegram_id'], win_msg):
            await mark_winner_notified(w['id'])
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(config.MESSAGE_DELAY_SECONDS)
    
    for l in losers:
        if shutdown_event.is_set():
            return
        if await send_message_with_retry(l['telegram_id'], lose_msg):
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(config.MESSAGE_DELAY_SECONDS)
    
    await mark_campaign_completed(campaign_id, sent, failed)
    logger.info(f"🎉 Raffle {campaign_id} complete: {sent} notified, {failed} failed")


async def on_startup():
    errors = config.validate_config()
    if errors:
        for e in errors:
            logger.error(f"Config error: {e}")
        sys.exit(1)
    
    await init_db()
    await init_api_client()
    try:
        await init_rate_limiter()
    except Exception as e:
        logger.warning(f"Rate limiter unavailable: {e}")
    
    # Load dynamic config from database
    try:
        from utils.config_manager import config_manager
        await config_manager.load()
        logger.info("Dynamic configuration loaded")
    except Exception as e:
        logger.warning(f"Failed to load dynamic config: {e}")
    
    logger.info("Bot initialized")


async def on_shutdown():
    shutdown_event.set()
    await close_rate_limiter()
    await close_api_client()
    await close_db()
    logger.info("Shutdown complete")


async def main():
    global bot
    
    signal.signal(signal.SIGINT, lambda s, f: shutdown_event.set())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown_event.set())
    
    await on_startup()
    
    bot = Bot(token=config.BOT_TOKEN)
    try:
        storage = RedisStorage.from_url(config.REDIS_URL)
        logger.info("Using Redis FSM storage")
    except:
        storage = MemoryStorage()
        logger.warning("Using MemoryStorage - state lost on restart")
    
    dp = Dispatcher(storage=storage)
    dp.include_router(user.router)
    dp.include_router(registration.router)
    dp.include_router(receipts.router)
    dp.include_router(admin.router)
    
    # Start background tasks
    scheduler_task = asyncio.create_task(scheduler())
    listener_task = asyncio.create_task(pg_listener())
    
    logger.info(f"Starting {config.PROMO_NAME}... Admins: {config.ADMIN_IDS}")
    logger.info("🎯 PostgreSQL NOTIFY/LISTEN enabled for instant campaign execution")
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        shutdown_event.set()
        
        # Graceful shutdown
        try:
            await asyncio.wait_for(scheduler_task, timeout=30)
        except asyncio.TimeoutError:
            scheduler_task.cancel()
        
        try:
            await asyncio.wait_for(listener_task, timeout=30)
        except asyncio.TimeoutError:
            listener_task.cancel()
        
        await on_shutdown()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
