"""
Config Manager - Dynamic settings from database
Allows changing promo texts, keywords, messages without restart
"""
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from database import get_connection
import config

logger = logging.getLogger(__name__)


class ConfigManager:
    _settings: Dict[str, str] = {}
    _messages: Dict[str, str] = {}
    _initialized = False

    async def load(self):
        """Load all settings and messages from DB"""
        try:
            async with get_connection() as db:
                rows = await db.fetch("SELECT key, value FROM settings")
                self._settings = {row['key']: row['value'] for row in rows}
                
                rows = await db.fetch("SELECT key, text FROM messages")
                self._messages = {row['key']: row['text'] for row in rows}
                
            self._initialized = True
            logger.info(f"Loaded {len(self._settings)} settings, {len(self._messages)} messages")
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get setting value"""
        return self._settings.get(key, default)

    def get_message(self, key: str, default: str = "") -> str:
        """Get message text"""
        return self._messages.get(key, default)

    async def set_setting(self, key: str, value: str, description: str = None):
        """Update setting in DB and cache"""
        async with get_connection() as db:
            await db.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
            """, key, str(value))
        
        self._settings[key] = str(value)
        await self._update_env_file(key, str(value))

    async def set_message(self, key: str, text: str, description: str = None):
        """Update message in DB and cache"""
        async with get_connection() as db:
            await db.execute("""
                INSERT INTO messages (key, text, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (key) DO UPDATE SET text = $2, updated_at = NOW()
            """, key, text)
        
        self._messages[key] = text

    async def _update_env_file(self, key: str, value: str):
        """Update .env file"""
        try:
            env_path = Path(".env")
            if not env_path.exists():
                return
            
            lines = env_path.read_text().splitlines()
            updated = False
            
            for i, line in enumerate(lines):
                if line.strip() and not line.startswith('#') and '=' in line:
                    if line.split('=', 1)[0].strip() == key:
                        lines[i] = f"{key}={value}"
                        updated = True
                        break
            
            if not updated:
                lines.append(f"{key}={value}")
            
            env_path.write_text('\n'.join(lines) + '\n')
            logger.info(f"Updated .env: {key}")
        except Exception as e:
            logger.error(f"Failed to update .env: {e}")

    async def get_all_settings(self):
        """Get all settings for admin panel"""
        async with get_connection() as db:
            return await db.fetch("SELECT * FROM settings ORDER BY key")

    async def get_all_messages(self):
        """Get all messages for admin panel"""
        async with get_connection() as db:
            return await db.fetch("SELECT * FROM messages ORDER BY key")


config_manager = ConfigManager()
