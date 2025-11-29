"""External API client for receipt validation"""
import aiohttp
import asyncio
import logging
import config

logger = logging.getLogger(__name__)
_session = None


async def init_api_client():
    global _session
    _session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=100, limit_per_host=20),
        timeout=aiohttp.ClientTimeout(total=30, connect=10)
    )


async def close_api_client():
    global _session
    if _session:
        await _session.close()
        _session = None


async def check_receipt(qr_file=None, qr_raw: str = None, user_id: int = None) -> dict:
    """Validate receipt via proverkacheka.com API"""
    if not config.PROVERKA_CHEKA_TOKEN:
        return {"code": -1, "message": "API token not configured"}
    
    global _session
    if not _session:
        await init_api_client()
    
    try:
        data = aiohttp.FormData()
        data.add_field("token", config.PROVERKA_CHEKA_TOKEN)
        if user_id:
            data.add_field("userdata_telegram_id", str(user_id))
        
        if qr_raw:
            if len(qr_raw) > 1000:
                return {"code": 0, "message": "QR data too long"}
            data.add_field("qrraw", qr_raw)
        elif qr_file:
            data.add_field("qrfile", qr_file, filename="qr.jpg", content_type="image/jpeg")
        else:
            return {"code": 0, "message": "No QR data provided"}
        
        async with _session.post(config.PROVERKA_CHEKA_URL, data=data) as resp:
            if resp.status != 200:
                return {"code": -1, "message": f"HTTP {resp.status}"}
            return await resp.json()
            
    except asyncio.TimeoutError:
        return {"code": -1, "message": "Timeout"}
    except Exception as e:
        logger.error(f"API error: {e}")
        return {"code": -1, "message": str(e)}
