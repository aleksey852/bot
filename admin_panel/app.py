"""Admin Panel - FastAPI app with full management capabilities"""
from fastapi import FastAPI, Request, Depends, HTTPException, status, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
import sys
import os
import json
import uuid
import time
import aiofiles
import asyncio
import logging

# Ensure project root is in path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Paths relative to this file
ADMIN_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = ADMIN_DIR / "templates"
STATIC_DIR = ADMIN_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = ADMIN_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Buster Admin")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
DB_OPERATION_TIMEOUT = 10.0  # 10 seconds timeout for DB operations (increased for safety)
SLOW_REQUEST_THRESHOLD = 3.0  # Log requests slower than 3 seconds


@app.middleware("http")
async def log_slow_requests(request: Request, call_next):
    """Middleware to log slow requests for debugging"""
    start_time = time.time()
    
    try:
        response = await call_next(request)
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"❌ Request failed: {request.method} {request.url.path} - {duration:.2f}s - {e}")
        raise
    
    duration = time.time() - start_time
    if duration > SLOW_REQUEST_THRESHOLD:
        logger.warning(f"🐢 Slow request: {request.method} {request.url.path} - {duration:.2f}s")
    
    return response

# Editable promo settings
PROMO_FIELDS = [
    ("PROMO_NAME", "Название акции"),
    ("PROMO_START_DATE", "Дата начала (YYYY-MM-DD)"),
    ("PROMO_END_DATE", "Дата окончания (YYYY-MM-DD)"),
    ("PROMO_PRIZES", "Призы (через запятую)"),
    ("TARGET_KEYWORDS", "Ключевые слова товаров (через запятую)"),
]

SUPPORT_FIELDS = [
    ("SUPPORT_EMAIL", "Email поддержки"),
    ("SUPPORT_TELEGRAM", "Telegram поддержки (@username)"),
]


def create_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "exp": expire}, config.ADMIN_SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str:
    try:
        payload = jwt.decode(token, config.ADMIN_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    username = verify_token(token)
    if not username:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return username


# === Auth ===

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    if form.get("username") == config.ADMIN_PANEL_USER and form.get("password") == config.ADMIN_PANEL_PASSWORD:
        token = create_token(form.get("username"))
        response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie("access_token", token, httponly=True, max_age=TOKEN_EXPIRE_HOURS * 3600)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response


# === Dashboard ===

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: str = Depends(get_current_user)):
    from database import get_stats, get_participants_count, get_stats_by_days, get_recent_campaigns
    stats = await get_stats()
    participants = await get_participants_count()
    daily_stats = await get_stats_by_days(14)
    # Convert date objects to strings for JSON serialization
    for stat in daily_stats:
        if 'day' in stat and isinstance(stat['day'], (datetime, date)):
            stat['day'] = stat['day'].isoformat()
            
    recent_campaigns = await get_recent_campaigns(5)
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "stats": stats, "participants": participants,
        "daily_stats": daily_stats, "recent_campaigns": recent_campaigns,
        "title": "Dashboard"
    })


# === Statistics API (for charts) ===

@app.get("/api/stats/daily")
async def api_daily_stats(days: int = 14, user: str = Depends(get_current_user)):
    from database import get_stats_by_days
    data = await get_stats_by_days(days)
    return JSONResponse({
        "labels": [str(d['day']) for d in data],
        "users": [d['users'] for d in data],
        "receipts": [d['receipts'] for d in data]
    })


# === Settings (Promo, Keywords, etc.) ===

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: str = Depends(get_current_user), updated: str = None):
    from utils.config_manager import config_manager
    
    if not config_manager._initialized:
        await config_manager.load()
    
    # Read current values from .env
    env_vars = {}
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            for key, _ in PROMO_FIELDS:
                if line.startswith(f"{key}="):
                    env_vars[key] = line.split("=", 1)[1]
    
    # Fallback to config.py
    for key, _ in PROMO_FIELDS:
        if key not in env_vars:
            env_vars[key] = getattr(config, key, "")
    
    promo_fields = [(key, label, env_vars.get(key, "")) for key, label in PROMO_FIELDS]
    db_settings = await config_manager.get_all_settings()
    
    return templates.TemplateResponse("settings/index.html", {
        "request": request, "user": user, "title": "Настройки",
        "promo_fields": promo_fields, "db_settings": db_settings,
        "updated": updated
    })


