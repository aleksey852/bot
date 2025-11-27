"""
User handlers: start, profile, receipts list, FAQ, support
Combined from common.py + info.py
"""
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
import math

from database import get_user, get_user_with_stats, get_user_receipts, update_username, get_user_wins
from utils.states import Registration
from utils.config_manager import config_manager
from keyboards import (
    get_main_keyboard, get_cancel_keyboard, get_receipts_pagination_keyboard,
    get_faq_keyboard, get_faq_back_keyboard, get_support_keyboard
)
import config

router = Router()
RECEIPTS_PER_PAGE = 10


# === Core Navigation ===

@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    user = await get_user_with_stats(message.from_user.id)
    count = user['valid_receipts'] if user else 0
    await message.answer(
        f"Выберите действие 👇\nВаших чеков: {count}",
        reply_markup=get_main_keyboard(config.is_admin(message.from_user.id))
    )


@router.message(F.text == "🏠 В меню")
async def go_to_menu(message: Message, state: FSMContext):
    await cancel_handler(message, state)


@router.message(CommandStart())
async def command_start(message: Message, state: FSMContext):
    user = await get_user_with_stats(message.from_user.id)
    
    if user:
        if message.from_user.username != user.get('username'):
            await update_username(message.from_user.id, message.from_user.username or "")
        
        days = config.days_until_end()
        days_text = f"\nДо конца акции: {days} дн." if days > 0 else ""
        
        # Use dynamic message from config_manager
        welcome_msg = config_manager.get_message(
            'welcome_back',
            "С возвращением, {name}! 👋\n\nВаших чеков: {count}{days_text}\n\nВыберите действие 👇"
        ).format(name=user['full_name'], count=user['valid_receipts'], days_text=days_text)
        
        await message.answer(welcome_msg, reply_markup=get_main_keyboard(config.is_admin(message.from_user.id)))
    else:
        promo_name = config_manager.get_setting('PROMO_NAME', config.PROMO_NAME)
        prizes = config_manager.get_setting('PROMO_PRIZES', config.PROMO_PRIZES)
        
        welcome_new_msg = config_manager.get_message(
            'welcome_new',
            "🎉 Добро пожаловать в {promo_name}!\n\nПризы: {prizes}\n\nДля участия введите ваше имя:"
        ).format(promo_name=promo_name, prizes=prizes)
        
        await message.answer(welcome_new_msg, reply_markup=get_cancel_keyboard())
        await state.set_state(Registration.name)


# === Profile & Receipts ===

@router.message(F.text == "👤 Мой профиль")
async def show_profile(message: Message):
    user = await get_user_with_stats(message.from_user.id)
    if not user:
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return
    
    if message.from_user.username != user.get('username'):
        await update_username(message.from_user.id, message.from_user.username or "")
    
    wins = await get_user_wins(user['id'])
    wins_text = f"\n\n🏆 Выигрыши: {len(wins)}" if wins else ""
    for w in wins[:3]:
        wins_text += f"\n• {w['prize_name']}"
    
    days = config.days_until_end()
    days_text = f"\n\nДо конца акции: {days} дн." if days > 0 else ""
    
    await message.answer(
        f"👤 Ваш профиль\n\n"
        f"Имя: {user['full_name']}\nТелефон: {user['phone']}\n\n"
        f"📊 Чеков загружено: {user['total_receipts']}\n"
        f"Чеков принято: {user['valid_receipts']}{wins_text}{days_text}"
    )


@router.message(Command("help"))
async def command_help(message: Message):
    await message.answer(
        "🤖 Что умеет бот:\n\n"
        "🧾 Загрузить чек — отправьте QR-код\n"
        "👤 Мой профиль — ваша статистика\n"
        "📋 Мои чеки — история загрузок\n"
        "ℹ️ FAQ — частые вопросы\n"
        "🆘 Поддержка — связь с нами\n\n"
        "Команды: /start /help /status /cancel",
        reply_markup=get_main_keyboard(config.is_admin(message.from_user.id))
    )


@router.message(Command("status"))
@router.message(Command("stats"))
async def command_status(message: Message):
    user = await get_user_with_stats(message.from_user.id)
    if not user:
        await message.answer("Сначала /start")
        return
    await message.answer(f"📊 {user['full_name']}\n\nЧеков: {user['valid_receipts']}\nДо конца: {config.days_until_end()} дн.")


