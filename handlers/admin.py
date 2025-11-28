"""Admin handlers: stats, broadcast, raffle, winners export"""
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
import orjson
import logging

from utils.states import AdminBroadcast, AdminRaffle, AdminManualReceipt
from keyboards import (
    get_main_keyboard, get_cancel_keyboard, get_confirm_keyboard,
    get_schedule_keyboard, get_admin_broadcast_preview_keyboard
)
from database import (
    add_campaign, get_stats, get_participants_count, get_recent_raffles_with_winners,
    get_all_winners_for_export, add_receipt, get_user_by_id, get_user_by_username, get_user_by_phone,
    get_total_users_count
)
    get_total_users_count
)
from utils.config_manager import config_manager
import config

logger = logging.getLogger(__name__)
router = Router()


def admin_only(func):
    async def wrapper(message: Message, *args, **kwargs):
        if not config.is_admin(message.from_user.id):
            return
        return await func(message, *args, **kwargs)
    return wrapper


# === Statistics ===

@router.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    if not config.is_admin(message.from_user.id):
        return
    
    stats = await get_stats()
    participants = await get_participants_count()
    conversion = (participants / stats['total_users'] * 100) if stats['total_users'] else 0
    
    stats_msg = config_manager.get_message(
        'stats_msg',
        "📊 Статистика\n\n👥 Пользователи: {users}\n   сегодня: +{users_today}\n\n🧾 Чеки: {receipts}\n   принято: {valid}\n   сегодня: +{receipts_today}\n\n🎯 Участников: {participants}\n📈 Конверсия: {conversion:.1f}%\n\n🏆 Победителей: {winners}"
    ).format(
        users=stats['total_users'],
        users_today=stats['users_today'],
        receipts=stats['total_receipts'],
        valid=stats['valid_receipts'],
        receipts_today=stats['receipts_today'],
        participants=participants,
        conversion=conversion,
        winners=stats['total_winners']
    )
    
    await message.answer(stats_msg)


# === Broadcast ===

