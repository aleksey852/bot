"""
Buster Vibe Bot - Centralized Configuration
Simplified: removed runtime validation, consolidated helpers
"""
import os
from dotenv import load_dotenv
from datetime import datetime
from typing import List, Optional
import pytz

load_dotenv()

# === Core ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS: List[int] = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
TIMEZONE = pytz.timezone(os.getenv("TIMEZONE", "Europe/Moscow"))

# === Database & Redis ===
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/buster_bot")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DB_POOL_MIN, DB_POOL_MAX = int(os.getenv("DB_POOL_MIN", "5")), int(os.getenv("DB_POOL_MAX", "20"))

# === External API ===
PROVERKA_CHEKA_TOKEN = os.getenv("PROVERKA_CHEKA_TOKEN", "")
PROVERKA_CHEKA_URL = "https://proverkacheka.com/api/v1/check/get"

# === Promo Settings ===
TARGET_KEYWORDS = [x.strip().lower() for x in os.getenv("TARGET_KEYWORDS", "чипсы,buster,vibe").split(",")]
PROMO_NAME = os.getenv("PROMO_NAME", "Buster Vibe")
PROMO_START_DATE = os.getenv("PROMO_START_DATE", "2025-01-15")
PROMO_END_DATE = os.getenv("PROMO_END_DATE", "2025-03-15")
PROMO_PRIZES = os.getenv("PROMO_PRIZES", "iPhone 16, PlayStation 5, сертификаты")

# === Support ===
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@example.com")
SUPPORT_TELEGRAM = os.getenv("SUPPORT_TELEGRAM", "@YourSupportBot")

# === Limits & Timing ===
RECEIPTS_RATE_LIMIT = int(os.getenv("RECEIPTS_RATE_LIMIT", "50"))
RECEIPTS_DAILY_LIMIT = int(os.getenv("RECEIPTS_DAILY_LIMIT", "200"))
SCHEDULER_INTERVAL = int(os.getenv("SCHEDULER_INTERVAL", "30"))
BROADCAST_BATCH_SIZE = int(os.getenv("BROADCAST_BATCH_SIZE", "25"))
MESSAGE_DELAY_SECONDS = float(os.getenv("MESSAGE_DELAY_SECONDS", "0.05"))
STATS_CACHE_TTL = int(os.getenv("STATS_CACHE_TTL", "60"))

# === Admin Panel ===
ADMIN_PANEL_USER = os.getenv("ADMIN_PANEL_USER", "admin")
ADMIN_PANEL_PASSWORD = os.getenv("ADMIN_PANEL_PASSWORD", "")
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "")

# === Monitoring ===
METRICS_ENABLED = os.getenv("METRICS_ENABLED", "true").lower() == "true"
METRICS_PORT = int(os.getenv("METRICS_PORT", "9090"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def get_now() -> datetime:
    return datetime.now(TIMEZONE)


def parse_scheduled_time(time_str: str) -> Optional[datetime]:
    if not time_str:
        return None
    try:
        # Handle both space (manual) and T (datetime-local) separators
        clean_str = time_str.replace("T", " ")
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M")
        # Return naive datetime as DB expects TIMESTAMP without timezone
        return dt
    except:
        return None


def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS


def is_promo_active() -> bool:
    try:
        start = datetime.strptime(PROMO_START_DATE, "%Y-%m-%d")
        end = datetime.strptime(PROMO_END_DATE, "%Y-%m-%d")
        return start <= datetime.now() <= end
    except:
        return True


def days_until_end() -> int:
    try:
        end = datetime.strptime(PROMO_END_DATE, "%Y-%m-%d")
        return max(0, (end - datetime.now()).days)
    except:
        return 0


def validate_config() -> List[str]:
    """Validate critical settings on startup"""
    errors = []
    if not BOT_TOKEN: errors.append("BOT_TOKEN is not set")
    if not PROVERKA_CHEKA_TOKEN: errors.append("PROVERKA_CHEKA_TOKEN is not set")
    if not ADMIN_IDS: errors.append("ADMIN_IDS is not set")
    if not ADMIN_PANEL_PASSWORD or len(ADMIN_PANEL_PASSWORD) < 12:
        errors.append("ADMIN_PANEL_PASSWORD must be at least 12 characters")
    if not ADMIN_SECRET_KEY or len(ADMIN_SECRET_KEY) < 32:
        errors.append("ADMIN_SECRET_KEY must be at least 32 characters")
    return errors