@app.post("/settings/update")
async def update_setting(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    user: str = Depends(get_current_user)
):
    from utils.config_manager import config_manager
    
    editable_keys = [k for k, _ in PROMO_FIELDS] + [k for k, _ in SUPPORT_FIELDS]
    
    if key in editable_keys:
        # Update .env file
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            lines = env_path.read_text().splitlines()
            updated = False
            for i, line in enumerate(lines):
                if line.startswith(f"{key}="):
                    lines[i] = f"{key}={value}"
                    updated = True
                    break
            if not updated:
                lines.append(f"{key}={value}")
            env_path.write_text('\n'.join(lines) + "\n")
    else:
        # Update in database
        await config_manager.set_setting(key, value)
    
    return RedirectResponse(url="/settings?updated=1", status_code=status.HTTP_303_SEE_OTHER)


# === Support Settings ===

@app.get("/settings/support", response_class=HTMLResponse)
async def support_settings_page(request: Request, user: str = Depends(get_current_user), updated: str = None):
    # Read current values from .env
    env_vars = {}
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            for key, _ in SUPPORT_FIELDS:
                if line.startswith(f"{key}="):
                    env_vars[key] = line.split("=", 1)[1]
    
    # Fallback to config.py
    for key, _ in SUPPORT_FIELDS:
        if key not in env_vars:
            env_vars[key] = getattr(config, key, "")
    
    support_fields = [(key, label, env_vars.get(key, "")) for key, label in SUPPORT_FIELDS]
    
    return templates.TemplateResponse("settings/support.html", {
        "request": request, "user": user, "title": "Настройки поддержки",
        "support_fields": support_fields, "updated": updated
    })


@app.post("/settings/support/update")
async def update_support_setting(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    user: str = Depends(get_current_user)
):
    editable_keys = [k for k, _ in SUPPORT_FIELDS]
    
    if key not in editable_keys:
        raise HTTPException(status_code=400, detail="Invalid setting key")
    
    # Update .env file
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")
        env_path.write_text('\n'.join(lines) + "\n")
    
    return RedirectResponse(url="/settings/support?updated=1", status_code=status.HTTP_303_SEE_OTHER)


# === Messages (Bot UX texts) ===

@app.get("/settings/messages", response_class=HTMLResponse)
async def messages_page(request: Request, user: str = Depends(get_current_user), updated: str = None):
    from utils.config_manager import config_manager
    
    if not config_manager._initialized:
        await config_manager.load()
    
    messages = await config_manager.get_all_messages()
    
    # Default messages if DB is empty
    default_messages = [
        ("welcome_back", "С возвращением, {name}! 👋\n\nВаших чеков: {count}"),
        ("welcome_new", "🎉 Добро пожаловать в {promo_name}!\n\nПризы: {prizes}"),
        ("receipt_valid", "✅ Чек принят!\n\nВсего чеков: {count} 🎯"),
        ("receipt_first", "🎉 Поздравляем с первым чеком!"),
        ("receipt_duplicate", "ℹ️ Этот чек уже загружен"),
        ("receipt_no_product", "😔 В чеке нет акционных товаров"),
        ("no_receipts", "📋 У вас пока нет чеков"),
    ]
    
    return templates.TemplateResponse("settings/messages.html", {
        "request": request, "user": user, "title": "Тексты сообщений",
        "messages": messages, "default_messages": default_messages,
        "updated": updated
    })


@app.post("/settings/messages/update")
async def update_message(
    request: Request,
    key: str = Form(...),
    text: str = Form(...),
    user: str = Depends(get_current_user)
):
    from utils.config_manager import config_manager
    await config_manager.set_message(key, text)
    return RedirectResponse(url="/settings/messages?updated=1", status_code=status.HTTP_303_SEE_OTHER)


