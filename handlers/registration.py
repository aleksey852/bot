"""User registration handlers"""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
import re

from utils.states import Registration
from keyboards import get_contact_keyboard, get_main_keyboard, get_start_keyboard
from database import add_user
import config

router = Router()
PHONE_PATTERN = re.compile(r'^\+?[0-9]{10,15}$')


@router.message(Registration.name)
async def process_name(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Хорошо! Возвращайтесь 👋", reply_markup=get_start_keyboard())
        return
    
    if not message.text or len(message.text) < 2 or len(message.text) > 100:
        await message.answer("Введите имя (2-100 символов)")
        return
    
    await state.update_data(name=message.text.strip())
    await message.answer(
        f"Отлично, {message.text}! 👋\n\nОтправьте номер телефона:",
        reply_markup=get_contact_keyboard()
    )
    await state.set_state(Registration.phone)


@router.message(Registration.phone)
async def process_phone(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Хорошо! Возвращайтесь 👋", reply_markup=get_start_keyboard())
        return
    
    phone = None
    if message.contact:
        phone = message.contact.phone_number
    elif message.text:
        clean = re.sub(r'\D', '', message.text)
        if not PHONE_PATTERN.match(clean) and not PHONE_PATTERN.match(message.text.strip()):
            await message.answer("❌ Неверный формат. Введите как +79991234567")
            return
        phone = message.text.strip()
    else:
        await message.answer("Отправьте номер телефона")
        return
    
    data = await state.get_data()
    await add_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=data.get("name", "Пользователь"),
        phone=phone
    )
    
    await state.clear()
    await message.answer(
        f"✅ Регистрация завершена!\n\n"
        f"1. Купите акционные товары\n2. Сфотографируйте QR-код\n3. Загрузите сюда\n\n"
        f"Акция: {config.PROMO_START_DATE} — {config.PROMO_END_DATE}\n\n👇 Загрузите первый чек",
        reply_markup=get_main_keyboard(config.is_admin(message.from_user.id))
    )