@router.message(F.text == "📋 Мои чеки")
async def show_receipts(message: Message):
    user = await get_user_with_stats(message.from_user.id)
    if not user:
        await message.answer("Вы не зарегистрированы. /start")
        return
    
    total = user['total_receipts']
    if total == 0:
        await message.answer("📋 У вас пока нет чеков\n\nНажмите «🧾 Загрузить чек»")
        return
    
    receipts = await get_user_receipts(user['id'], limit=RECEIPTS_PER_PAGE, offset=0)
    total_pages = math.ceil(total / RECEIPTS_PER_PAGE)
    
    text = _format_receipts(receipts, 1, total)
    kb = get_receipts_pagination_keyboard(1, total_pages) if total_pages > 1 else None
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("receipts_page_"))
async def receipts_pagination(callback: CallbackQuery):
    page = int(callback.data.split("_")[-1])
    user = await get_user_with_stats(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка")
        return
    
    offset = (page - 1) * RECEIPTS_PER_PAGE
    receipts = await get_user_receipts(user['id'], limit=RECEIPTS_PER_PAGE, offset=offset)
    total_pages = math.ceil(user['total_receipts'] / RECEIPTS_PER_PAGE)
    
    await callback.message.edit_text(
        _format_receipts(receipts, page, user['total_receipts']),
        reply_markup=get_receipts_pagination_keyboard(page, total_pages)
    )
    await callback.answer()


@router.callback_query(F.data == "receipts_current")
async def receipts_current_page(callback: CallbackQuery):
    await callback.answer()


def _format_receipts(receipts: list, page: int, total: int) -> str:
    lines = [f"📋 Ваши чеки ({total})\n"]
    for r in receipts:
        status = "✅" if r['status'] == 'valid' else "❌"
        date = str(r['created_at'])[:10] if r.get('created_at') else ""
        sum_text = f" • {r['total_sum'] // 100}₽" if r.get('total_sum') else ""
        product = f"\n   └ {r['product_name'][:30]}" if r.get('product_name') else ""
        lines.append(f"\n{status} {date}{sum_text}{product}")
    return "".join(lines)


# === FAQ ===

@router.message(F.text == "ℹ️ FAQ")
async def show_faq(message: Message):
    await message.answer("❓ Частые вопросы\n\nВыберите тему:", reply_markup=get_faq_keyboard())


@router.callback_query(F.data == "faq_how")
async def faq_how(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎯 Как участвовать?\n\n1. Купите акционные товары\n2. Сохраните чек\n"
        "3. Сфотографируйте QR-код\n4. Отправьте фото в бот\n5. Ждите розыгрыша!\n\n"
        "💡 Чем больше чеков — тем выше шансы",
        reply_markup=get_faq_back_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "faq_limit")
async def faq_limit(callback: CallbackQuery):
    await callback.message.edit_text(
        "🧾 Сколько чеков можно загрузить?\n\nОграничений нет!\n\n"
        "Важно:\n• Каждый чек — один раз\n• Нужны акционные товары\n• Чек не старше 30 дней",
        reply_markup=get_faq_back_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "faq_win")
async def faq_win(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏆 Как узнать о выигрыше?\n\nМы пришлём сообщение в этот бот!\n\n"
        "Убедитесь, что уведомления включены",
        reply_markup=get_faq_back_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "faq_reject")
async def faq_reject(callback: CallbackQuery):
    await callback.message.edit_text(
        "❌ Почему чек не принят?\n\n"
        "• QR-код нечёткий\n• Нет акционных товаров\n• Чек старше 30 дней\n• Уже загружен\n\n"
        "💡 Свежий чек? Подождите 5-10 минут",
        reply_markup=get_faq_back_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "faq_dates")
async def faq_dates(callback: CallbackQuery):
    days = config.days_until_end()
    status = f"Осталось: {days} дн." if days > 0 else "Акция завершена"
    await callback.message.edit_text(
        f"📅 Сроки акции\n\nНачало: {config.PROMO_START_DATE}\n"
        f"Окончание: {config.PROMO_END_DATE}\n\n{status}",
        reply_markup=get_faq_back_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "faq_prizes")
async def faq_prizes(callback: CallbackQuery):
    await callback.message.edit_text(
        f"🎁 Призы\n\n{config.PROMO_PRIZES}\n\nБольше чеков = выше шансы!",
        reply_markup=get_faq_back_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "faq_back")
async def faq_back(callback: CallbackQuery):
    await callback.message.edit_text("❓ Частые вопросы\n\nВыберите тему:", reply_markup=get_faq_keyboard())
    await callback.answer()


# === Support ===

@router.message(F.text == "🆘 Поддержка")
async def show_support(message: Message):
    await message.answer("🆘 Нужна помощь?\n\nНапишите нам!", reply_markup=get_support_keyboard())