# === Users list ===

@app.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, user: str = Depends(get_current_user), page: int = 1, q: str = None):
    from database import get_users_paginated, get_total_users_count, search_users
    
    if q:
        users = await search_users(q)
        total = len(users)
        total_pages = 1
    else:
        users = await get_users_paginated(page=page, per_page=50)
        total = await get_total_users_count()
        total_pages = (total + 49) // 50
    
    return templates.TemplateResponse("users/list.html", {
        "request": request, "user": user, "users": users,
        "page": page, "total_pages": total_pages, "total": total,
        "search_query": q or "",
        "title": "Пользователи"
    })


# === User Detail & Actions ===

@app.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: int, user: str = Depends(get_current_user), msg: str = None):
    from database import get_user_detail, get_user_receipts_detailed
    
    user_data = await get_user_detail(user_id)
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    
    receipts = await get_user_receipts_detailed(user_id, limit=50)
    
    return templates.TemplateResponse("users/detail.html", {
        "request": request, "user": user, "user_data": user_data,
        "receipts": receipts, "title": f"Пользователь #{user_id}",
        "message": msg
    })


@app.post("/users/{user_id}/message")
async def send_user_message(
    request: Request,
    user_id: int,
    text: str = Form(None),
    photo: UploadFile = File(None),
    user: str = Depends(get_current_user)
):
    """Send message to specific user"""
    from database import get_user_detail, add_campaign
    
    logger.info(f"📤 Sending message to user {user_id}")
    start_time = time.time()
    
    try:
        user_data = await asyncio.wait_for(
            get_user_detail(user_id),
            timeout=DB_OPERATION_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error(f"❌ Timeout getting user {user_id} details")
        raise HTTPException(status_code=504, detail="Database timeout while getting user")
    
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    
    content = {}
    
    if photo and photo.filename:
        # Save photo
        ext = Path(photo.filename).suffix or ".jpg"
        filename = f"{uuid.uuid4()}{ext}"
        filepath = UPLOADS_DIR / filename
        
        async with aiofiles.open(filepath, 'wb') as f:
            await f.write(await photo.read())
        
        content["photo_path"] = str(filepath)
        content["caption"] = text
    elif text:
        content["text"] = text
    else:
        return RedirectResponse(
            url=f"/users/{user_id}?msg=error_empty",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    # Create single-user campaign with timeout
    content["target_user_id"] = user_data['telegram_id']
    
    try:
        campaign_id = await asyncio.wait_for(
            add_campaign("single_message", content),
            timeout=DB_OPERATION_TIMEOUT
        )
        duration = time.time() - start_time
        logger.info(f"✅ Message campaign {campaign_id} created for user {user_id} in {duration:.2f}s")
    except asyncio.TimeoutError:
        logger.error(f"❌ Timeout creating message campaign for user {user_id}")
        raise HTTPException(
            status_code=504,
            detail="Database operation timed out. The message may still be queued."
        )
    except Exception as e:
        logger.error(f"❌ Failed to create message campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create campaign: {str(e)}")
    
    return RedirectResponse(
        url=f"/users/{user_id}?msg=sent",
        status_code=status.HTTP_303_SEE_OTHER
    )


@app.post("/users/{user_id}/add-receipt")
async def add_user_receipt(
    request: Request,
    user_id: int,
    user: str = Depends(get_current_user)
):
    """Add manual receipt to user"""
    from database import get_user_detail, add_receipt
    
    user_data = await get_user_detail(user_id)
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    
    ts = int(time.time())
    uid = str(uuid.uuid4())[:8]
    
    try:
        await asyncio.wait_for(
            add_receipt(
                user_id=user_id,
                status="valid",
                data={"manual": True, "admin": user, "source": "web_panel"},
                fiscal_drive_number="MANUAL",
                fiscal_document_number=f"MANUAL_{ts}_{uid}",
                fiscal_sign=f"MANUAL_{user_id}_{ts}",
                total_sum=0,
                raw_qr="manual_web",
                product_name="Ручное добавление (веб)"
            ),
            timeout=DB_OPERATION_TIMEOUT
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Database operation timed out")
    
    return RedirectResponse(
        url=f"/users/{user_id}?msg=receipt_added",
        status_code=status.HTTP_303_SEE_OTHER
    )


@app.post("/users/{user_id}/block")
async def toggle_user_block(
    request: Request,
    user_id: int,
    user: str = Depends(get_current_user)
):
    """Block/unblock user"""
    from database import get_user_detail, block_user
    
    user_data = await get_user_detail(user_id)
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    
    new_status = not user_data.get('is_blocked', False)
    
    try:
        await asyncio.wait_for(
            block_user(user_id, new_status),
            timeout=DB_OPERATION_TIMEOUT
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Database operation timed out")
    
    return RedirectResponse(
        url=f"/users/{user_id}?msg={'blocked' if new_status else 'unblocked'}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# === Receipts list ===

@app.get("/receipts", response_class=HTMLResponse)
async def receipts_list(request: Request, user: str = Depends(get_current_user), page: int = 1):
    from database import get_all_receipts_paginated, get_total_receipts_count
    receipts = await get_all_receipts_paginated(page=page, per_page=50)
    total = await get_total_receipts_count()
    total_pages = (total + 49) // 50
    return templates.TemplateResponse("receipts/list.html", {
        "request": request, "user": user, "receipts": receipts,
        "page": page, "total_pages": total_pages, "total": total,
        "title": "Чеки"
    })


# === Winners ===

@app.get("/winners", response_class=HTMLResponse)
async def winners_list(request: Request, user: str = Depends(get_current_user)):
    from database import get_recent_raffles_with_winners
    raffles = await get_recent_raffles_with_winners(limit=10)
    return templates.TemplateResponse("winners/list.html", {
        "request": request, "user": user, "raffles": raffles,
        "title": "Победители"
    })


# === Broadcast ===

@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, user: str = Depends(get_current_user), created: str = None):
    from database import get_total_users_count, get_recent_campaigns
    
    total_users = await get_total_users_count()
    recent = await get_recent_campaigns(10)
    broadcasts = [c for c in recent if c['type'] == 'broadcast']
    
    return templates.TemplateResponse("broadcast/index.html", {
        "request": request, "user": user, "title": "Рассылка",
        "total_users": total_users, "broadcasts": broadcasts,
        "created": created
    })


@app.post("/broadcast/create")
async def create_broadcast(
    request: Request,
    text: str = Form(None),
    photo: UploadFile = File(None),
    scheduled_for: str = Form(None),
    user: str = Depends(get_current_user)
):
    from database import add_campaign
    
    content = {}
    
    if photo and photo.filename:
        # Save photo
        ext = Path(photo.filename).suffix or ".jpg"
        filename = f"{uuid.uuid4()}{ext}"
        filepath = UPLOADS_DIR / filename
        
        async with aiofiles.open(filepath, 'wb') as f:
            await f.write(await photo.read())
        
        content["photo_path"] = str(filepath)
        content["caption"] = text
    elif text:
        content["text"] = text
    else:
        raise HTTPException(status_code=400, detail="Message text or photo required")
    
    # Parse scheduled time
    schedule_dt = None
    if scheduled_for and scheduled_for.strip():
        schedule_dt = config.parse_scheduled_time(scheduled_for)
    
    try:
        campaign_id = await asyncio.wait_for(
            add_campaign("broadcast", content, schedule_dt),
            timeout=DB_OPERATION_TIMEOUT
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Database operation timed out. The broadcast may still be queued."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create broadcast: {str(e)}")
    
    return RedirectResponse(
        url=f"/broadcast?created={campaign_id}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# === Raffle ===

@app.get("/raffle", response_class=HTMLResponse)
async def raffle_page(request: Request, user: str = Depends(get_current_user), created: str = None):
    from database import get_participants_count, get_recent_raffles_with_winners
    
    participants = await get_participants_count()
    recent_raffles = await get_recent_raffles_with_winners(limit=5)
    
    return templates.TemplateResponse("raffle/index.html", {
        "request": request, "user": user, "title": "Розыгрыш",
        "participants": participants, "recent_raffles": recent_raffles,
        "created": created
    })


@app.post("/raffle/create")
async def create_raffle(
    request: Request,
    prize_name: str = Form(...),
    winner_count: int = Form(...),
    win_text: str = Form(None),
    win_photo: UploadFile = File(None),
    lose_text: str = Form(None),
    lose_photo: UploadFile = File(None),
    scheduled_for: str = Form(None),
    user: str = Depends(get_current_user)
):
    from database import add_campaign, get_participants_count
    
    participants = await get_participants_count()
    if winner_count < 1 or winner_count > participants:
        raise HTTPException(status_code=400, detail=f"Winner count must be 1-{participants}")
    
    # Build win message
    win_msg = {}
    if win_photo and win_photo.filename:
        ext = Path(win_photo.filename).suffix or ".jpg"
        filename = f"win_{uuid.uuid4()}{ext}"
        filepath = UPLOADS_DIR / filename
        async with aiofiles.open(filepath, 'wb') as f:
            await f.write(await win_photo.read())
        win_msg["photo_path"] = str(filepath)
        win_msg["caption"] = win_text
    elif win_text:
        win_msg["text"] = win_text
    else:
        win_msg["text"] = f"🎉 Поздравляем! Вы выиграли: {prize_name}!"
    
    # Build lose message
    lose_msg = {}
    if lose_photo and lose_photo.filename:
        ext = Path(lose_photo.filename).suffix or ".jpg"
        filename = f"lose_{uuid.uuid4()}{ext}"
        filepath = UPLOADS_DIR / filename
        async with aiofiles.open(filepath, 'wb') as f:
            await f.write(await lose_photo.read())
        lose_msg["photo_path"] = str(filepath)
        lose_msg["caption"] = lose_text
    elif lose_text:
        lose_msg["text"] = lose_text
    else:
        lose_msg["text"] = "К сожалению, в этот раз удача не на вашей стороне. Следите за новыми розыгрышами!"
    
    content = {
        "prize": prize_name,
        "prize_name": prize_name,
        "count": winner_count,
        "win_msg": win_msg,
        "lose_msg": lose_msg
    }
    
    # Parse scheduled time
    schedule_dt = None
    if scheduled_for and scheduled_for.strip():
        schedule_dt = config.parse_scheduled_time(scheduled_for)
    
    try:
        campaign_id = await asyncio.wait_for(
            add_campaign("raffle", content, schedule_dt),
            timeout=DB_OPERATION_TIMEOUT
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Database operation timed out. The raffle may still be queued."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create raffle: {str(e)}")
    
    return RedirectResponse(
        url=f"/raffle?created={campaign_id}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# === Campaigns list ===

@app.get("/campaigns", response_class=HTMLResponse)
async def campaigns_list(request: Request, user: str = Depends(get_current_user)):
    from database import get_recent_campaigns
    
    campaigns = await get_recent_campaigns(50)
    
    return templates.TemplateResponse("campaigns/list.html", {
        "request": request, "user": user, "title": "Кампании",
        "campaigns": campaigns
    })


# === Startup/Shutdown ===

@app.on_event("startup")
async def startup_event():
    """Initialize database connection pool"""
    logger.info("🚀 Admin panel starting...")
    start_time = time.time()
    
    try:
        from database import init_db
        await asyncio.wait_for(init_db(), timeout=30.0)
        duration = time.time() - start_time
        logger.info(f"✅ Database initialized in {duration:.2f}s")
    except asyncio.TimeoutError:
        logger.error("❌ Database initialization timed out after 30s")
        raise
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        raise
    
    logger.info("✅ Admin panel ready to accept requests")


@app.on_event("shutdown")
async def shutdown_event():
    """Close database connections"""
    logger.info("🛑 Admin panel shutting down...")
    from database import close_db
    await close_db()
    logger.info("✅ Admin panel shutdown complete")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