@router.message(F.text == "📢 Рассылка")
async def start_broadcast(message: Message, state: FSMContext):
    if not config.is_admin(message.from_user.id):
        return
    
    total = await get_total_users_count()
    await message.answer(f"📢 Рассылка\n\nПолучателей: {total}\n\nОтправьте сообщение:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminBroadcast.content)


@router.message(AdminBroadcast.content)
async def process_broadcast_content(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    content = {}
    if message.photo:
        content["photo"] = message.photo[-1].file_id
        content["caption"] = message.caption
    elif message.text:
        content["text"] = message.text
    else:
        await message.answer("Отправьте текст или фото")
        return
    
    await state.update_data(content=content)
    await message.answer("👀 Предпросмотр:")
    
    if "photo" in content:
        await bot.send_photo(message.from_user.id, content["photo"], caption=content.get("caption"))
    else:
        await message.answer(content.get("text", ""))
    
    await message.answer("Всё верно?", reply_markup=get_admin_broadcast_preview_keyboard())
    await state.set_state(AdminBroadcast.preview)


@router.callback_query(AdminBroadcast.preview, F.data == "broadcast_edit")
async def broadcast_edit(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Отправьте новое сообщение:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminBroadcast.content)
    await callback.answer()


@router.callback_query(AdminBroadcast.preview, F.data == "broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
    await callback.answer()


@router.callback_query(AdminBroadcast.preview, F.data == "broadcast_send")
async def broadcast_send(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("⏰ Когда?\n\n2025-01-15 18:00\n\nИли «Сейчас»", reply_markup=get_schedule_keyboard())
    await state.set_state(AdminBroadcast.schedule)
    await callback.answer()


@router.message(AdminBroadcast.schedule)
async def process_broadcast_schedule(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    data = await state.get_data()
    scheduled_for = None
    
    if message.text != "🚀 Сейчас":
        dt = config.parse_scheduled_time(message.text)
        if not dt or dt < config.get_now():
            await message.answer("Неверная дата. Формат: 2025-01-15 18:00")
            return
        scheduled_for = message.text
    
    campaign_id = await add_campaign("broadcast", data["content"], scheduled_for)
    msg = f"✅ Рассылка #{campaign_id} " + (f"запланирована на {scheduled_for}" if scheduled_for else "начнётся через минуту")
    await message.answer(msg, reply_markup=get_main_keyboard(is_admin=True))
    await state.clear()


# === Raffle ===

@router.message(F.text == "🎁 Розыгрыш")
async def start_raffle(message: Message, state: FSMContext):
    if not config.is_admin(message.from_user.id):
        return
    
    participants = await get_participants_count()
    if participants == 0:
        await message.answer("Нет участников", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    await message.answer(f"🎁 Розыгрыш\n\nУчастников: {participants}\n\nНазвание приза:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminRaffle.prize_name)


@router.message(AdminRaffle.prize_name)
async def raffle_prize(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    await state.update_data(prize=message.text)
    await message.answer("Сколько победителей?")
    await state.set_state(AdminRaffle.winner_count)


@router.message(AdminRaffle.winner_count)
async def raffle_count(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    if not message.text or not message.text.isdigit():
        await message.answer("Введите число")
        return
    
    count = int(message.text)
    participants = await get_participants_count()
    
    if count < 1 or count > participants:
        await message.answer(f"От 1 до {participants}")
        return
    
    await state.update_data(count=count)
    await message.answer("📨 Сообщение для ПОБЕДИТЕЛЕЙ:")
    await state.set_state(AdminRaffle.winner_message)


@router.message(AdminRaffle.winner_message)
async def raffle_win_msg(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    content = {"photo": message.photo[-1].file_id, "caption": message.caption} if message.photo else {"text": message.text}
    await state.update_data(win_msg=content)
    await message.answer("📨 Сообщение для ОСТАЛЬНЫХ:")
    await state.set_state(AdminRaffle.loser_message)


@router.message(AdminRaffle.loser_message)
async def raffle_lose_msg(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    content = {"photo": message.photo[-1].file_id, "caption": message.caption} if message.photo else {"text": message.text}
    await state.update_data(lose_msg=content)
    await message.answer("⏰ Когда?\n\n2025-01-15 18:00 или «Сейчас»", reply_markup=get_schedule_keyboard())
    await state.set_state(AdminRaffle.schedule)


@router.message(AdminRaffle.schedule)
async def raffle_schedule(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    scheduled_for = None
    if message.text != "🚀 Сейчас":
        dt = config.parse_scheduled_time(message.text)
        if not dt or dt < config.get_now():
            await message.answer("Неверная дата")
            return
        scheduled_for = message.text
    
    await state.update_data(scheduled_for=scheduled_for)
    data = await state.get_data()
    participants = await get_participants_count()
    
    await message.answer(
        f"⚠️ Подтверждение\n\nПриз: {data['prize']}\nПобедителей: {data['count']}\n"
        f"Участников: {participants}\nВремя: {scheduled_for or 'Сейчас'}\n\nПодтвердить?",
        reply_markup=get_confirm_keyboard()
    )
    await state.set_state(AdminRaffle.confirm)


@router.message(AdminRaffle.confirm)
async def raffle_confirm(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    if message.text != "✅ Подтвердить":
        await message.answer("Нажмите «Подтвердить» или «Отмена»")
        return
    
    data = await state.get_data()
    campaign_id = await add_campaign("raffle", {
        "prize": data["prize"], "count": data["count"],
        "win_msg": data["win_msg"], "lose_msg": data["lose_msg"]
    }, data.get("scheduled_for"))
    
    msg = f"✅ Розыгрыш #{campaign_id} " + (f"запланирован на {data.get('scheduled_for')}" if data.get("scheduled_for") else "начнётся через минуту")
    await message.answer(msg, reply_markup=get_main_keyboard(is_admin=True))
    await state.clear()


# === Winners ===

@router.message(F.text == "🏆 Победители")
async def show_winners(message: Message):
    if not config.is_admin(message.from_user.id):
        return
    
    campaigns = await get_recent_raffles_with_winners(limit=5)
    if not campaigns:
        await message.answer("Розыгрышей ещё не было")
        return
    
    text = ["🏆 Последние розыгрыши\n"]
    for c in campaigns:
        content = c['content'] if isinstance(c['content'], dict) else orjson.loads(c['content'])
        date_str = str(c['completed_at'])[:16] if c.get('completed_at') else "N/A"
        text.append(f"\n🎁 {content.get('prize', 'Приз')}\n📅 {date_str}\n👥 {len(c['winners'])}\n")
        for w in c['winners'][:10]:
            notified = "✓" if w['notified'] else "..."
            text.append(f"  {notified} {w.get('full_name', 'Unknown')}\n")
    
    await message.answer("".join(text))


@router.message(F.text == "📥 Экспорт победителей")
async def export_winners(message: Message):
    if not config.is_admin(message.from_user.id):
        return
    
    winners = await get_all_winners_for_export()
    if not winners:
        await message.answer("Победителей нет")
        return
    
    csv = ["Имя,Телефон,Username,Приз,Дата,Уведомлён"]
    for w in winners:
        csv.append(f"{w.get('full_name', '').replace(',', ' ')},{w.get('phone', '')},"
                   f"@{w.get('username', '')},{w.get('prize_name', '').replace(',', ' ')},"
                   f"{str(w.get('created_at', ''))[:19]},{'Да' if w.get('notified') else 'Нет'}")
    
    await message.answer_document(
        BufferedInputFile("\n".join(csv).encode('utf-8-sig'), filename="winners.csv"),
        caption=f"📥 {len(winners)} победителей"
    )


# === Manual Receipt ===

@router.message(F.text == "➕ Ручное добавление")
async def start_manual_receipt(message: Message, state: FSMContext):
    if not config.is_admin(message.from_user.id):
        return
    
    await message.answer("➕ Введите пользователя:\n• ID\n• @username\n• +7...", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminManualReceipt.user_id)


@router.message(AdminManualReceipt.user_id)
async def process_manual_user(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    text = message.text.strip()
    user = None
    
    if text.startswith('@'):
        user = await get_user_by_username(text)
    elif '+' in text or (text.isdigit() and len(text) > 7):
        user = await get_user_by_phone(text)
    elif text.isdigit():
        user = await get_user_by_id(int(text))
        if not user:
            user = await get_user_by_phone(text)
    
    if not user:
        await message.answer("❌ Пользователь не найден", reply_markup=get_cancel_keyboard())
        return
    
    await state.update_data(user_id=user['id'], user_name=user['full_name'])
    await message.answer(
        f"⚠️ Добавить чек для {user['full_name']} (ID: {user['id']})?",
        reply_markup=get_confirm_keyboard()
    )
    await state.set_state(AdminManualReceipt.confirm)


@router.message(AdminManualReceipt.confirm)
async def confirm_manual_receipt(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_main_keyboard(is_admin=True))
        return
    
    if message.text != "✅ Подтвердить":
        await message.answer("«Подтвердить» или «Отмена»")
        return
    
    import time, uuid
    data = await state.get_data()
    ts = int(time.time())
    uid = str(uuid.uuid4())[:8]
    
    receipt_id = await add_receipt(
        user_id=data['user_id'],
        status="valid",
        data={"manual": True, "admin_id": message.from_user.id},
        fiscal_drive_number="MANUAL",
        fiscal_document_number=f"MANUAL_{ts}_{uid}",
        fiscal_sign=f"MANUAL_{data['user_id']}_{ts}",
        total_sum=0,
        raw_qr="manual",
        product_name="Ручное добавление"
    )
    
    await message.answer(f"✅ Чек #{receipt_id} добавлен!", reply_markup=get_main_keyboard(is_admin=True))
    await state.clear()
