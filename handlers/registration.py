"""User registration handlers"""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
import re

from utils.states import Registration
from keyboards import get_contact_keyboard, get_main_keyboard, get_start_keyboard
from database import add_user
from utils.config_manager import config_manager
import config

router = Router()
PHONE_PATTERN = re.compile(r'^\+?[0-9]{10,15}$')


@router.message(Registration.name)
async def process_name(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        reg_cancel_msg = config_manager.get_message('reg_cancel', "Хорошо! Возвращайтесь 👋")
        await message.answer(reg_cancel_msg, reply_markup=get_start_keyboard())
        return
    
    if not message.text or len(message.text) < 2 or len(message.text) > 100:
        reg_name_error_msg = config_manager.get_message('reg_name_error', "Введите имя (2-100 символов)")
        await message.answer(reg_name_error_msg)
        return
    
    await state.update_data(name=message.text.strip())
    reg_phone_prompt = config_manager.get_message(
        'reg_phone_prompt',
        "Отлично, {name}! 👋\n\nОтправьте номер телефона:"
    ).format(name=message.text)
    
    await message.answer(
        reg_phone_prompt,
        reply_markup=get_contact_keyboard()
    )
    await state.set_state(Registration.phone)


@router.message(Registration.phone)
async def process_phone(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        reg_cancel_msg = config_manager.get_message('reg_cancel', "Хорошо! Возвращайтесь 👋")
        await message.answer(reg_cancel_msg, reply_markup=get_start_keyboard())
        return
    
    phone = None
    if message.contact:
        phone = message.contact.phone_number
    elif message.text:
        clean = re.sub(r'\D', '', message.text)
        if not PHONE_PATTERN.match(clean) and not PHONE_PATTERN.match(message.text.strip()):
            reg_phone_error_msg = config_manager.get_message('reg_phone_error', "❌ Неверный формат. Введите как +79991234567")
            await message.answer(reg_phone_error_msg)
            return
        phone = message.text.strip()
    else:
        reg_phone_request_msg = config_manager.get_message('reg_phone_request', "Отправьте номер телефона")
        await message.answer(reg_phone_request_msg)
        return
    
    data = await state.get_data()
    await add_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=data.get("name", "Пользователь"),
        phone=phone
    )
    
    await state.clear()
    reg_success_msg = config_manager.get_message(
        'reg_success',
        "✅ Регистрация завершена!\n\n1. Купите акционные товары\n2. Сфотографируйте QR-код\n3. Загрузите сюда\n\nАкция: {start} — {end}\n\n👇 Загрузите первый чек"
    ).format(start=config.PROMO_START_DATE, end=config.PROMO_END_DATE)
    
    await message.answer(
        reg_success_msg,
        reply_markup=get_main_keyboard(config.is_admin(message.from_user.id))
    )
